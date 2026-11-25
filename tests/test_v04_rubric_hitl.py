import io
import openpyxl
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.app.core.database import db
from backend.app.main import app
from backend.app.models.schemas import (
    ExamRubric,
    ExtractedLine,
    ExtractedQuestion,
    LineType,
    QuestionRubric,
    RubricCriterion,
)
from backend.app.services.agent2_grader import (
    match_student_questions_to_rubric,
    reasoning_agent,
)

client = TestClient(app)


def _sample_img(text: str = "x = 2") -> bytes:
    img = Image.new("RGB", (400, 300), color=(250, 250, 250))
    ImageDraw.Draw(img).text((20, 40), text, fill=(10, 10, 10))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def test_rubric_generate_from_text_and_files_persists_in_sqlite():
    """Tạo Biểu điểm từ văn bản hoặc ảnh đề thi phải lưu thẳng vào SQLite và hiện trong /exams."""
    res_txt = client.post(
        "/api/v1/exams/generate-from-text",
        json={
            "title": "Kiểm tra Toán 7 - v04",
            "grade": 7,
            "total_points": 10.0,
            "exam_text": "Câu 2a: x - 3/4 = 2/5\nCâu 2b: 1/4 x - 3/8 = -2/3\nCâu 3: Tính tiền giảm giá 20%",
            "save_immediately": True,
        },
    )
    assert res_txt.status_code == 200
    rubric = res_txt.json()
    assert rubric["exam_id"].startswith("EXAM_")
    assert len(rubric["questions"]) == 3

    res_list = client.get("/api/v1/exams")
    assert res_list.status_code == 200
    assert any(e["exam_id"] == rubric["exam_id"] for e in res_list.json())

    res_file = client.post(
        "/api/v1/exams/generate-from-files",
        files=[("files", ("de_toan_7.jpg", _sample_img("De thi Toan 7"), "image/jpeg"))],
        data={"title": "Đề ảnh 7A", "grade": "7", "total_points": "10.0", "save_immediately": "true"},
    )
    assert res_file.status_code == 200
    assert res_file.json()["exam_id"].startswith("EXAM_")


def test_match_student_questions_to_rubric_shorthand_and_rollup():
    """Khớp câu viết tắt `a, b, c` vào `Câu 2a, Câu 2b, Câu 2c` và gom ý con vào câu cha."""
    rubric_qs = [
        QuestionRubric(question_id="Câu 2a", question_text="x - 3/4 = 2/5", max_score=1.0),
        QuestionRubric(question_id="Câu 2b", question_text="1/4 x - 3/8 = -2/3", max_score=1.0),
        QuestionRubric(question_id="Câu 2c", question_text="(1/2 - x)^2 - 24/25 = 1", max_score=1.5),
        QuestionRubric(question_id="Câu 3", question_text="Mua 2 áo giảm 20%", max_score=3.0),
        QuestionRubric(question_id="Câu 4", question_text="Hình học góc và song song", max_score=3.5),
    ]
    hs_classmate_qs = [
        ExtractedQuestion(question_id="a", lines=[ExtractedLine(line_no=1, raw_text="x - 3/4 = 2/5")]),
        ExtractedQuestion(question_id="b", lines=[ExtractedLine(line_no=1, raw_text="1/4 x - 3/8 = -2/3")]),
        ExtractedQuestion(question_id="c", lines=[ExtractedLine(line_no=1, raw_text="(1/2 - x)^2 = 49/25")]),
    ]
    matched = match_student_questions_to_rubric(hs_classmate_qs, rubric_qs)
    assert set(matched.keys()) == {"Câu 2a", "Câu 2b", "Câu 2c"}

    # Rollup ý con 4a, 4b vào Câu 4
    rollup = match_student_questions_to_rubric(
        [
            ExtractedQuestion(question_id="4a", lines=[ExtractedLine(line_no=1, raw_text="A2 = 110")]),
            ExtractedQuestion(question_id="4b", lines=[ExtractedLine(line_no=1, raw_text="a // b")]),
        ],
        [QuestionRubric(question_id="Câu 4", question_text="Hình học", max_score=3.5)],
    )
    assert "Câu 4" in rollup
    assert len(rollup["Câu 4"].lines) == 2


def test_grade_with_exam_rubric_prevents_score_inflation_for_partial_paper():
    """Học sinh chỉ làm Câu 2a, 2b, 2c (tối đa 3.5đ) thì Câu 3, Câu 4 PHẢI 0đ -> không bao giờ phồng thành 8.25/10đ!"""
    rubric = ExamRubric(
        exam_id="TEST_NO_INFLATE",
        title="Đề Toán 7 chuẩn",
        total_points=10.0,
        questions=[
            QuestionRubric(
                question_id="Câu 2a",
                question_text="x - 3/4 = 2/5",
                max_score=1.0,
                criteria=[RubricCriterion(criterion_id="C2A", name="Giải đúng", max_score=1.0, required_logic="x = 23/20")],
            ),
            QuestionRubric(
                question_id="Câu 2b",
                question_text="1/4 x - 3/8 = -2/3",
                max_score=1.0,
                criteria=[RubricCriterion(criterion_id="C2B", name="Giải đúng", max_score=1.0, required_logic="x = -7/6")],
            ),
            QuestionRubric(
                question_id="Câu 2c",
                question_text="(1/2 - x)^2 - 24/25 = 1",
                max_score=1.5,
                criteria=[RubricCriterion(criterion_id="C2C", name="Giải đúng", max_score=1.5, required_logic="-9/10; 19/10")],
            ),
            QuestionRubric(question_id="Câu 3", question_text="Toán thực tế", max_score=3.0),
            QuestionRubric(question_id="Câu 4", question_text="Hình học", max_score=3.5),
        ],
    )
    partial_student = [
        ExtractedQuestion(question_id="a", lines=[ExtractedLine(line_no=1, raw_text="x = 23/20")]),
        ExtractedQuestion(question_id="b", lines=[ExtractedLine(line_no=1, raw_text="x = -7/6")]),
        ExtractedQuestion(question_id="c", lines=[ExtractedLine(line_no=1, raw_text="(1/2-x)^2 = 49/25")]),
    ]
    results, _ = reasoning_agent.grade_with_exam_rubric(partial_student, rubric)
    by_id = {r.question_id: r for r in results}
    assert by_id["Câu 3"].suggested_score == 0.0
    assert by_id["Câu 4"].suggested_score == 0.0
    assert sum(r.suggested_score for r in results) <= 3.5


def test_hitl_rename_student_regrade_question_and_export_excel():
    """Đổi tên học sinh + sửa dòng OCR + chấm lại câu + xuất lại Excel bảng điểm cả lớp."""
    # 1. Tạo batch 2 học sinh qua API smart folder
    files = [
        ("files", ("em_hoa.jpg", _sample_img("HS Hoa"), "image/jpeg")),
        ("files", ("em_minh.jpg", _sample_img("HS Minh"), "image/jpeg")),
    ]
    res_batch = client.post(
        "/api/v1/submissions/grade-folder-smart-upload",
        files=files,
        data={"exam_id": "TOAN9_01"},
    )
    assert res_batch.status_code == 200
    batch = res_batch.json()
    sub_id = batch["students"][0]["submission_id"]
    batch_id = batch["batch_id"]

    # 2. Đổi tên học sinh trực tiếp
    res_ren = client.patch(
        f"/api/v1/submissions/{sub_id}/student",
        json={"student_name": "Phạm Thị Hoa 7A"},
    )
    assert res_ren.status_code == 200
    assert res_ren.json()["student_name"] == "Phạm Thị Hoa 7A"
    assert db.get_submission(sub_id).student_name == "Phạm Thị Hoa 7A"

    # 3. Sửa dòng OCR bị đọc nhầm & bấm Chấm lại câu Q1 (thêm ĐKXĐ và nghiệm chuẩn)
    res_reg = client.post(
        f"/api/v1/submissions/{sub_id}/regrade-question",
        json={
            "question_id": "Q1",
            "edited_lines": [
                {"line_no": 1, "raw_text": "ĐKXĐ: x != 2", "latex": "x \\neq 2", "line_type": "condition"},
                {"line_no": 2, "raw_text": "x + (x - 2) = 4 <=> 2x = 6 <=> x = 3", "latex": "x + (x - 2) = 4 \\Leftrightarrow 2x = 6 \\Leftrightarrow x = 3", "line_type": "equation"},
                {"line_no": 3, "raw_text": "x = 3 thỏa mãn ĐKXĐ, Vậy S = {3}", "latex": "S = \\{3\\}", "line_type": "conclusion"},
            ],
            "run_reread_ocr": False,
        },
    )
    assert res_reg.status_code == 200
    updated_rec = res_reg.json()
    assert updated_rec["question_results"][0]["suggested_score"] == 2.0
    assert updated_rec["ai_total_score"] == 2.0

    # 4. Xuất lại Excel mới nhất của batch -> tên học sinh mới và điểm mới phải có mặt
    res_xl = client.get(f"/api/v1/batches/{batch_id}/export-excel")
    assert res_xl.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(res_xl.content))
    ws = wb.active
    sheet_text = " ".join(str(cell.value or "") for row in ws.iter_rows() for cell in row)
    assert "Phạm Thị Hoa 7A" in sheet_text
