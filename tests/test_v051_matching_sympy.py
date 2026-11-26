from backend.app.models.schemas import ExtractedLine, ExtractedQuestion, LineType, QuestionRubric
from backend.app.services.agent2_grader import match_student_questions_to_rubric, reasoning_agent
from backend.app.services.math_verifier import verify_question_math


def test_reverse_parent_to_child_matching_with_explicit_subheaders():
    """OCR gom chung 'Câu 4' nhưng bên trong có 'a)', 'b)', 'c)' -> phải tách đúng sang Câu 4a, 4b, 4c."""
    rubric_qs = [
        QuestionRubric(question_id="Câu 4a", question_text="Tính A2 và A3", max_score=1.0),
        QuestionRubric(question_id="Câu 4b", question_text="Chứng minh a // b", max_score=1.0),
        QuestionRubric(question_id="Câu 4c", question_text="Tính BAM và HAM", max_score=1.5),
    ]
    student_qs = [
        ExtractedQuestion(
            question_id="Câu 4",
            source_files=["hs3.jpg"],
            lines=[
                ExtractedLine(line_no=1, raw_text="a) Vì A1 và A3 đối đỉnh nên A3 = 70°", line_type=LineType.TEXT),
                ExtractedLine(line_no=2, raw_text="b) Ta có a ⊥ c và b ⊥ c => a // b", line_type=LineType.TEXT),
                ExtractedLine(line_no=3, raw_text="c) BAM = 180° - 70° = 110°", line_type=LineType.EQUATION),
            ],
        )
    ]

    matched = match_student_questions_to_rubric(student_qs, rubric_qs)
    assert set(matched.keys()) == {"Câu 4a", "Câu 4b", "Câu 4c"}
    assert "70°" in matched["Câu 4a"].lines[0].raw_text
    assert "a // b" in matched["Câu 4b"].lines[0].raw_text
    assert "110°" in matched["Câu 4c"].lines[0].raw_text


def test_reverse_parent_to_child_matching_without_subheaders():
    """OCR gom chung 'Câu 4' không có a)/b)/c) -> phải map trọn câu cha sang các ý con để không bị 0đ oan."""
    rubric_qs = [
        QuestionRubric(question_id="Câu 4a", question_text="Tính góc", max_score=1.0),
        QuestionRubric(question_id="Câu 4b", question_text="Song song", max_score=1.0),
    ]
    student_qs = [
        ExtractedQuestion(
            question_id="Bài 4",
            lines=[
                ExtractedLine(line_no=1, raw_text="A2 = 180° - 70° = 110°", line_type=LineType.EQUATION),
                ExtractedLine(line_no=2, raw_text="c vuông góc a và b => a // b", line_type=LineType.TEXT),
            ],
        )
    ]

    matched = match_student_questions_to_rubric(student_qs, rubric_qs)
    assert set(matched.keys()) == {"Câu 4a", "Câu 4b"}
    assert len(matched["Câu 4a"].lines) == 2
    assert len(matched["Câu 4b"].lines) == 2


def test_sympy_arithmetic_percentage_verification():
    """SymPy kiểm chứng phép tính tiền tệ hàng nghìn và phần trăm."""
    q_ok = ExtractedQuestion(
        question_id="Câu 3",
        lines=[
            ExtractedLine(line_no=1, raw_text="250.000 * 2 = 500.000 đ", line_type=LineType.EQUATION),
            ExtractedLine(
                line_no=2,
                raw_text="(500.000 + 300.000) - (500.000 + 300.000) * 20% = 640.000 đ",
                line_type=LineType.EQUATION,
            ),
        ],
    )
    mv_ok = verify_question_math(q_ok)
    assert mv_ok.equations_checked >= 2
    assert mv_ok.valid_transitions >= 2
    assert mv_ok.invalid_transitions == 0

    q_wrong = ExtractedQuestion(
        question_id="Câu 3",
        lines=[
            ExtractedLine(line_no=1, raw_text="700.000 - 640.000 = 80.000 đ", line_type=LineType.EQUATION),
        ],
    )
    mv_wrong = verify_question_math(q_wrong)
    assert mv_wrong.invalid_transitions == 1
    assert "60000" in mv_wrong.issues[0]


def test_sympy_geometry_angle_verification():
    """SymPy kiểm chứng phép cộng trừ góc hình học có ký hiệu độ."""
    q_ok = ExtractedQuestion(
        question_id="Câu 4a",
        lines=[
            ExtractedLine(line_no=1, raw_text="\\widehat{A_2} = 180^\\circ - 70^\\circ = 110^\\circ", latex="\\widehat{A_2} = 180^\\circ - 70^\\circ = 110^\\circ"),
        ],
    )
    mv_ok = verify_question_math(q_ok)
    assert mv_ok.valid_transitions == 1
    assert mv_ok.invalid_transitions == 0

    q_wrong = ExtractedQuestion(
        question_id="Câu 4c",
        lines=[
            ExtractedLine(line_no=1, raw_text="\\widehat{BAM} = 180° - 70° = 120°", latex="\\widehat{BAM} = 180° - 70° = 120°"),
        ],
    )
    mv_wrong = verify_question_math(q_wrong)
    assert mv_wrong.invalid_transitions == 1


def test_list_wrapped_gemini_grader_json_unwrapping():
    """Gemini trả JSON bọc trong list 1 phần tử [{...}] không được gây AttributeError 500."""
    wrapped_auto = [
        {
            "detected_exam_title": "Toán 7",
            "total_points": 1.5,
            "suggested_score": 1.5,
            "confidence": 0.95,
            "teacher_summary_comment": "Đúng hoàn toàn",
            "criteria_evaluations": [
                {"criterion_id": "C1", "name": "Giải đúng", "score": 1.5, "max_score": 1.5, "status": "passed", "feedback": "Tốt"}
            ],
        }
    ]
    title, auto_res = reasoning_agent._parse_auto_rubric_response(wrapped_auto, "Câu 2c")
    assert title == "Toán 7"
    assert auto_res.suggested_score == 1.5

    rq = QuestionRubric(question_id="Câu 2c", question_text="Giải PT", max_score=1.5)
    rub_res = reasoning_agent._parse_rubric_response(wrapped_auto, rq)
    assert rub_res.suggested_score == 1.5
