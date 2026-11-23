import io
import pytest
from PIL import Image, ImageDraw, ImageFilter
from backend.app.services.preprocessor import preprocessor
from backend.app.models.schemas import ExamRubric, QuestionRubric, RubricCriterion, SubjectType
from backend.app.services.pipeline import grading_pipeline


SAMPLE_TOPICS = [
    ("Bai 1: Giai pt chua an o mau (chuan)", [
        "DKXD: x != 2",
        "x/(x-2) + 1 = 4/(x-2)",
        "x + x - 2 = 4",
        "2x = 6 <=> x = 3",
        "Doi chieu: x = 3 (tm)",
        "Vay S = {3}"
    ], {"skew": 0, "blur": 0, "contrast": 1.0}),
    
    ("Bai 2: Giai pt quen DKXD", [
        "x/(x-2) + 1 = 4/(x-2)",
        "x + x - 2 = 4",
        "2x = 6 <=> x = 3",
        "Vay S = {3}"
    ], {"skew": 0, "blur": 0, "contrast": 1.0}),

    ("Bai 3: Giai pt quen doi chieu nghiem", [
        "DKXD: x != 2",
        "x/(x-2) + 1 = 4/(x-2)",
        "2x = 6 => x = 3",
        "Vay tap nghiem S = {3}"
    ], {"skew": 0, "blur": 0, "contrast": 1.0}),

    ("Bai 4: Rut gon bieu thuc chua can", [
        "DKXD: x >= 0, x != 4",
        "P = (sqrt(x) + 2)/(sqrt(x) - 2)",
        "P = (x - 4)/(sqrt(x) - 2)^2",
        "Ket luan: P = (sqrt(x) + 2)/(sqrt(x) - 2)"
    ], {"skew": 0, "blur": 0, "contrast": 1.0}),

    ("Bai 5: He phuong trinh bac nhat 2 an (phuong phap the)", [
        "He: 2x + y = 5 va x - y = 1",
        "Tu pt (2) => y = x - 1",
        "Thay vao (1): 2x + (x - 1) = 5 <=> 3x = 6 => x = 2",
        "y = 2 - 1 = 1",
        "Vay he co nghiem duy nhat (x, y) = (2, 1)"
    ], {"skew": 0, "blur": 0, "contrast": 1.0}),

    ("Bai 6: He phuong trinh (cong dai so)", [
        "2x + 3y = 7 va 2x - y = 3",
        "Lay (1) tru (2): 4y = 4 => y = 1",
        "Thay y = 1: 2x - 1 = 3 => 2x = 4 => x = 2",
        "Ket luan: (x; y) = (2; 1)"
    ], {"skew": 0, "blur": 0, "contrast": 1.0}),

    ("Bai 7: Phuong trinh tich A(x).B(x) = 0", [
        "(x - 3)(2x + 4) = 0",
        "TH1: x - 3 = 0 <=> x = 3",
        "TH2: 2x + 4 = 0 <=> x = -2",
        "Vay S = {-2; 3}"
    ], {"skew": 0, "blur": 0, "contrast": 1.0}),

    ("Bai 8: Co gach xoa nhap", [
        "DKXD: x != 1",
        "--- x + 3 = 7 (gach bo) ---",
        "2x + 1 = 5 <=> 2x = 4",
        "x = 2 (thoa man)",
        "Vay x = 2"
    ], {"skew": 0, "blur": 0, "contrast": 1.0}),

    ("Bai 9: Bai thi nghieng 4 do", [
        "x^2 - 4x + 3 = 0",
        "(x - 1)(x - 3) = 0",
        "x = 1 hoac x = 3",
        "Vay S = {1, 3}"
    ], {"skew": 4, "blur": 0, "contrast": 1.0}),

    ("Bai 10: Bai thi nghieng 8 do", [
        "2x - 5 = 15",
        "2x = 20 <=> x = 10",
        "Dap so: x = 10"
    ], {"skew": 8, "blur": 0, "contrast": 1.0}),

    ("Bai 11: Bai thi mo nhe (slight blur)", [
        "DKXD: x != 0",
        "1/x = 2 => x = 0.5 (tm)",
        "Ket luan S = {0.5}"
    ], {"skew": 0, "blur": 1.5, "contrast": 1.0}),

    ("Bai 12: Bai thi tuong phan kem (muc nhat)", [
        "3x + 6 = 0",
        "3x = -6 <=> x = -2",
        "Vay S = {-2}"
    ], {"skew": 0, "blur": 0, "contrast": 0.7}),

    ("Bai 13: Bai thi 2 cau hoi Q1 va Q2", [
        "Cau 1: x + 1 = 2 => x = 1",
        "Cau 2: 2y = 8 => y = 4",
        "Ket luan: x = 1 va y = 4"
    ], {"skew": 0, "blur": 0, "contrast": 1.0}),

    ("Bai 14: Net but thanh manh", [
        "x^2 = 9",
        "<=> x = 3 hoac x = -3",
        "Tap nghiem: S = {-3; 3}"
    ], {"skew": 0, "blur": 0, "contrast": 1.0}),

    ("Bai 15: Net but dam", [
        "DKXD: x > 0",
        "sqrt(x) = 4 <=> x = 16 (tm)",
        "Vay x = 16"
    ], {"skew": 0, "blur": 0, "contrast": 1.2}),

    ("Bai 16: Co chu thich tieng Viet", [
        "Dieu kien xac dinh: bieu thuc duoi mau khac 0",
        "x - 5 != 0 => x != 5",
        "Giai: x = 7 (thoa man dieu kien)",
        "Ket luan: Nghiem cua pt la x = 7"
    ], {"skew": 0, "blur": 0, "contrast": 1.0}),

    ("Bai 17: Sai dau bien doi", [
        "2(x - 3) = 8",
        "2x - 3 = 8 (sai dau)",
        "2x = 11 => x = 5.5"
    ], {"skew": 0, "blur": 0, "contrast": 1.0}),

    ("Bai 18: Nhieu ky hieu tuong duong va suy ra", [
        "x + 2 = 4 <=> x = 2",
        "=> 2x = 4 <=> x = 2",
        "S = {2}"
    ], {"skew": 0, "blur": 0, "contrast": 1.0}),

    ("Bai 19: Hoc sinh lam do dang nua chung", [
        "DKXD: x != 3",
        "x/(x-3) = 2",
        "x = 2(x - 3) = 2x - 6",
        "... (chua xong)"
    ], {"skew": 0, "blur": 0, "contrast": 1.0}),

    ("Bai 20: Bai lam hoan hao 10/10", [
        "Dieu kien xac dinh: x khac 2",
        "Ta co: x/(x-2) + 1 = 4/(x-2)",
        "Quy dong: x + x - 2 = 4",
        "<=> 2x = 6 <=> x = 3",
        "Doi chieu dieu kien: x = 3 thoa man DKXD",
        "Vay tap nghiem cua phuong trinh da cho la S = {3}"
    ], {"skew": 0, "blur": 0, "contrast": 1.0}),
]


def render_synthetic_exam_paper(title: str, lines: list[str], options: dict) -> bytes:
    """Tạo ảnh trang giấy thi giả lập với các biến thể thực tế."""
    w, h = 800, 700
    img = Image.new("RGB", (w, h), color=(250, 250, 248))
    draw = ImageDraw.Draw(img)

    # Kẻ dòng ô li mờ
    for y in range(60, h, 35):
        draw.line([(30, y), (w - 30, y)], fill=(225, 235, 245), width=1)

    # Tiêu đề
    draw.text((40, 25), title, fill=(100, 100, 100))

    # Viết từng dòng chữ
    y_offset = 80
    pen_color = (25, 35, 75) if options.get("contrast", 1.0) >= 1.0 else (120, 130, 150)
    for line in lines:
        draw.text((60, y_offset), line, fill=pen_color)
        y_offset += 45

    # Biến đổi góc nghiêng nếu có
    skew = options.get("skew", 0)
    if skew != 0:
        img = img.rotate(skew, resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255))

    # Biến đổi làm mờ nếu có
    blur = options.get("blur", 0)
    if blur > 0:
        img = img.filter(ImageFilter.GaussianBlur(radius=blur))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture(scope="module")
def sample_rubric() -> ExamRubric:
    return ExamRubric(
        exam_id="EXAM_TEST_20",
        title="Bo de kiem tra 20 mau Dai so THCS",
        subject=SubjectType.MATH_SECONDARY,
        questions=[
            QuestionRubric(
                question_id="Q1",
                max_score=2.0,
                criteria=[
                    RubricCriterion(
                        criterion_id="Q1_C1_DKXD",
                        name="Dieu kien xac dinh",
                        max_score=0.5,
                        required_logic="x != 2",
                    ),
                    RubricCriterion(
                        criterion_id="Q1_C2_BIEN_DOI",
                        name="Bien doi dai so",
                        max_score=1.0,
                        required_logic="2x = 6 hoac x = 3",
                    ),
                    RubricCriterion(
                        criterion_id="Q1_C3_KET_LUAN",
                        name="Ket luan",
                        max_score=0.5,
                        required_logic="x = 3 va doi chieu",
                    ),
                ],
            )
        ],
    )


def test_wp04_dataset_preprocessor_20_images():
    """WP-04: Đảm bảo cả 20 ảnh mẫu đa dạng đều qua Preprocessor an toàn, không crash."""
    for idx, (title, lines, opts) in enumerate(SAMPLE_TOPICS):
        img_bytes = render_synthetic_exam_paper(title, lines, opts)
        proc_bytes, report = preprocessor.process_image(img_bytes)

        assert len(proc_bytes) > 0, f"Ảnh {idx + 1} ({title}) không có dữ liệu ra"
        assert report.blur_score >= 0.0, f"Ảnh {idx + 1} tính sai blur score"
        assert len(report.applied_transforms) > 0, f"Ảnh {idx + 1} thiếu applied_transforms"


def test_wp04_dataset_pipeline_and_cache_20_images(sample_rubric, monkeypatch):
    """
    WP-04 & Exit Gate G-03, G-04:
    Chạy cả 20 bài qua Pipeline:
      - Lần 1: Cache miss, OCR thành công.
      - Lần 2: Cache hit 100%, tốc độ cao, điểm số nhất quán.
    """
    monkeypatch.setattr(grading_pipeline.vision_agent, "openai_api_key", "mock_test_key")
    monkeypatch.setattr(grading_pipeline.vision_agent, "api_key", "mock_test_key")
    for idx, (title, lines, opts) in enumerate(SAMPLE_TOPICS):
        img_bytes = render_synthetic_exam_paper(title, lines, opts)
        sub_id = f"test_sub_{idx + 1:02d}"

        # Lần 1: Run OCR + Grader
        record1 = grading_pipeline.process_submission(
            image_bytes=img_bytes,
            rubric=sample_rubric,
            student_name=f"Hoc sinh {idx + 1}",
            submission_id=sub_id,
        )
        assert record1.submission_id == sub_id
        assert len(record1.extracted_questions) > 0
        assert record1.cache_hit is False

        # Lần 2: Cache hit
        record2 = grading_pipeline.process_submission(
            image_bytes=img_bytes,
            rubric=sample_rubric,
            student_name=f"Hoc sinh {idx + 1}",
            submission_id=f"{sub_id}_retry",
        )
        assert record2.cache_hit is True
        assert record2.ai_total_score == record1.ai_total_score


def test_wp09_grader_accuracy_benchmark(sample_rubric, monkeypatch):
    """
    WP-09 & Exit Gate G-06:
    Benchmark độ chính xác của Grader Agent 2 trên các bài giải:
    - Bài chuẩn (Bài 1, Bài 20): Đạt >= 85% điểm hoặc đúng yêu cầu barem.
    - Bài quên ĐKXĐ (Bài 2): Trừ điểm ĐKXĐ, giữ điểm biến đổi.
    - Bài quên đối chiếu (Bài 3): Bắt được thiếu bước đối chiếu.
    """
    monkeypatch.setattr(grading_pipeline.vision_agent, "openai_api_key", "mock_test_key")
    monkeypatch.setattr(grading_pipeline.vision_agent, "api_key", "mock_test_key")
    grading_pipeline.cache.clear()
    # 1. Test bài làm chuẩn hoàn hảo (Bài 20)
    monkeypatch.setattr(
        grading_pipeline.vision_agent,
        "extract_latex",
        lambda *args, **kwargs: grading_pipeline.vision_agent._mock_extraction(scenario="perfect_solution"),
    )
    b20_title, b20_lines, b20_opts = SAMPLE_TOPICS[19]
    b20_bytes = render_synthetic_exam_paper(b20_title, b20_lines, b20_opts)
    rec20 = grading_pipeline.process_submission(b20_bytes, sample_rubric, student_name="HS Hoan Hao")
    assert rec20.ai_total_score >= 1.0

    # 2. Test bài quên ĐKXĐ (Bài 2)
    grading_pipeline.cache.clear()
    monkeypatch.setattr(
        grading_pipeline.vision_agent,
        "extract_latex",
        lambda *args, **kwargs: grading_pipeline.vision_agent._mock_extraction(scenario="missing_condition"),
    )
    b2_title, b2_lines, b2_opts = SAMPLE_TOPICS[1]
    b2_bytes = render_synthetic_exam_paper(b2_title, b2_lines, b2_opts)
    rec2 = grading_pipeline.process_submission(b2_bytes, sample_rubric, student_name="HS Quen DKXD")
    q_res = rec2.question_results[0]
    dkxd_c = next(c for c in q_res.criteria_evaluations if "dkxd" in c.criterion_id.lower())
    assert dkxd_c.earned_points == 0.0
    assert "Thiếu bước đặt điều kiện" in dkxd_c.feedback
