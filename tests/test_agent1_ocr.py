import pytest
from backend.app.models.schemas import LineType, ExtractedLine, ExtractedQuestion
from backend.app.services.agent1_ocr import (
    vision_agent,
    parse_and_validate_ocr_json,
    OCRLowConfidenceError,
    JSONParseError,
    GeminiAPIError,
)


def test_ut19_mock_extraction_valid():
    """UT-19: Mock OCR trả về danh sách ExtractedQuestion hợp lệ với cấu trúc dòng toán học."""
    questions = vision_agent.extract_latex(b"dummy_bytes")
    assert len(questions) > 0
    q = questions[0]
    assert q.question_id == "Q1"
    assert q.confidence >= 0.9
    assert len(q.lines) == 5
    assert q.lines[0].line_type == LineType.EQUATION
    assert q.lines[-1].line_type == LineType.CONCLUSION


def test_ut19_perfect_solution_scenario():
    """Kiểm tra kịch bản bài làm hoàn hảo có ĐKXĐ và đối chiếu."""
    questions = vision_agent._mock_extraction(scenario="perfect_solution")
    assert len(questions) == 1
    q = questions[0]
    assert any(l.line_type == LineType.CONDITION for l in q.lines)
    assert any(l.line_type == LineType.CONCLUSION for l in q.lines)
    assert q.confidence >= 0.95


def test_ut20_ocr_low_confidence_empty():
    """UT-20: JSON rỗng hoặc không có câu hỏi -> raise OCRLowConfidenceError."""
    with pytest.raises(OCRLowConfidenceError):
        parse_and_validate_ocr_json("[]")


def test_parser_strip_markdown_fences():
    """Kiểm tra parser tự động làm sạch markdown code fence ```json."""
    raw = """```json
    [
      {
        "question_id": "1a",
        "lines": [
          {
            "line_index": 0,
            "raw_text": "x + 2 = 5",
            "latex": "x + 2 = 5",
            "confidence": 0.95,
            "line_type": "equation"
          }
        ]
      }
    ]
    ```"""
    questions = parse_and_validate_ocr_json(raw)
    assert len(questions) == 1
    assert questions[0].question_id == "1a"
    assert questions[0].lines[0].line_type == LineType.EQUATION
    assert questions[0].lines[0].latex == "x + 2 = 5"


def test_parser_invalid_json_syntax():
    """Kiểm tra parser bắt lỗi cú pháp JSON và ném JSONParseError."""
    invalid_json = "{ invalid_json: true, "
    with pytest.raises(JSONParseError):
        parse_and_validate_ocr_json(invalid_json)


def test_parser_line_type_fallback():
    """Kiểm tra nếu line_type không thuộc enum chuẩn thì fallback về EQUATION."""
    raw = """[
      {
        "question_id": "Q2",
        "lines": [
          {
            "raw_text": "2x = 4",
            "line_type": "something_exotic_123"
          }
        ]
      }
    ]"""
    questions = parse_and_validate_ocr_json(raw)
    assert questions[0].lines[0].line_type == LineType.EQUATION


def test_overall_confidence_calculation():
    """Kiểm tra tính toán confidence trung bình tự động."""
    raw = """[
      {
        "question_id": "Q3",
        "lines": [
          {"raw_text": "line 1", "confidence": 0.8},
          {"raw_text": "line 2", "confidence": 0.6}
        ]
      }
    ]"""
    questions = parse_and_validate_ocr_json(raw)
    assert questions[0].overall_confidence == 0.7
    assert questions[0].confidence == 0.7


def test_parser_latex_backslash_escape_resilience():
    """Sửa lỗi Invalid \\escape khi LLM trộn lẫn \\\\Rightarrow hợp lệ và \\underline / \\frac / \\{ đơn lẻ."""
    raw = (
        '{"questions": [{"question_id": "Câu 1", "lines": ['
        '{"raw_text": "\\\\Rightarrow \\underline{x} = \\frac{1}{2} \\neq 0 \\begin{cases} a=1 \\\\ b=2 \\end{cases}", '
        '"latex": "\\\\Rightarrow \\underline{x} = \\frac{1}{2} \\neq 0", '
        '"line_type": "equation", "confidence": 0.95}'
        ']}]}'
    )
    questions = parse_and_validate_ocr_json(raw)
    assert len(questions) == 1
    assert r"\Rightarrow" in questions[0].lines[0].latex
    assert r"\underline{x}" in questions[0].lines[0].latex
    assert r"\frac{1}{2}" in questions[0].lines[0].latex
    assert r"\neq" in questions[0].lines[0].latex

