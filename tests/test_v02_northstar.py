import io
import os
import tempfile
import zipfile
import pytest
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFilter

from backend.app.main import app
from backend.app.core.database import EduMarkDatabase
from backend.app.models.schemas import (
    ExamRubric,
    ExtractedLine,
    ExtractedQuestion,
    LineType,
    QuestionRubric,
    RubricCriterion,
    SubmissionGradingRecord,
    SubmissionStatus,
)
from backend.app.services.preprocessor import (
    assess_capture_quality,
    detect_and_warp_document,
    remove_shadows,
)
from backend.app.services.agent1_ocr import (
    OCRApiError,
    VisionOcrAgent,
    clean_and_normalize_math_latex,
    parse_and_validate_ocr_json,
)
from backend.app.services.agent2_grader import reasoning_agent
from backend.app.services.math_verifier import verify_question_math

client = TestClient(app)


def _jpeg_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _sharp_handwriting_image(width: int = 500, height: int = 350) -> Image.Image:
    img = Image.new("RGB", (width, height), color=(245, 245, 245))
    draw = ImageDraw.Draw(img)
    for i in range(6):
        y = 30 + i * 45
        draw.line([(30, y), (width - 30, y)], fill=(20, 20, 40), width=3)
        draw.rectangle([(40 + i * 20, y - 10), (75 + i * 20, y + 10)], outline=(10, 10, 80), width=2)
    return img


def test_v2_01_assess_capture_quality():
    """V2-01: Pre-capture quality check phát hiện đúng ảnh tối, ảnh mờ, ảnh đạt chuẩn."""
    sharp_bytes = _jpeg_bytes(_sharp_handwriting_image())
    rep_ok = assess_capture_quality(sharp_bytes)
    assert rep_ok.is_acceptable is True
    assert rep_ok.is_too_dark is False
    assert rep_ok.is_blurry is False

    dark_img = Image.new("RGB", (300, 300), color=(20, 20, 20))
    rep_dark = assess_capture_quality(_jpeg_bytes(dark_img))
    assert rep_dark.is_too_dark is True
    assert rep_dark.is_acceptable is False
    assert len(rep_dark.warnings) >= 1

    blurry_img = _sharp_handwriting_image().filter(ImageFilter.GaussianBlur(radius=12))
    rep_blur = assess_capture_quality(_jpeg_bytes(blurry_img))
    assert rep_blur.is_blurry is True
    assert rep_blur.is_acceptable is False


def test_v2_02_perspective_warp_and_shadow_removal():
    """V2-02: Duỗi phẳng tờ giấy trên nền tối và khử bóng đổ ánh sáng không đều."""
    # Tạo ảnh nền bàn tối với tờ giấy trắng nghiêng nhẹ ở giữa
    canvas = Image.new("RGB", (600, 800), color=(25, 25, 30))
    draw = ImageDraw.Draw(canvas)
    draw.polygon([(110, 90), (490, 110), (520, 710), (80, 690)], fill=(245, 245, 245))
    draw.line([(160, 200), (430, 200)], fill=(30, 30, 120), width=4)

    warped, did_warp = detect_and_warp_document(canvas)
    assert warped.size[0] > 100 and warped.size[1] > 100
    assert isinstance(did_warp, bool)

    # Tạo ảnh có gradient bóng đổ mạnh từ trái sang phải
    arr = np.zeros((300, 400, 3), dtype=np.uint8)
    for x in range(400):
        val = int(90 + (x / 400.0) * 155)
        arr[:, x, :] = val
    shadow_img = Image.fromarray(arr)
    cleaned, removed = remove_shadows(shadow_img)
    assert cleaned.size == shadow_img.size
    assert removed is True


def test_v2_03_no_overfit_regex_and_parse_strikethrough_header():
    """V2-03: Không còn regex tự ý sửa biểu thức hợp lệ; parse đúng strikethrough & tên HS."""
    expr = r"\frac{x+4}{4} = \frac{1}{g}"
    cleaned = clean_and_normalize_math_latex(expr)
    assert r"\frac{x+4}{4}" in cleaned
    assert r"\frac{7}{9}" not in cleaned

    raw_json = """{
      "student_name_detected": "Nguyễn Minh Quân",
      "student_class_detected": "9A2",
      "questions": [{
        "question_id": "Q1",
        "lines": [
          {"line_no": 1, "page_no": 1, "raw_text": "2x = 8", "latex": "2x = 8", "line_type": "scratch", "is_strikethrough": true, "confidence": 0.9},
          {"line_no": 2, "page_no": 2, "raw_text": "2x = 6 <=> x = 3", "latex": "2x = 6 \\\\Leftrightarrow x = 3", "line_type": "equation", "confidence": 0.96}
        ]
      }]
    }"""
    qs = parse_and_validate_ocr_json(raw_json)
    assert len(qs) == 1
    assert qs[0].student_name_detected == "Nguyễn Minh Quân"
    assert qs[0].lines[0].is_strikethrough is True
    assert qs[0].lines[0].line_type == LineType.SCRATCH
    assert qs[0].lines[1].page_no == 2


def test_v2_04_live_ocr_no_silent_mock_fallback(monkeypatch):
    """V2-04: Khi đã cài Live API Key mà API lỗi -> phải raise OCRApiError, CẤM âm thầm trả mock."""
    agent = VisionOcrAgent(api_key="live_fake_gemini_key")
    agent.openai_api_key = ""

    def _fail(*args, **kwargs):
        raise OCRApiError("Simulated Gemini outage", error_code="E008")

    monkeypatch.setattr(agent, "_call_gemini_vision_with_retry", _fail)
    with pytest.raises(OCRApiError) as exc_info:
        agent.extract_latex(b"fake_image")
    assert exc_info.value.error_code == "E008"


def test_v2_05_sympy_math_verifier():
    """V2-05: SymPy xác nhận bước biến đổi đúng và phát hiện chính xác bước tính sai."""
    q_good = ExtractedQuestion(
        question_id="Q1",
        lines=[
            ExtractedLine(line_no=1, latex=r"\frac{x}{x-2} + 1 = \frac{4}{x-2}", line_type=LineType.EQUATION),
            ExtractedLine(line_no=2, latex=r"x + (x - 2) = 4", line_type=LineType.EQUATION),
            ExtractedLine(line_no=3, latex=r"2x - 2 = 4 \Leftrightarrow 2x = 6", line_type=LineType.EQUATION),
            ExtractedLine(line_no=4, latex=r"x = 3", line_type=LineType.EQUATION),
        ],
    )
    mv_good = verify_question_math(q_good)
    assert mv_good.equations_checked >= 4
    assert mv_good.invalid_transitions == 0
    assert mv_good.valid_transitions >= 3
    assert "x = 3" in mv_good.detected_roots

    q_bad = ExtractedQuestion(
        question_id="Q1",
        lines=[
            ExtractedLine(line_no=1, latex=r"2x - 2 = 4", line_type=LineType.EQUATION),
            ExtractedLine(line_no=2, latex=r"2x = 6", line_type=LineType.EQUATION),
            ExtractedLine(line_no=3, latex=r"x = 4", line_type=LineType.EQUATION),
        ],
    )
    mv_bad = verify_question_math(q_bad)
    assert mv_bad.invalid_transitions >= 1
    assert len(mv_bad.issues) >= 1


def test_v2_06_grader_ignores_strikethrough_lines():
    """V2-06: Agent 2 bỏ qua các dòng học sinh đã gạch bỏ (scratch/strikethrough)."""
    rubric = QuestionRubric(
        question_id="Q1",
        max_score=2.0,
        criteria=[
            RubricCriterion(criterion_id="Q1_C1_DKXD", name="Điều kiện xác định", max_score=0.5, required_logic="x != 2"),
            RubricCriterion(criterion_id="Q1_C2_BIEN_DOI", name="Biến đổi", max_score=1.0, required_logic="2x = 6 <=> x = 3"),
            RubricCriterion(criterion_id="Q1_C3_KET_LUAN", name="Kết luận", max_score=0.5, required_logic="x = 3 thỏa mãn, S = {3}"),
        ],
    )
    q = ExtractedQuestion(
        question_id="Q1",
        confidence=0.96,
        lines=[
            ExtractedLine(line_no=1, raw_text="ĐKXĐ: x != 2", latex="x \\neq 2", line_type=LineType.CONDITION),
            # Dòng tính nhầm nhưng học sinh ĐÃ GẠCH BỎ -> phải bỏ qua!
            ExtractedLine(line_no=2, raw_text="2x = 99 => x = 50", latex="2x = 99", line_type=LineType.SCRATCH, is_strikethrough=True),
            ExtractedLine(line_no=3, raw_text="2x = 6 <=> x = 3", latex="2x = 6 \\Leftrightarrow x = 3", line_type=LineType.EQUATION),
            ExtractedLine(line_no=4, raw_text="x = 3 thỏa mãn ĐKXĐ, S = {3}", latex="S = \\{3\\}", line_type=LineType.CONCLUSION),
        ],
    )
    res = reasoning_agent.grade_question(q, rubric)
    assert res.earned_points == 2.0
    assert res.error_propagation_detected is False


def test_v2_07_sqlite_persistence_survives_reload():
    """V2-07: SQLite lưu bền vững exam, submission, batch job sau khi mở kết nối mới."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = os.path.join(tmpdir, "test_edumark.db")
        db1 = EduMarkDatabase(db_path=db_file)
        exam = ExamRubric(exam_id="TEST_PERSIST", title="Đề kiểm tra thử", total_points=10.0, questions=[])
        db1.save_exam(exam)

        sub = SubmissionGradingRecord(
            submission_id="sub_persist_1",
            exam_id="TEST_PERSIST",
            student_name="Lê Văn Bảo",
            status=SubmissionStatus.APPROVED,
            ai_total_score=8.5,
            final_score=9.0,
        )
        db1.save_submission(sub)
        db1.save_batch_job({"job_id": "batch_p1", "exam_id": "TEST_PERSIST", "status": "completed"})

        # Mở instance mới hoàn toàn từ cùng file SQLite
        db2 = EduMarkDatabase(db_path=db_file)
        assert db2.ping() is True
        assert db2.get_exam("TEST_PERSIST").title == "Đề kiểm tra thử"
        loaded_sub = db2.get_submission("sub_persist_1")
        assert loaded_sub is not None
        assert loaded_sub.final_score == 9.0
        assert db2.get_batch_job("batch_p1")["status"] == "completed"


def test_v2_08_new_endpoints_and_multipage_grade():
    """V2-08: Root /health, CRUD exams, quality-check, grade-multipage (2 trang), strict export."""
    assert client.get("/health").status_code == 200
    assert client.get("/health").json()["db_ok"] is True

    # Create -> Update -> Delete exam
    new_exam = {"exam_id": "TMP_EXAM_V2", "title": "Đề tạm", "total_points": 5.0, "questions": []}
    assert client.post("/api/v1/exams", json=new_exam).status_code == 200
    new_exam["title"] = "Đề tạm đã sửa"
    res_put = client.put("/api/v1/exams/TMP_EXAM_V2", json=new_exam)
    assert res_put.status_code == 200
    assert res_put.json()["title"] == "Đề tạm đã sửa"
    assert client.delete("/api/v1/exams/TMP_EXAM_V2").status_code == 200

    # Quality check
    img_bytes = _jpeg_bytes(_sharp_handwriting_image())
    res_q = client.post("/api/v1/submissions/quality-check", files={"file": ("p1.jpg", img_bytes, "image/jpeg")})
    assert res_q.status_code == 200
    assert res_q.json()["is_acceptable"] is True

    # Grade multipage (2 pages)
    res_mp = client.post(
        "/api/v1/submissions/grade-multipage",
        files=[
            ("files", ("page1.jpg", img_bytes, "image/jpeg")),
            ("files", ("page2.jpg", img_bytes, "image/jpeg")),
        ],
        data={"exam_id": "TOAN9_01", "student_name": "Phạm Thu Hà"},
    )
    assert res_mp.status_code == 200
    mp_data = res_mp.json()
    assert mp_data["page_count"] == 2
    assert mp_data["math_verification"] is not None

    # List submissions
    res_list = client.get("/api/v1/submissions?exam_id=TOAN9_01")
    assert res_list.status_code == 200
    assert any(s["submission_id"] == mp_data["submission_id"] for s in res_list.json())

    # Export cho exam không có bài -> 400 (không trộn bài của exam khác)
    res_empty_exp = client.get("/api/v1/exports/excel/EMPTY_EXAM_XYZ")
    assert res_empty_exp.status_code == 400


def test_v2_09_async_batch_mode():
    """V2-09: Batch upload với async_mode=true chạy nền và hoàn tất sạch."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hs1.jpg", _jpeg_bytes(_sharp_handwriting_image()))
        zf.writestr("hs2.jpg", _jpeg_bytes(_sharp_handwriting_image()))

    res = client.post(
        "/api/v1/submissions/batch",
        files={"file": ("async_test.zip", buf.getvalue(), "application/zip")},
        data={"exam_id": "TOAN9_01", "async_mode": "true"},
    )
    assert res.status_code == 200
    job_id = res.json()["job_id"]
    status_res = client.get(f"/api/v1/submissions/batch/{job_id}")
    assert status_res.status_code == 200
    assert status_res.json()["status"] == "completed"
    assert status_res.json()["completed_submissions"] == 2


def test_v2_10_mobile_pwa_routes():
    """V2-10: Mobile PWA routes (/app, manifest.json, sw.js) trả về 200 OK."""
    r_app = client.get("/app")
    assert r_app.status_code == 200
    assert "EduMark AI" in r_app.text

    r_man = client.get("/static/manifest.json")
    assert r_man.status_code == 200
    assert r_man.json()["start_url"] == "/app"

    r_sw = client.get("/static/sw.js")
    assert r_sw.status_code == 200


def test_v2_11_telemetry_metrics_populated():
    """V2-11: Mỗi lượt chấm phải trả về đầy đủ telemetry thời gian từng chặng, token và chi phí."""
    img_bytes = _jpeg_bytes(_sharp_handwriting_image())
    res = client.post(
        "/api/v1/submissions/grade",
        files={"file": ("tel_test.jpg", img_bytes, "image/jpeg")},
        data={"exam_id": "TOAN9_01", "student_name": "Đo Hiệu Năng"},
    )
    assert res.status_code == 200
    data = res.json()
    tel = data["telemetry"]
    assert tel["total_ms"] >= 0
    assert tel["preprocess_ms"] >= 0
    assert tel["ocr_ms"] >= 0
    assert tel["sympy_ms"] >= 0
    assert tel["grading_ms"] >= 0
    assert "total_tokens" in tel
    assert "estimated_cost_vnd" in tel

