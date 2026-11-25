from __future__ import annotations

import json
import re
from backend.app.models.schemas import (
    ExtractedQuestion,
    LineType,
    QuestionRubric,
    RubricCriterion,
    QuestionGradingResult,
    CriterionEvaluation,
    MathVerificationSummary,
)
from backend.app.core.config import settings
from backend.app.services.math_verifier import verify_question_math


DEFAULT_KEYWORD_PATTERNS: dict[str, str] = {
    "dkxd": r"(đkxđ|điều kiện|đk|xác định|mẫu.*khác.*0)",
    "bien_doi": r"(biến đổi|rút gọn|quy đồng|⇔|=>|chuyển vế|nhân|chia)",
    "giai_pt": r"(giải|nghiệm|x\s*=\s*|⇒|tương đương)",
    "ket_luan": r"(kết luận|vậy|đáp số|kl|kết quả|s\s*=|tập nghiệm)",
    "doi_chieu": r"(đối chiếu|thỏa mãn|tm|thay.*vào|kiểm tra|loại)",
}

PARTIAL_CREDIT_DEFAULTS = {
    "method_correct_calc_wrong": 0.5,
    "incomplete_step": 0.3,
    "correct_but_no_explanation": 0.7,
}


def compute_confidence(
    ocr_confidence: float,
    match_quality: float,
    has_ambiguity: bool = False,
) -> tuple[float, str]:
    """
    Tính điểm tự tin (Confidence Scoring):
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


def _active_lines_of(question: ExtractedQuestion):
    """Chỉ lấy các dòng thực sự được học sinh giữ lại (bỏ qua dòng gạch xóa/scratch)."""
    return [l for l in question.lines if not l.is_strikethrough and l.line_type != LineType.SCRATCH]


def _extract_json_dict(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise ValueError("Không tìm thấy cấu trúc JSON hợp lệ từ phản hồi chấm bài.")


class ReasoningGradingAgent:
    """Agent 2: Chấm điểm theo Barem từng bước + Kiểm chứng SymPy + Error Propagation."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.openai_api_key = settings.OPENAI_API_KEY
        self.model_name = settings.DEFAULT_REASONING_MODEL
        self.openai_model = settings.DEFAULT_OPENAI_MODEL

    @property
    def has_openai_live(self) -> bool:
        return bool(self.openai_api_key and not self.openai_api_key.startswith("mock_"))

    @property
    def has_gemini_live(self) -> bool:
        return bool(self.api_key and not self.api_key.startswith("mock_"))

    @property
    def is_live(self) -> bool:
        return self.has_openai_live or self.has_gemini_live

    def _active_provider(self) -> str:
        pref = (settings.AI_PROVIDER or "openai").lower()
        if pref == "gemini" and self.has_gemini_live:
            return "gemini"
        if pref == "openai" and self.has_openai_live:
            return "openai"
        if self.has_openai_live:
            return "openai"
        if self.has_gemini_live:
            return "gemini"
        return "mock"

    def grade_question(
        self,
        question: ExtractedQuestion,
        rubric: QuestionRubric,
        run_self_critique: bool = True,
        use_llm: bool = False,
        math_verification: MathVerificationSummary | None = None,
    ) -> QuestionGradingResult:
        """
        Chấm chi tiết 1 câu hỏi theo Barem tiêu chí.
        Hỗ trợ chế độ AUTO (Zero-Shot), Rule-based tất định (kèm SymPy), và LLM (OpenAI/Gemini).
        """
        if rubric.question_id == "AUTO" or not rubric.criteria or rubric.question_text == "AUTO":
            _, auto_res = self.grade_auto_rubric(question, math_verification=math_verification)
            return auto_res

        has_standard_rules = any(
            any(
                k in c.criterion_id.lower() or k in c.name.lower()
                for k in ("dkxd", "điều kiện", "bien_doi", "quy đồng", "giải", "ket_luan", "kết luận")
            )
            for c in rubric.criteria
        )

        if use_llm or not has_standard_rules:
            provider = self._active_provider()
            if provider == "openai":
                try:
                    return self._call_openai_reasoning_grader(question, rubric, math_verification)
                except Exception as e:
                    print(f"[Agent 2 Warning] OpenAI Reasoning failed: {e}. Falling back to Rule Engine.")
            elif provider == "gemini":
                try:
                    return self._call_gemini_reasoning_grader(question, rubric, math_verification)
                except Exception as e:
                    print(f"[Agent 2 Warning] Gemini Reasoning failed: {e}. Falling back to Rule Engine.")

        return self._grade_with_rules(question, rubric, run_self_critique, math_verification)

    def grade_auto_rubric(
        self,
        question: ExtractedQuestion | list[ExtractedQuestion],
        math_verification: MathVerificationSummary | None = None,
    ) -> tuple[str, QuestionGradingResult]:
        """
        Tự động nhận diện Đề bài từ bài viết tay và chấm linh hoạt (Auto-Rubric Zero-Shot).
        Hỗ trợ đầy đủ cả OpenAI và Google Gemini (theo settings.AI_PROVIDER).
        """
        provider = self._active_provider()
        if provider == "openai":
            return self._call_openai_auto_reasoning_grader(question, math_verification)
        if provider == "gemini":
            return self._call_gemini_auto_reasoning_grader(question, math_verification)

        # Fallback offline khi chưa cài API key
        primary_q = question[0] if isinstance(question, list) else question
        rubric = QuestionRubric(
            question_id=primary_q.question_id,
            question_text="Giải phương trình",
            max_score=10.0,
            total_points=10.0,
            criteria=[
                RubricCriterion(criterion_id="C1_BIEN_DOI", name="Biến đổi và lời giải", max_score=7.0),
                RubricCriterion(criterion_id="C2_KET_LUAN", name="Kết luận", max_score=3.0),
            ],
        )
        return "Bài toán Toán học (Chế độ Offline)", self._grade_with_rules(
            primary_q, rubric, math_verification=math_verification
        )

    def _build_auto_rubric_prompt(
        self,
        q_list: list[ExtractedQuestion],
        math_verification: MathVerificationSummary | None = None,
    ) -> str:
        solution_lines: list[str] = []
        for q_item in q_list:
            if q_item.question_text:
                solution_lines.append(
                    f"=== {q_item.question_id}: {q_item.question_text} (Khối: {q_item.grade_level or 'THCS'}) ==="
                )
            for l in _active_lines_of(q_item):
                solution_lines.append(
                    f"Trang {l.page_no} - Dòng {l.line_no or l.line_index + 1} [{l.line_type.value}]: {l.raw_text or l.latex or l.raw_latex}"
                )

        student_solution = "\n".join(solution_lines) or "(Học sinh để trống bài làm)"
        sympy_block = ""
        if math_verification and math_verification.equations_checked > 0:
            sympy_block = f"\nKẾT QUẢ KIỂM CHỨNG TOÁN HỌC SYMPY (TẤT ĐỊNH):\n- {math_verification.summary}\n"
            if math_verification.issues:
                sympy_block += "\n".join(f"- CẢNH BÁO: {iss}" for iss in math_verification.issues) + "\n"

        return f"""Bạn là Giám khảo chấm thi môn Toán THCS/THPT Việt Nam.
BÀI LÀM CỦA HỌC SINH (Đã loại bỏ các dòng học sinh gạch bỏ):
{student_solution}
{sympy_block}
NHIỆM VỤ SƯ PHẠM (ZERO-SHOT PRECISION GRADING):
1. Tự động nhận diện Đề bài và Khối lớp thực tế từ bài làm của học sinh.
2. Xây dựng Barem điểm (tổng 10.0đ) DỰA 100% vào bài toán và phương pháp thực tế học sinh trình bày. Không tự bịa cách giải học sinh không viết.
3. Đối chiếu từng bước tính toán với kết quả kiểm chứng SymPy (nếu có). Nếu phát hiện sai dấu/sai số ở bước nào, chỉ rõ dòng đó và trừ đúng điểm bước đó.
4. CHỈ yêu cầu ĐKXĐ nếu bài toán thực sự có mẫu thức chứa ẩn hoặc căn thức.
5. ERROR PROPAGATION: Nếu bước trước sai số học nhẹ nhưng các bước sau vẫn dùng đúng phương pháp, bảo lưu 50% điểm phương pháp cho các bước sau và đặt `"error_propagation_detected": true`.
6. Mọi công thức Toán trong text đặt trong cặp dấu `$...$` để KaTeX hiển thị đẹp.

TRẢ VỀ JSON HỢP LỆ:
{{
  "detected_exam_title": "<Tên đề bài và khối lớp>",
  "grade_level": "<Khối lớp>",
  "total_points": 10.0,
  "suggested_score": 10.0,
  "error_propagation_detected": false,
  "affected_steps": [],
  "confidence": 0.95,
  "confidence_level": "green",
  "teacher_summary_comment": "<Lời phê sư phạm chi tiết, cụ thể, khích lệ>",
  "criteria_evaluations": [
    {{
      "criterion_id": "T1",
      "name": "<Tên bước chấm>",
      "score": 5.0,
      "max_score": 5.0,
      "status": "passed",
      "error_type": "none",
      "feedback": "<Nhận xét chi tiết>"
    }}
  ]
}}
"""

    def _parse_auto_rubric_response(
        self,
        data: dict,
        primary_qid: str,
    ) -> tuple[str, QuestionGradingResult]:
        evals: list[CriterionEvaluation] = []
        for idx, c_data in enumerate(data.get("criteria_evaluations", [])):
            max_c = max(0.0, float(c_data.get("max_score", c_data.get("score", 5.0))))
            earned_c = max(0.0, min(max_c, float(c_data.get("score", 0.0))))
            evals.append(
                CriterionEvaluation(
                    criterion_id=str(c_data.get("criterion_id", f"C{idx + 1}")),
                    name=str(c_data.get("name", f"Tiêu chí {idx + 1}")),
                    score=earned_c,
                    earned_points=earned_c,
                    max_score=max_c,
                    max_points=max_c,
                    status=str(c_data.get("status", "passed")),
                    matched=c_data.get("status") != "failed",
                    error_type=str(c_data.get("error_type", "none")),
                    feedback=str(c_data.get("feedback", "")),
                )
            )

        total_max = max(0.1, float(data.get("total_points", 10.0)))
        total_pts = max(0.0, min(total_max, float(data.get("suggested_score", sum(e.score for e in evals)))))
        exam_title = str(data.get("detected_exam_title", "Bài thi Toán học"))
        conf = max(0.0, min(1.0, float(data.get("confidence", 0.92))))
        conf_lvl = "green" if conf >= 0.90 else ("yellow" if conf >= 0.70 else "red")

        q_res = QuestionGradingResult(
            question_id=primary_qid,
            suggested_score=round(total_pts, 2),
            earned_points=round(total_pts, 2),
            max_score=total_max,
            total_points=total_max,
            self_critique_applied=True,
            needs_review=(conf_lvl == "red" or bool(data.get("error_propagation_detected", False))),
            review_reasons=[],
            criteria_evaluation=evals,
            criteria_evaluations=evals,
            error_propagation_detected=bool(data.get("error_propagation_detected", False)),
            affected_steps=[str(s) for s in data.get("affected_steps", [])],
            confidence=round(conf, 2),
            confidence_level=conf_lvl,
            teacher_summary_comment=str(data.get("teacher_summary_comment", "Đã chấm xong bài làm.")),
        )
        return exam_title, q_res

    def _call_openai_auto_reasoning_grader(
        self,
        question: ExtractedQuestion | list[ExtractedQuestion],
        math_verification: MathVerificationSummary | None = None,
    ) -> tuple[str, QuestionGradingResult]:
        from openai import OpenAI

        client = OpenAI(api_key=self.openai_api_key, timeout=30.0)
        q_list = question if isinstance(question, list) else [question]
        primary_qid = q_list[0].question_id if q_list else "Q1"
        prompt = self._build_auto_rubric_prompt(q_list, math_verification)

        response = client.chat.completions.create(
            model=self.openai_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        data = _extract_json_dict(response.choices[0].message.content or "")
        return self._parse_auto_rubric_response(data, primary_qid)

    def _call_gemini_auto_reasoning_grader(
        self,
        question: ExtractedQuestion | list[ExtractedQuestion],
        math_verification: MathVerificationSummary | None = None,
    ) -> tuple[str, QuestionGradingResult]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        q_list = question if isinstance(question, list) else [question]
        primary_qid = q_list[0].question_id if q_list else "Q1"
        prompt = self._build_auto_rubric_prompt(q_list, math_verification)

        response = client.models.generate_content(
            model=self.model_name,
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )
        data = _extract_json_dict(response.text or "")
        return self._parse_auto_rubric_response(data, primary_qid)

    def _build_rubric_prompt(
        self,
        question: ExtractedQuestion,
        rubric: QuestionRubric,
        math_verification: MathVerificationSummary | None = None,
    ) -> str:
        criteria_desc = "\n".join(
            f"- Mã {c.criterion_id} ({c.name}): Tối đa {c.max_points or c.max_score}đ. Yêu cầu: {c.required_logic or c.description}"
            for c in rubric.criteria
        )
        student_solution = "\n".join(
            f"Trang {l.page_no} - Dòng {l.line_no or l.line_index + 1} [{l.line_type.value}]: {l.raw_text or l.latex or l.raw_latex}"
            for l in _active_lines_of(question)
        )
        sympy_block = ""
        if math_verification and math_verification.equations_checked > 0:
            sympy_block = f"\nKIỂM CHỨNG TOÁN HỌC SYMPY:\n- {math_verification.summary}\n"

        return f"""Bạn là Giám khảo chấm thi môn Toán THCS/THPT Việt Nam.
ĐỀ BÀI: {rubric.question_text or 'Giải toán'} (Tổng điểm tối đa: {rubric.total_points or rubric.max_score}đ)
BAREM TIÊU CHÍ:
{criteria_desc}

BÀI LÀM CỦA HỌC SINH (Đã lọc bỏ dòng gạch xóa):
{student_solution}
{sympy_block}
QUY TẮC SƯ PHẠM BẮT BUỘC:
1. Đánh giá từng tiêu chí trong Barem (`passed` | `partial` | `failed`), điểm từng tiêu chí nằm trong `[0, max_points]`.
2. CHỈ trừ điểm tiêu chí ĐKXĐ nếu bài toán thực sự có mẫu thức chứa ẩn hoặc căn thức.
3. ERROR PROPAGATION: Nếu sai tính toán ở bước trên nhưng bước sau vẫn đúng phương pháp, bảo lưu 50% điểm phương pháp cho các bước sau.
4. Lời phê sư phạm bằng tiếng Việt, khích lệ, chỉ rõ chỗ sai.

TRẢ VỀ JSON HỢP LỆ:
{{
  "suggested_score": 1.25,
  "error_propagation_detected": false,
  "affected_steps": [],
  "confidence": 0.95,
  "confidence_level": "green",
  "teacher_summary_comment": "...",
  "criteria_evaluations": [
    {{
      "criterion_id": "...",
      "score": 0.5,
      "max_score": 0.5,
      "status": "passed",
      "error_type": "none",
      "feedback": "..."
    }}
  ]
}}
"""

    def _parse_rubric_response(self, data: dict, rubric: QuestionRubric) -> QuestionGradingResult:
        evals: list[CriterionEvaluation] = []
        for c_data in data.get("criteria_evaluations", []):
            max_c = max(0.0, float(c_data.get("max_score", 0.5)))
            earned_c = max(0.0, min(max_c, float(c_data.get("score", 0.0))))
            evals.append(
                CriterionEvaluation(
                    criterion_id=str(c_data.get("criterion_id", "")),
                    score=earned_c,
                    earned_points=earned_c,
                    max_score=max_c,
                    max_points=max_c,
                    status=str(c_data.get("status", "passed")),
                    matched=c_data.get("status") != "failed",
                    error_type=str(c_data.get("error_type", "none")),
                    feedback=str(c_data.get("feedback", "")),
                )
            )
        max_total = rubric.total_points or rubric.max_score
        total_pts = max(0.0, min(max_total, float(data.get("suggested_score", sum(e.score for e in evals)))))
        conf = max(0.0, min(1.0, float(data.get("confidence", 0.9))))
        conf_lvl = "green" if conf >= 0.90 else ("yellow" if conf >= 0.70 else "red")

        return QuestionGradingResult(
            question_id=rubric.question_id,
            suggested_score=round(total_pts, 2),
            earned_points=round(total_pts, 2),
            max_score=max_total,
            total_points=max_total,
            self_critique_applied=True,
            needs_review=(conf_lvl == "red"),
            review_reasons=[],
            criteria_evaluation=evals,
            criteria_evaluations=evals,
            error_propagation_detected=bool(data.get("error_propagation_detected", False)),
            affected_steps=[str(s) for s in data.get("affected_steps", [])],
            confidence=round(conf, 2),
            confidence_level=conf_lvl,
            teacher_summary_comment=str(data.get("teacher_summary_comment", "Đã chấm xong bài làm.")),
        )

    def _call_openai_reasoning_grader(
        self,
        question: ExtractedQuestion,
        rubric: QuestionRubric,
        math_verification: MathVerificationSummary | None = None,
    ) -> QuestionGradingResult:
        from openai import OpenAI

        client = OpenAI(api_key=self.openai_api_key, timeout=30.0)
        prompt = self._build_rubric_prompt(question, rubric, math_verification)
        response = client.chat.completions.create(
            model=self.openai_model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        data = _extract_json_dict(response.choices[0].message.content or "")
        return self._parse_rubric_response(data, rubric)

    def _call_gemini_reasoning_grader(
        self,
        question: ExtractedQuestion,
        rubric: QuestionRubric,
        math_verification: MathVerificationSummary | None = None,
    ) -> QuestionGradingResult:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        prompt = self._build_rubric_prompt(question, rubric, math_verification)
        response = client.models.generate_content(
            model=self.model_name,
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )
        data = _extract_json_dict(response.text or "")
        return self._parse_rubric_response(data, rubric)

    def _grade_with_rules(
        self,
        question: ExtractedQuestion,
        rubric: QuestionRubric,
        run_self_critique: bool = True,
        math_verification: MathVerificationSummary | None = None,
    ) -> QuestionGradingResult:
        """Thuật toán chấm điểm Rule-based ngoại tuyến (lọc bỏ dòng gạch xóa + kết hợp SymPy)."""
        active_lines = _active_lines_of(question)
        full_text = " ".join([f"{l.raw_text} {l.latex} {l.raw_latex}" for l in active_lines])
        full_text_lower = full_text.lower()
        criteria_evals: list[CriterionEvaluation] = []
        review_reasons: list[str] = []

        mv = math_verification if math_verification is not None else verify_question_math(question)
        if mv.invalid_transitions > 0:
            review_reasons.extend(mv.issues[:2])

        has_unclear_lines = any(l.unclear for l in active_lines) or question.has_unclear
        if has_unclear_lines:
            review_reasons.append("Bài làm có nét chữ bị mờ, gạch xóa hoặc nhòe nét.")

        matched_criteria_count = 0
        has_ambiguity = False

        for crit in rubric.criteria:
            crit_id = crit.criterion_id.lower()
            max_pt = crit.max_points or crit.max_score
            custom_regex = "|".join([re.escape(k) for k in crit.keywords]) if crit.keywords else None

            if "dkxd" in crit_id or "điều kiện" in crit.name.lower():
                pattern = custom_regex or DEFAULT_KEYWORD_PATTERNS["dkxd"]
                has_cond = any(l.line_type == LineType.CONDITION for l in active_lines) or bool(
                    re.search(pattern, full_text_lower)
                )
                if has_cond:
                    score, status, err = max_pt, "passed", "none"
                    fb = "Đã đặt điều kiện xác định chính xác."
                    matched_criteria_count += 1
                else:
                    score, status, err = 0.0, "failed", "missing_condition"
                    cond_desc = f" ({crit.required_logic})" if crit.required_logic else ""
                    fb = f"Thiếu bước đặt điều kiện xác định{cond_desc}."

            elif "bien_doi" in crit_id or "quy đồng" in crit.name.lower() or "giải" in crit.name.lower():
                req_pattern = (
                    "|".join(
                        [
                            re.escape(k.strip())
                            for k in crit.required_logic.replace("\\Leftrightarrow", "<=>").split("<=>")
                            if k.strip()
                        ]
                    )
                    if crit.required_logic
                    else None
                )
                pattern = custom_regex or req_pattern or DEFAULT_KEYWORD_PATTERNS["bien_doi"]
                has_exact_transform = bool(re.search(pattern, full_text_lower)) if (custom_regex or req_pattern) else False
                has_any_transform = any(l.line_type == LineType.EQUATION for l in active_lines)

                if has_exact_transform and mv.invalid_transitions == 0:
                    score, status, err = max_pt, "passed", "none"
                    fb = "Quy đồng và giải phương trình chính xác."
                    matched_criteria_count += 1
                elif has_any_transform:
                    score = round(max_pt * PARTIAL_CREDIT_DEFAULTS["method_correct_calc_wrong"], 2)
                    status, err = "partial", "calculation"
                    fb = "Có thực hiện biến đổi phương trình nhưng có sai sót trong quá trình rút gọn/tính toán."
                    has_ambiguity = True
                else:
                    score, status, err = 0.0, "failed", "missing_step"
                    fb = "Chưa thực hiện các bước biến đổi phương trình."

            elif "ket_luan" in crit_id or "kết luận" in crit.name.lower():
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
                    score, status, err = max_pt, "passed", "none"
                    fb = "Kết luận tập nghiệm và có đối chiếu điều kiện đầy đủ."
                    matched_criteria_count += 1
                elif has_any_root and not has_exact_root:
                    score, status, err = 0.0, "failed", "calculation"
                    fb = "Kết luận nghiệm sai do tính toán sai số ở bước trước."
                elif has_exact_root and not has_verify:
                    score = round(max_pt * 0.5, 2) if max_pt > 0.5 else 0.0
                    status = "partial" if score > 0 else "failed"
                    err = "missing_step"
                    fb = "Có tìm ra nghiệm nhưng quên đối chiếu với điều kiện xác định trước khi kết luận."
                    has_ambiguity = True
                else:
                    score, status, err = 0.0, "failed", "wrong_conclusion"
                    fb = "Chưa kết luận đúng tập nghiệm."

            else:
                score, status, err = max_pt, "passed", "none"
                fb = f"Đạt yêu cầu: {crit.name or crit.description}"
                matched_criteria_count += 1

            criteria_evals.append(
                CriterionEvaluation(
                    criterion_id=crit.criterion_id,
                    name=crit.name,
                    score=score,
                    earned_points=score,
                    max_score=max_pt,
                    max_points=max_pt,
                    status=status,
                    matched=(status != "failed"),
                    error_type=err,
                    feedback=fb,
                )
            )

        # Error Propagation Detection
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

        total_awarded = round(sum(c.earned_points for c in criteria_evals), 2)
        total_awarded = min(total_awarded, rubric.total_points or rubric.max_score)

        match_quality = matched_criteria_count / max(1, len(rubric.criteria))
        calc_conf, conf_level = compute_confidence(
            ocr_confidence=question.confidence,
            match_quality=match_quality,
            has_ambiguity=has_ambiguity or bool(review_reasons),
        )

        needs_review = conf_level == "red" or bool(review_reasons) or error_prop_detected

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
