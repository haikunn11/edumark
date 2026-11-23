import pytest
from backend.app.models.schemas import (
    ExtractedQuestion,
    ExtractedLine,
    LineType,
    QuestionRubric,
    RubricCriterion,
)
from backend.app.services.agent2_grader import reasoning_agent, compute_confidence


@pytest.fixture
def math_rubric() -> QuestionRubric:
    """Barem chuẩn phương trình chứa ẩn ở mẫu: tổng 2.0đ (0.5đ ĐKXĐ + 1.0đ biến đổi + 0.5đ kết luận)."""
    return QuestionRubric(
        question_id="Q1",
        max_score=2.0,
        total_points=2.0,
        allow_error_propagation=True,
        criteria=[
            RubricCriterion(
                criterion_id="Q1_C1_DKXD",
                name="Điều kiện xác định",
                max_score=0.5,
                max_points=0.5,
                required_logic="x != 2",
            ),
            RubricCriterion(
                criterion_id="Q1_C2_BIEN_DOI",
                name="Quy đồng và biến đổi",
                max_score=1.0,
                max_points=1.0,
                required_logic="2x = 6 <=> x = 3",
            ),
            RubricCriterion(
                criterion_id="Q1_C3_KET_LUAN",
                name="Kết luận và đối chiếu",
                max_score=0.5,
                max_points=0.5,
                required_logic="x = 3 thỏa mãn ĐKXĐ, S = {3}",
            ),
        ],
    )


def test_ut23_perfect_answer_full_marks(math_rubric):
    """UT-23: Bài làm hoàn hảo đầy đủ ĐKXĐ, biến đổi đúng và đối chiếu -> Đạt điểm tối đa 2.0đ, green."""
    lines = [
        ExtractedLine(line_no=1, raw_text="ĐKXĐ: x khác 2", latex="x \\neq 2", line_type=LineType.CONDITION),
        ExtractedLine(line_no=2, raw_text="2x = 6 <=> x = 3", latex="2x = 6 \\Leftrightarrow x = 3", line_type=LineType.EQUATION),
        ExtractedLine(line_no=3, raw_text="Đối chiếu: x = 3 thỏa mãn", latex="x = 3 \\text{ (thỏa mãn)}", line_type=LineType.TEXT),
        ExtractedLine(line_no=4, raw_text="Vậy S = {3}", latex="S = \\{3\\}", line_type=LineType.CONCLUSION),
    ]
    q = ExtractedQuestion(question_id="Q1", lines=lines, confidence=0.98)
    res = reasoning_agent.grade_question(q, math_rubric)

    assert res.earned_points == 2.0
    assert res.confidence_level == "green"
    assert res.error_propagation_detected is False
    assert all(c.status == "passed" for c in res.criteria_evaluations)


def test_ut24_missing_dkxd_partial_marks(math_rubric):
    """UT-24: Bài làm quên ĐKXĐ -> Tiêu chí ĐKXĐ được 0đ, các tiêu chí khác vẫn tính đúng."""
    lines = [
        ExtractedLine(line_no=1, raw_text="2x = 6 <=> x = 3", latex="2x = 6 \\Leftrightarrow x = 3", line_type=LineType.EQUATION),
        ExtractedLine(line_no=2, raw_text="Vậy nghiệm x = 3", latex="S = \\{3\\}", line_type=LineType.CONCLUSION),
    ]
    q = ExtractedQuestion(question_id="Q1", lines=lines, confidence=0.95)
    res = reasoning_agent.grade_question(q, math_rubric)

    dkxd_eval = next(c for c in res.criteria_evaluations if "dkxd" in c.criterion_id.lower())
    assert dkxd_eval.earned_points == 0.0
    assert dkxd_eval.status == "failed"
    assert res.earned_points < 2.0


def test_ut25_error_propagation_detected(math_rubric):
    """
    UT-25: Học sinh tính toán sai ở bước biến đổi (chuyển vế đổi dấu nhầm ra 2x = 8 => x = 4),
    nhưng bước sau vẫn kết luận đúng phương pháp trên nghiệm x = 4 đó
    -> Bắt được Error Propagation, bảo lưu điểm phương pháp cho bước kết luận.
    """
    lines = [
        ExtractedLine(line_no=1, raw_text="ĐKXĐ: x khác 2", latex="x \\neq 2", line_type=LineType.CONDITION),
        # Biến đổi có làm nhưng tính toán sai số (không ra 2x = 6)
        ExtractedLine(line_no=2, raw_text="2x = 8 <=> x = 4 (tinh sai)", latex="2x = 8 \\Leftrightarrow x = 4", line_type=LineType.EQUATION),
        # Bước kết luận làm đúng phương pháp trên số sai x = 4
        ExtractedLine(line_no=3, raw_text="Đối chiếu: x = 4 thỏa mãn ĐKXĐ, S = {4}", latex="S = \\{4\\}", line_type=LineType.CONCLUSION),
    ]
    q = ExtractedQuestion(question_id="Q1", lines=lines, confidence=0.9)
    res = reasoning_agent.grade_question(q, math_rubric)

    assert res.error_propagation_detected is True
    assert len(res.affected_steps) > 0
    # Bước kết luận được bảo lưu điểm phương pháp
    kl_eval = next(c for c in res.criteria_evaluations if "ket_luan" in c.criterion_id.lower())
    assert kl_eval.earned_points > 0.0
    assert "Lỗi lan truyền" in kl_eval.feedback


def test_ut26_error_propagation_disabled(math_rubric):
    """UT-26: Khi barem tắt allow_error_propagation=False -> Không kích hoạt bảo lưu lỗi lan truyền."""
    math_rubric.allow_error_propagation = False
    lines = [
        ExtractedLine(line_no=1, raw_text="ĐKXĐ: x khác 2", latex="x \\neq 2", line_type=LineType.CONDITION),
        ExtractedLine(line_no=2, raw_text="2x = 8 <=> x = 4 (tính sai)", latex="2x = 8", line_type=LineType.EQUATION),
        ExtractedLine(line_no=3, raw_text="S = {4}", latex="S = \\{4\\}", line_type=LineType.CONCLUSION),
    ]
    q = ExtractedQuestion(question_id="Q1", lines=lines, confidence=0.9)
    res = reasoning_agent.grade_question(q, math_rubric)

    assert res.error_propagation_detected is False


def test_ut27_all_wrong_empty_solution(math_rubric):
    """UT-27: Bài làm rỗng hoặc sai toàn bộ -> Điểm = 0, confidence rơi vào ngưỡng red."""
    lines = [
        ExtractedLine(line_no=1, raw_text="Em không biết làm bài này", latex="", line_type=LineType.TEXT)
    ]
    q = ExtractedQuestion(question_id="Q1", lines=lines, confidence=0.4)
    res = reasoning_agent.grade_question(q, math_rubric)

    assert res.earned_points == 0.0
    assert res.confidence_level == "red"
    assert res.needs_review is True


def test_ut28_confidence_green_threshold():
    """UT-28: OCR nét cao (0.95), khớp tiêu chí 100% -> confidence_level = green."""
    conf, level = compute_confidence(ocr_confidence=0.95, match_quality=1.0, has_ambiguity=False)
    assert level == "green"
    assert conf >= 0.90


def test_ut29_confidence_red_threshold():
    """UT-29: OCR thấp (0.4), khớp tiêu chí kém -> confidence_level = red."""
    conf, level = compute_confidence(ocr_confidence=0.4, match_quality=0.2, has_ambiguity=True)
    assert level == "red"
    assert conf < 0.70
