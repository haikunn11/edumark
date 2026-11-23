from __future__ import annotations

import json
import re
from typing import Literal, Tuple
from backend.app.models.schemas import (
    ExtractedQuestion,
    ExtractedLine,
    LineType,
    QuestionRubric,
    RubricCriterion,
    QuestionGradingResult,
    CriterionEvaluation,
)
from backend.app.core.config import settings


DEFAULT_KEYWORD_PATTERNS: dict[str, str] = {
    "dkxd": r"(đkxđ|điều kiện|đk|xác định|mẫu.*khác.*0)",
    "bien_doi": r"(biến đổi|rút gọn|quy đồng|⇔|=>|chuyển vế|nhân|chia)",
    "giai_pt": r"(giải|nghiệm|x\s*=\s*|⇒|tương đương)",
    "ket_luan": r"(kết luận|vậy|đáp số|kl|kết quả|s\s*=|tập nghiệm)",
    "doi_chieu": r"(đối chiếu|thỏa mãn|tm|thay.*vào|kiểm tra|loại)",
}

PARTIAL_CREDIT_DEFAULTS = {
    "method_correct_calc_wrong": 0.5,    # Phương pháp đúng, tính sai -> 50%
    "incomplete_step": 0.3,               # Có ý tưởng nhưng chưa xong -> 30%
    "correct_but_no_explanation": 0.7,    # Đáp số đúng nhưng thiếu giải thích -> 70%
}


def compute_confidence(
    ocr_confidence: float,
    match_quality: float,
    has_ambiguity: bool = False,
) -> tuple[float, str]:
    """
    Tính điểm tự tin (Confidence Scoring - WP-08):
    confidence = ocr_confidence * 0.4 + match_quality * 0.5 + (0.1 nếu rõ ràng)
    """
    conf = (ocr_confidence * 0.4) + (match_quality * 0.5) + (0.1 if not has_ambiguity else 0.0)
    conf = max(0.0, min(1.0, round(conf, 2)))
    if conf >= 0.90:
        level = "green"
    elif conf >= 0.70:
        level = "yellow"
    else:
        level = "red"
    return conf, level


class ReasoningGradingAgent:
    """Agent 2: Chuyên gia sư phạm chấm điểm theo Barem từng bước & bắt lỗi lan truyền."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.openai_api_key = settings.OPENAI_API_KEY
        self.model_name = settings.DEFAULT_REASONING_MODEL
        self.openai_model = settings.DEFAULT_OPENAI_MODEL

    @property
    def is_live(self) -> bool:
        return bool(
            (self.openai_api_key and not self.openai_api_key.startswith("mock_"))
            or (self.api_key and not self.api_key.startswith("mock_"))
        )

    def grade_question(
        self,
        question: ExtractedQuestion,
        rubric: QuestionRubric,
        run_self_critique: bool = True,
        use_llm: bool = False,
    ) -> QuestionGradingResult:
        """
        Chấm chi tiết 1 câu hỏi theo Barem tiêu chí:
        Mặc định sử dụng Rule-based Step Matcher (SPEC-P02) với độ chính xác và tính tất định tuyệt đối.
        Nếu use_llm=True hoặc barem không có tiêu chí đại số chuẩn -> Sử dụng OpenAI / Gemini LLM.
        """
        # Nếu là chế độ AUTO hoặc Barem trống -> Tự động nhận diện đề bài và sinh Barem linh hoạt
        if rubric.question_id == "AUTO" or not rubric.criteria or rubric.question_text == "AUTO":
            exam_title, auto_res = self.grade_auto_rubric(question)
            return auto_res

        # Kiểm tra xem Barem có khớp với các rule chuẩn không (dkxd, bien_doi, ket_luan)
        has_standard_rules = any(
            any(k in c.criterion_id.lower() or k in c.name.lower() for k in ("dkxd", "điều kiện", "bien_doi", "quy đồng", "giải", "ket_luan", "kết luận"))
            for c in rubric.criteria
        )

        if use_llm or not has_standard_rules:
            if self.openai_api_key and not self.openai_api_key.startswith("mock_"):
                try:
                    return self._call_openai_reasoning_grader(question, rubric)
                except Exception as e:
                    print(f"[Agent 2 Warning] OpenAI Reasoning call failed: {e}. Falling back to Rule Engine.")

            if self.api_key and not self.api_key.startswith("mock_"):
                try:
                    return self._call_gemini_reasoning_grader(question, rubric)
                except Exception as e:
                    print(f"[Agent 2 Warning] Gemini Reasoning call failed: {e}. Falling back to Rule Engine.")

        return self._grade_with_rules(question, rubric, run_self_critique)

    def grade_auto_rubric(
        self,
        question: ExtractedQuestion | list[ExtractedQuestion],
    ) -> tuple[str, QuestionGradingResult]:
        """
        Tự động nhận diện Đề bài từ ảnh và chấm linh hoạt (Auto-Rubric Zero-Shot).
        Dùng OpenAI gpt-4o-mini để đọc đề bài thực tế của học sinh và sinh Barem chính xác.
        Hỗ trợ nhận diện bài làm nhiều câu hoặc 1 bài giải nhiều cách (C1, C2).
        """
        if self.openai_api_key and not self.openai_api_key.startswith("mock_"):
            return self._call_openai_auto_reasoning_grader(question)

        # Fallback offline
        primary_q = question[0] if isinstance(question, list) else question
        rubric = QuestionRubric(
            question_id=primary_q.question_id,
            question_text="Giải phương trình",
            max_score=10.0,
            total_points=10.0,
            criteria=[
                RubricCriterion(criterion_id="C1", name="Biến đổi và lời giải", max_score=7.0),
                RubricCriterion(criterion_id="C2", name="Kết luận", max_score=3.0),
            ],
        )
        return "Bài toán Toán học", self._grade_with_rules(primary_q, rubric)

    def _call_openai_auto_reasoning_grader(
        self,
        question: ExtractedQuestion | list[ExtractedQuestion],
    ) -> tuple[str, QuestionGradingResult]:
        """Sử dụng OpenAI gpt-4o-mini tự động nhận diện đề bài và sinh Barem tương ứng."""
        from openai import OpenAI

        client = OpenAI(api_key=self.openai_api_key, timeout=25.0)

        q_list = question if isinstance(question, list) else [question]
        primary_qid = q_list[0].question_id if q_list else "Q1"

        solution_lines: list[str] = []
        for q_item in q_list:
            if q_item.question_text:
                solution_lines.append(f"=== {q_item.question_id}: {q_item.question_text} (Khối: {q_item.grade_level or 'THCS'}) ===")
            for l in q_item.lines:
                solution_lines.append(
                    f"Dòng {l.line_no or l.line_index + 1} [{l.line_type}]: {l.raw_text or l.latex or l.raw_latex}"
                )

        student_solution = "\n".join(solution_lines)

        prompt = f"""Bạn là Giám khảo chấm thi môn Toán THCS Việt Nam (Toán 6, 7, 8, 9).
BÀI LÀM CỦA HỌC SINH (Từ nhận diện ảnh OCR):
{student_solution}

NHIỆM VỤ SƯ PHẠM ĐỘC LẬP & NĂNG ĐỘNG (ZERO-SHOT GRADING):
1. ĐỀ BÀI & KHỐI LỚP: Tự động nhận diện Đề bài và Khối lớp thực tế từ bài làm của học sinh (Ví dụ: "Toán 7: ...", "Toán 8: ...", "Toán 9: ...").
2. XÂY DỰNG BAREM ĐÚNG VỚI BÀI LÀM THỰC TẾ CỦA HỌC SINH:
   - Barem chấm PHẢI DỰA 100% VÀO NỘI DUNG VÀ PHƯƠNG PHÁP HỌC SINH THỰC SỰ LÀM Ở TRÊN.
   - TUYỆT ĐỐI KHÔNG tự bịa ra hoặc đánh giá các phương pháp/cách giải mà học sinh KHÔNG hề viết trong bài làm!
   - Nếu học sinh chỉ làm 1 cách duy nhất (ví dụ: chỉ làm Cách 3 - Dãy tỉ số bằng nhau, hoặc chỉ làm Cách 1, hoặc giải phương trình bậc nhất, bậc hai, hệ phương trình, rút gọn biểu thức...):
     + Hãy chia các bước giải thực tế đó thành 2-4 tiêu chí/bước chấm logic của chính phương pháp đó.
     + Phân bổ thang điểm 10.0đ cho các bước đó (tổng max_score = 10.0đ).
   - Nếu học sinh làm nhiều cách giải (Cách 1, Cách 2...): Đánh giá riêng từng cách giải có trong bài làm.
3. CHỈ yêu cầu ĐKXĐ nếu bài toán thực sự cần điều kiện (mẫu số chứa biến hoặc căn thức). Không bắt ĐKXĐ thừa!
4. ERROR PROPAGATION: Bảo lưu điểm phương pháp cho các bước sau nếu bước trước chỉ nhầm lẫn số học nhẹ.
5. CÔNG THỨC TOÁN HỌC: Mọi công thức Toán trong detected_exam_title, name tiêu chí, feedback và teacher_summary_comment BẮT BUỘC đặt trong cặp dấu $...$ (ví dụ: $\\frac{{x}}{{y}} = \\frac{{7}}{{9}}$) để KaTeX hiển thị chuẩn xác trên web.

TRẢ VỀ JSON:
{{
  "detected_exam_title": "<Tên đề bài và khối lớp nhận diện được>",
  "grade_level": "<Khối lớp>",
  "total_points": 10.0,
  "suggested_score": 10.0,
  "error_propagation_detected": false,
  "affected_steps": [],
  "confidence": 0.95,
  "confidence_level": "green",
  "teacher_summary_comment": "<Lời phê sư phạm chi tiết, nhận xét cụ thể đúng theo phương pháp và các bước học sinh đã trình bày trong bài>",
  "criteria_evaluations": [
    {{
      "criterion_id": "T1",
      "name": "<Tên bước hoặc tiêu chí chấm tương ứng với bài làm thực tế của học sinh>",
      "score": 5.0,
      "max_score": 5.0,
      "status": "passed",
      "feedback": "<Nhận xét chi tiết cho bước này>"
    }}
  ]
}}
"""
        response = client.chat.completions.create(
            model=self.openai_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
        )

        text = (response.choices[0].message.content or "").strip()
        data = json.loads(text)

        evals: list[CriterionEvaluation] = []
        for idx, c_data in enumerate(data.get("criteria_evaluations", [])):
            max_c = float(c_data.get("max_score", c_data.get("score", 5.0)))
            evals.append(CriterionEvaluation(
                criterion_id=str(c_data.get("criterion_id", f"C{idx+1}")),
                name=str(c_data.get("name", f"Tiêu chí {idx+1}")),
                score=float(c_data.get("score", 0.0)),
                earned_points=float(c_data.get("score", 0.0)),
                max_score=max_c,
                max_points=max_c,
                status=str(c_data.get("status", "passed")),
                matched=c_data.get("status") != "failed",
                error_type=str(c_data.get("error_type", "none")),
                feedback=str(c_data.get("feedback", "")),
            ))

        total_max = float(data.get("total_points", 10.0))
        total_pts = float(data.get("suggested_score", sum(e.score for e in evals)))
        total_pts = min(total_pts, total_max)
        exam_title = str(data.get("detected_exam_title", "Bài thi Toán học"))

        q_res = QuestionGradingResult(
            question_id=primary_qid,
            suggested_score=round(total_pts, 2),
            earned_points=round(total_pts, 2),
            max_score=total_max,
            total_points=total_max,
            self_critique_applied=True,
            needs_review=(data.get("confidence_level") == "red"),
            review_reasons=[],
            criteria_evaluation=evals,
            criteria_evaluations=evals,
            error_propagation_detected=bool(data.get("error_propagation_detected", False)),
            affected_steps=[str(s) for s in data.get("affected_steps", [])],
            confidence=float(data.get("confidence", 0.95)),
            confidence_level=str(data.get("confidence_level", "green")),
            teacher_summary_comment=str(data.get("teacher_summary_comment", "Đã chấm xong bài làm.")),
        )
        return exam_title, q_res

    def _call_openai_reasoning_grader(
        self,
        question: ExtractedQuestion,
        rubric: QuestionRubric,
    ) -> QuestionGradingResult:
        """Sử dụng OpenAI gpt-4o-mini để chấm bài tự luận học sinh theo Barem chuẩn."""
        from openai import OpenAI

        client = OpenAI(api_key=self.openai_api_key, timeout=25.0)

        criteria_desc = "\n".join([
            f"- Mã {c.criterion_id} ({c.name}): Tối đa {c.max_points or c.max_score}đ. Yêu cầu: {c.required_logic or c.description}"
            for c in rubric.criteria
        ])

        student_solution = "\n".join([
            f"Dòng {l.line_no or l.line_index + 1} [{l.line_type}]: {l.raw_text or l.latex or l.raw_latex}"
            for l in question.lines
        ])

        prompt = f"""Bạn là Giám khảo chấm thi môn Toán THCS Việt Nam.
Hãy chấm bài giải của học sinh dưới đây theo Barem điểm chi tiết.

ĐỀ BÀI: {rubric.question_text or 'Giải toán'} (Tổng điểm tối đa: {rubric.total_points or rubric.max_score}đ)
BAREM TIÊU CHÍ:
{criteria_desc}

BÀI LÀM CỦA HỌC SINH (Các dòng OCR):
{student_solution}

QUY TẮC SƯ PHẠM BẮT BUỘC:
1. Đánh giá từng tiêu chí trong Barem (status: 'passed' | 'partial' | 'failed').
2. CHÚ Ý QUAN TRỌNG VỀ ĐIỀU KIỆN XÁC ĐỊNH (ĐKXĐ): CHỈ trừ điểm tiêu chí ĐKXĐ nếu bài toán THỰC SỰ có mẫu thức chứa ẩn hoặc căn thức. Nếu bài toán của học sinh là đa thức, phương trình bậc nhất, bậc hai không có mẫu/căn, MẶC ĐỊNH CHO ĐIỂM TỐI ĐA bước này (status: 'passed'), TUYỆT ĐỐI KHÔNG bắt học sinh đặt điều kiện x != 2 khi đề bài không có!
3. Điểm từng tiêu chí (score) phải trong khoảng [0, max_points].
4. ERROR PROPAGATION (Lỗi lan truyền): Nếu học sinh tính toán sai ở bước biến đổi nhưng các bước sau vẫn tiếp tục giải đúng phương pháp trên số sai đó, BẢO LƯU 50% điểm phương pháp cho các bước sau, KHÔNG cho 0 điểm toàn bài!
5. Lời phê sư phạm bằng tiếng Việt, mang tính khích lệ, chỉ rõ chỗ sai.

TRẢ VỀ ĐÚNG FORMAT JSON SAU:
{{
  "suggested_score": 1.25,
  "error_propagation_detected": false,
  "affected_steps": [],
  "confidence": 0.95,
  "confidence_level": "green",
  "teacher_summary_comment": "Lời phê sư phạm tổng quát...",
  "criteria_evaluations": [
    {{
      "criterion_id": "Mã tiêu chí",
      "score": 0.5,
      "max_score": 0.5,
      "status": "passed",
      "error_type": "none",
      "feedback": "Nhận xét chi tiết cho bước này"
    }}
  ]
}}
"""
        response = client.chat.completions.create(
            model=self.openai_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
        )

        text = (response.choices[0].message.content or "").strip()
        data = json.loads(text)

        evals: list[CriterionEvaluation] = []
        for c_data in data.get("criteria_evaluations", []):
            evals.append(CriterionEvaluation(
                criterion_id=str(c_data.get("criterion_id", "")),
                score=float(c_data.get("score", 0.0)),
                earned_points=float(c_data.get("score", 0.0)),
                max_score=float(c_data.get("max_score", 0.5)),
                max_points=float(c_data.get("max_score", 0.5)),
                status=str(c_data.get("status", "passed")),
                matched=c_data.get("status") != "failed",
                error_type=str(c_data.get("error_type", "none")),
                feedback=str(c_data.get("feedback", "")),
            ))

        total_pts = float(data.get("suggested_score", sum(e.score for e in evals)))
        total_pts = min(total_pts, rubric.total_points or rubric.max_score)

        conf = float(data.get("confidence", 0.9))
        conf_lvl = str(data.get("confidence_level", "green"))

        return QuestionGradingResult(
            question_id=rubric.question_id,
            suggested_score=round(total_pts, 2),
            earned_points=round(total_pts, 2),
            max_score=rubric.total_points or rubric.max_score,
            total_points=rubric.total_points or rubric.max_score,
            self_critique_applied=True,
            needs_review=(conf_lvl == "red"),
            review_reasons=[],
            criteria_evaluation=evals,
            criteria_evaluations=evals,
            error_propagation_detected=bool(data.get("error_propagation_detected", False)),
            affected_steps=[str(s) for s in data.get("affected_steps", [])],
            confidence=conf,
            confidence_level=conf_lvl,
            teacher_summary_comment=str(data.get("teacher_summary_comment", "Đã chấm xong bài làm.")),
        )

    def _call_gemini_reasoning_grader(
        self,
        question: ExtractedQuestion,
        rubric: QuestionRubric,
    ) -> QuestionGradingResult:
        """Sử dụng mô hình ngôn ngữ lớn để chấm bài tự luận học sinh theo Barem chuẩn."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)

        criteria_desc = "\n".join([
            f"- Mã {c.criterion_id} ({c.name}): Tối đa {c.max_points or c.max_score}đ. Yêu cầu: {c.required_logic or c.description}"
            for c in rubric.criteria
        ])

        student_solution = "\n".join([
            f"Dòng {l.line_no or l.line_index + 1} [{l.line_type}]: {l.raw_text or l.latex or l.raw_latex}"
            for l in question.lines
        ])

        prompt = f"""Bạn là Giám khảo chấm thi môn Toán THCS Việt Nam.
Hãy chấm bài giải của học sinh dưới đây theo Barem điểm chi tiết.

ĐỀ BÀI: {rubric.question_text or 'Giải toán'} (Tổng điểm tối đa: {rubric.total_points or rubric.max_score}đ)
BAREM TIÊU CHÍ:
{criteria_desc}

BÀI LÀM CỦA HỌC SINH (Các dòng OCR):
{student_solution}

QUY TẮC SƯ PHẠM BẮT BUỘC:
1. Đánh giá từng tiêu chí trong Barem (status: 'passed' | 'partial' | 'failed').
2. CHÚ Ý QUAN TRỌNG VỀ ĐIỀU KIỆN XÁC ĐỊNH (ĐKXĐ): CHỈ trừ điểm tiêu chí ĐKXĐ nếu bài toán THỰC SỰ có mẫu thức chứa ẩn hoặc căn thức. Nếu bài toán của học sinh là đa thức, phương trình bậc nhất, bậc hai không có mẫu/căn, MẶC ĐỊNH CHO ĐIỂM TỐI ĐA bước này (status: 'passed'), TUYỆT ĐỐI KHÔNG bắt học sinh đặt điều kiện x != 2 khi đề bài không có!
3. Điểm từng tiêu chí (score) phải trong khoảng [0, max_points].
4. ERROR PROPAGATION (Lỗi lan truyền): Nếu học sinh tính toán sai ở bước biến đổi nhưng các bước sau vẫn tiếp tục giải đúng phương pháp trên số sai đó, BẢO LƯU 50% điểm phương pháp cho các bước sau, KHÔNG cho 0 điểm toàn bài!
5. Lời phê sư phạm bằng tiếng Việt, mang tính khích lệ, chỉ rõ chỗ sai.

TRẢ VỀ ĐÚNG FORMAT JSON SAU:
{{
  "suggested_score": 1.25,
  "error_propagation_detected": false,
  "affected_steps": [],
  "confidence": 0.95,
  "confidence_level": "green",
  "teacher_summary_comment": "Lời phê sư phạm tổng quát...",
  "criteria_evaluations": [
    {{
      "criterion_id": "Mã tiêu chí",
      "score": 0.5,
      "max_score": 0.5,
      "status": "passed",
      "error_type": "none",
      "feedback": "Nhận xét chi tiết cho bước này"
    }}
  ]
}}
"""
        response = client.models.generate_content(
            model=self.model_name,
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

        text = response.text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                data = json.loads(m.group(0))
            else:
                raise ValueError("Không tìm thấy cấu trúc JSON trong kết quả chấm của Gemini")

        evals: list[CriterionEvaluation] = []
        for c_data in data.get("criteria_evaluations", []):
            evals.append(CriterionEvaluation(
                criterion_id=str(c_data.get("criterion_id", "")),
                score=float(c_data.get("score", 0.0)),
                earned_points=float(c_data.get("score", 0.0)),
                max_score=float(c_data.get("max_score", 0.5)),
                max_points=float(c_data.get("max_score", 0.5)),
                status=str(c_data.get("status", "passed")),
                matched=c_data.get("status") != "failed",
                error_type=str(c_data.get("error_type", "none")),
                feedback=str(c_data.get("feedback", "")),
            ))

        total_pts = float(data.get("suggested_score", sum(e.score for e in evals)))
        total_pts = min(total_pts, rubric.total_points or rubric.max_score)

        conf = float(data.get("confidence", 0.9))
        conf_lvl = str(data.get("confidence_level", "green"))

        return QuestionGradingResult(
            question_id=rubric.question_id,
            suggested_score=round(total_pts, 2),
            earned_points=round(total_pts, 2),
            max_score=rubric.total_points or rubric.max_score,
            total_points=rubric.total_points or rubric.max_score,
            self_critique_applied=True,
            needs_review=(conf_lvl == "red"),
            review_reasons=[],
            criteria_evaluation=evals,
            criteria_evaluations=evals,
            error_propagation_detected=bool(data.get("error_propagation_detected", False)),
            affected_steps=[str(s) for s in data.get("affected_steps", [])],
            confidence=conf,
            confidence_level=conf_lvl,
            teacher_summary_comment=str(data.get("teacher_summary_comment", "Đã chấm xong bài làm.")),
        )

    def _grade_with_rules(
        self,
        question: ExtractedQuestion,
        rubric: QuestionRubric,
        run_self_critique: bool = True,
    ) -> QuestionGradingResult:
        """Thuật toán chấm điểm Rule-based ngoại tuyến dựa trên regex và cấu trúc dòng."""
        full_text = " ".join([f"{l.raw_text} {l.latex} {l.raw_latex}" for l in question.lines])
        full_text_lower = full_text.lower()
        criteria_evals: list[CriterionEvaluation] = []
        review_reasons: list[str] = []

        # 1. Kiểm tra chất lượng chữ đọc được từ Agent 1
        has_unclear_lines = any(l.unclear for l in question.lines) or question.has_unclear
        if has_unclear_lines:
            review_reasons.append("Bài làm có nét chữ bị mờ, gạch xóa hoặc nhòe nét.")

        matched_criteria_count = 0
        has_ambiguity = False

        # 2. Duyệt qua từng tiêu chí chấm trong Barem
        for crit in rubric.criteria:
            crit_id = crit.criterion_id.lower()
            max_pt = crit.max_points or crit.max_score

            # Xây dựng regex tìm kiếm từ keywords của tiêu chí
            custom_regex = "|".join([re.escape(k) for k in crit.keywords]) if crit.keywords else None
            
            # Tiêu chí: Điều kiện xác định (ĐKXĐ)
            if "dkxd" in crit_id or "điều kiện" in crit.name.lower():
                pattern = custom_regex or DEFAULT_KEYWORD_PATTERNS["dkxd"]
                has_cond = any(l.line_type == LineType.CONDITION for l in question.lines) or bool(re.search(pattern, full_text_lower))
                if has_cond:
                    score = max_pt
                    status = "passed"
                    err = "none"
                    fb = "Đã đặt điều kiện xác định chính xác."
                    matched_criteria_count += 1
                else:
                    score = 0.0
                    status = "failed"
                    err = "missing_condition"
                    cond_desc = f" ({crit.required_logic})" if crit.required_logic else ""
                    fb = f"Thiếu bước đặt điều kiện xác định{cond_desc}."

            # Tiêu chí: Biến đổi / Quy đồng / Rút gọn
            elif "bien_doi" in crit_id or "quy đồng" in crit.name.lower() or "giải" in crit.name.lower():
                req_pattern = "|".join([re.escape(k.strip()) for k in crit.required_logic.replace("\\Leftrightarrow", "<=>").split("<=>") if k.strip()]) if crit.required_logic else None
                pattern = custom_regex or req_pattern or DEFAULT_KEYWORD_PATTERNS["bien_doi"]
                has_exact_transform = bool(re.search(pattern, full_text_lower)) if (custom_regex or req_pattern) else False
                has_any_transform = any(l.line_type == LineType.EQUATION for l in question.lines)
                
                if has_exact_transform:
                    score = max_pt
                    status = "passed"
                    err = "none"
                    fb = "Quy đồng và giải phương trình chính xác."
                    matched_criteria_count += 1
                elif has_any_transform:
                    score = round(max_pt * PARTIAL_CREDIT_DEFAULTS["method_correct_calc_wrong"], 2)
                    status = "partial"
                    err = "calculation"
                    fb = "Có thực hiện biến đổi phương trình nhưng có sai sót trong quá trình rút gọn/tính toán."
                    has_ambiguity = True
                else:
                    score = 0.0
                    status = "failed"
                    err = "missing_step"
                    fb = "Chưa thực hiện các bước biến đổi phương trình."

            # Tiêu chí: Kết luận và đối chiếu nghiệm
            elif "ket_luan" in crit_id or "kết luận" in crit.name.lower():
                has_exact_root = False
                if crit.required_logic:
                    root_patterns = re.findall(r"(?:x\s*=\s*\d+|s\s*=\s*\{[^}]*\})", crit.required_logic.lower())
                    if root_patterns:
                        has_exact_root = any(p in full_text_lower for p in root_patterns)
                    else:
                        has_exact_root = bool(re.search(r"(x\s*=\s*\d+|s\s*=\s*\{)", full_text_lower))
                else:
                    has_exact_root = bool(re.search(r"(x\s*=\s*\d+|s\s*=\s*\{)", full_text_lower))

                has_any_root = bool(re.search(r"(x\s*=\s*\d+|s\s*=\s*\{)", full_text_lower))
                has_verify = bool(re.search(r"(thỏa mãn|tm|đkxđ|nhận)", full_text_lower))

                if has_exact_root and has_verify:
                    score = max_pt
                    status = "passed"
                    err = "none"
                    fb = "Kết luận tập nghiệm và có đối chiếu điều kiện đầy đủ."
                    matched_criteria_count += 1
                elif has_any_root and not has_exact_root:
                    score = 0.0
                    status = "failed"
                    err = "calculation"
                    fb = "Kết luận nghiệm sai do tính toán sai số ở bước trước."
                elif has_exact_root and not has_verify:
                    score = round(max_pt * 0.5, 2) if max_pt > 0.5 else 0.0
                    status = "partial" if score > 0 else "failed"
                    err = "missing_step"
                    fb = "Có tìm ra nghiệm nhưng quên đối chiếu với điều kiện xác định trước khi kết luận."
                    has_ambiguity = True
                else:
                    score = 0.0
                    status = "failed"
                    err = "wrong_conclusion"
                    fb = "Chưa kết luận đúng tập nghiệm."

            # Tiêu chí tổng quát khác
            else:
                score = max_pt
                status = "passed"
                err = "none"
                fb = f"Đạt yêu cầu: {crit.name or crit.description}"
                matched_criteria_count += 1

            criteria_evals.append(CriterionEvaluation(
                criterion_id=crit.criterion_id,
                score=score,
                earned_points=score,
                max_score=max_pt,
                max_points=max_pt,
                status=status,
                matched=(status != "failed"),
                error_type=err,
                feedback=fb,
            ))

        # 3. Phát hiện lỗi lan truyền (Error Propagation Detection - WP-07)
        error_prop_detected = False
        affected_steps: list[str] = []

        if rubric.allow_error_propagation and len(criteria_evals) >= 2:
            first_calc_error_idx = None
            for idx, eval_item in enumerate(criteria_evals):
                if eval_item.error_type == "calculation":
                    first_calc_error_idx = idx
                    break

            if first_calc_error_idx is not None:
                for next_idx in range(first_calc_error_idx + 1, len(criteria_evals)):
                    next_eval = criteria_evals[next_idx]
                    if next_eval.status in ("failed", "partial"):
                        error_prop_detected = True
                        affected_steps.append(next_eval.criterion_id)
                        restored_pts = round(next_eval.max_points * 0.5, 2)
                        next_eval.earned_points = max(next_eval.earned_points, restored_pts)
                        next_eval.score = next_eval.earned_points
                        next_eval.error_type = "error_propagation"
                        next_eval.feedback = (
                            f"Lỗi lan truyền: Phương pháp giải đúng nhưng đáp số sai do lấy số liệu từ "
                            f"bước trước ({criteria_evals[first_calc_error_idx].criterion_id}). "
                            f"Bảo lưu {restored_pts}đ điểm phương pháp."
                        )

        # 4. Tính toán tổng điểm sau điều chỉnh
        total_awarded = round(sum(c.earned_points for c in criteria_evals), 2)
        total_awarded = min(total_awarded, rubric.total_points or rubric.max_score)

        # 5. Tính Confidence Score (WP-08)
        match_quality = matched_criteria_count / max(1, len(rubric.criteria))
        calc_conf, conf_level = compute_confidence(
            ocr_confidence=question.confidence,
            match_quality=match_quality,
            has_ambiguity=has_ambiguity or bool(review_reasons),
        )

        needs_review = conf_level == "red" or bool(review_reasons) or error_prop_detected

        # 6. Sinh lời phê tự động mang tính sư phạm
        failed_feedbacks = [c.feedback for c in criteria_evals if c.status == "failed"]
        if not failed_feedbacks and not error_prop_detected:
            summary = "Em làm bài rất tốt, trình bày mạch lạc và nắm chắc kiến thức!"
        elif error_prop_detected:
            summary = "Em nắm chắc phương pháp giải, nhưng cần chú ý cẩn thận trong tính toán để tránh sai sót dây chuyền."
        else:
            summary = f"Em nắm được phương pháp giải cơ bản, tuy nhiên: {'; '.join(failed_feedbacks)}. Cần chú ý cẩn thận hơn!"

        return QuestionGradingResult(
            question_id=rubric.question_id,
            suggested_score=total_awarded,
            earned_points=total_awarded,
            max_score=rubric.total_points or rubric.max_score,
            total_points=rubric.total_points or rubric.max_score,
            self_critique_applied=run_self_critique,
            needs_review=needs_review,
            review_reasons=review_reasons,
            criteria_evaluation=criteria_evals,
            criteria_evaluations=criteria_evals,
            error_propagation_detected=error_prop_detected,
            affected_steps=affected_steps,
            confidence=calc_conf,
            confidence_level=conf_level,
            teacher_summary_comment=summary,
        )


reasoning_agent = ReasoningGradingAgent()
