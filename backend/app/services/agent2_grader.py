from __future__ import annotations

import json
import re
from backend.app.models.schemas import (
    ExamRubric,
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


def match_student_questions_to_rubric(
    student_qs: list[ExtractedQuestion],
    rubric_qs: list[QuestionRubric],
) -> dict[str, ExtractedQuestion]:
    """
    Khớp thông minh danh sách câu hỏi học sinh viết (`a`, `b`, `c`, `Câu 2a`, `Bài 3`...)
    vào danh sách câu hỏi trong Biểu điểm chuẩn (`Câu 1`, `Câu 2a`, `Câu 2b`, `Câu 3`...).
    Trả về dict[rubric_question_id -> ExtractedQuestion].
    """
    from backend.app.services.folder_reassembler import normalize_question_id

    matched: dict[str, ExtractedQuestion] = {}
    used_student_indices: set[int] = set()

    # Tầng 1: Khớp chính xác theo nhãn chuẩn hóa (vd "Bài 2a" == "Câu 2a")
    student_canon = [(idx, normalize_question_id(q.question_id)[1], q) for idx, q in enumerate(student_qs)]
    for rq in rubric_qs:
        _, r_lbl = normalize_question_id(rq.question_id)
        for idx, s_lbl, sq in student_canon:
            if idx in used_student_indices:
                continue
            if s_lbl == r_lbl or sq.question_id.strip().lower() == rq.question_id.strip().lower():
                matched[rq.question_id] = sq
                used_student_indices.add(idx)
                break

    # Tầng 2: Khớp viết tắt chữ cái đơn (vd học sinh ghi "a", "b", "c" cho "Câu 2a", "Câu 2b", "Câu 2c")
    unmatched_rubrics = [rq for rq in rubric_qs if rq.question_id not in matched]
    for idx, s_lbl, sq in student_canon:
        if idx in used_student_indices:
            continue
        m_letter = re.fullmatch(r"(?:câu|cau|bài|bai)?\s*([a-z])", sq.question_id.strip(), re.IGNORECASE)
        if not m_letter:
            continue
        sub = m_letter.group(1).lower()
        candidates = [
            rq
            for rq in unmatched_rubrics
            if rq.question_id not in matched
            and re.search(rf"\d+\s*{re.escape(sub)}$", rq.question_id.strip(), re.IGNORECASE)
        ]
        if len(candidates) == 1:
            matched[candidates[0].question_id] = sq
            used_student_indices.add(idx)
        elif len(candidates) > 1:
            s_nums = set(re.findall(r"\d+", " ".join(l.raw_text or l.latex for l in sq.lines)))
            best_rq = max(
                candidates,
                key=lambda rq: len(s_nums.intersection(set(re.findall(r"\d+", rq.question_text)))),
            )
            matched[best_rq.question_id] = sq
            used_student_indices.add(idx)

    # Tầng 3: Gom các ý con của học sinh (vd Câu 2a, 2b, 2c) vào câu cha trong Rubric (vd Câu 2)
    for rq in rubric_qs:
        if rq.question_id in matched:
            continue
        m_parent = re.search(r"(\d+)$", rq.question_id.strip())
        if not m_parent:
            continue
        p_num = m_parent.group(1)
        child_items = [
            (idx, sq)
            for idx, s_lbl, sq in student_canon
            if idx not in used_student_indices
            and re.search(rf"(?:câu|bài|q)?\s*{re.escape(p_num)}\s*[a-z]$", s_lbl, re.IGNORECASE)
        ]
        if child_items:
            merged_lines = []
            merged_srcs: list[str] = []
            for idx, sq in child_items:
                used_student_indices.add(idx)
                for l in sq.lines:
                    nl = l.model_copy(deep=True)
                    nl.line_no = len(merged_lines) + 1
                    nl.line_index = len(merged_lines)
                    merged_lines.append(nl)
                for sf in sq.source_files:
                    if sf not in merged_srcs:
                        merged_srcs.append(sf)
            matched[rq.question_id] = ExtractedQuestion(
                question_id=rq.question_id,
                question_text=rq.question_text,
                source_files=merged_srcs,
                lines=merged_lines,
            )

    # Tầng 4: Khớp ngược từ 1 câu cha của học sinh (vd OCR gom chung "Câu 4") sang các ý con chưa khớp trong Rubric ("Câu 4a", "Câu 4b", "Câu 4c")
    sub_header_re = re.compile(r"^\s*(?:ý\s*|câu\s*\d+\s*|bài\s*\d+\s*)?([a-z])\s*[).:\-]", re.IGNORECASE)
    for idx, s_lbl, sq in student_canon:
        if idx in used_student_indices:
            continue
        m_p = re.search(r"(?:câu|bài|q)?\s*(\d+)$", s_lbl, re.IGNORECASE)
        if not m_p:
            continue
        p_num = m_p.group(1)
        child_rubrics: list[tuple[str, QuestionRubric]] = []
        for rq in rubric_qs:
            if rq.question_id in matched:
                continue
            m_c = re.search(rf"(?:câu|bài|q)?\s*{re.escape(p_num)}\s*([a-z])$", rq.question_id.strip(), re.IGNORECASE)
            if m_c:
                child_rubrics.append((m_c.group(1).lower(), rq))
        if not child_rubrics:
            continue

        # Chia các dòng trong câu cha theo tiêu đề từng ý con (a), b), c)...) nếu có
        buckets: dict[str, list] = {}
        leading_lines: list = []
        active_sub: str | None = None
        for ln in sq.lines:
            txt_head = (ln.raw_text or ln.latex or "").strip()
            m_sh = sub_header_re.match(txt_head)
            if m_sh and any(sl == m_sh.group(1).lower() for sl, _ in child_rubrics):
                active_sub = m_sh.group(1).lower()
            if active_sub is None:
                leading_lines.append(ln)
            else:
                buckets.setdefault(active_sub, []).append(ln)

        if buckets:
            first_sub = next(iter(buckets))
            if leading_lines:
                buckets[first_sub] = leading_lines + buckets[first_sub]
            for sub_letter, rq in child_rubrics:
                sub_lines = buckets.get(sub_letter, [])
                if not sub_lines:
                    continue
                renum = []
                for li, l in enumerate(sub_lines):
                    nl = l.model_copy(deep=True)
                    nl.line_index = li
                    nl.line_no = li + 1
                    renum.append(nl)
                matched[rq.question_id] = ExtractedQuestion(
                    question_id=rq.question_id,
                    question_text=rq.question_text,
                    source_files=list(sq.source_files),
                    lines=renum,
                )
        else:
            # Không tách rõ đầu mục con trong dòng -> truyền toàn bộ bài làm câu cha vào từng ý con của Rubric
            for _, rq in child_rubrics:
                matched[rq.question_id] = ExtractedQuestion(
                    question_id=rq.question_id,
                    question_text=rq.question_text,
                    source_files=list(sq.source_files),
                    lines=[l.model_copy(deep=True) for l in sq.lines],
                )
        used_student_indices.add(idx)

    return matched


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


from backend.app.services.agent1_ocr import _repair_json_backslashes


def _extract_json_dict(text: str) -> dict | list:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        repaired = _repair_json_backslashes(cleaned)
        try:
            return json.loads(repaired, strict=False)
        except json.JSONDecodeError:
            m = re.search(r"(\{.*\}|\[.*\])", repaired, re.DOTALL)
            if m:
                return json.loads(m.group(0), strict=False)
            raise ValueError("Không tìm thấy cấu trúc JSON hợp lệ từ phản hồi chấm bài.")


_SCORE_EPS = 0.01


def _safe_float(val, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _unique_evals(res: QuestionGradingResult) -> list[CriterionEvaluation]:
    """Lấy `criteria_evaluations` làm nguồn DUY NHẤT và cho `criteria_evaluation` trỏ cùng list.

    Khi tạo mới, 2 trường cùng trỏ 1 list; nhưng bản ghi đọc lại từ SQLite/JSON có 2 list RIÊNG cùng nội dung —
    gộp cả hai sẽ đếm trùng tiêu chí (đo 2026-11-27: hs27 Câu 1d ra 0.74 thay vì 0.75)."""
    evals = res.criteria_evaluations or res.criteria_evaluation or []
    res.criteria_evaluations = evals
    res.criteria_evaluation = evals
    return list(evals)


def fit_result_to_max(res: QuestionGradingResult, new_max: float | None = None) -> QuestionGradingResult:
    """Đưa kết quả 1 câu về ĐÚNG thang điểm (v0.7.1 — planning/04_2026-11-27_grader-score-clamp).

    1. Nếu tiêu chí được chấm theo thang lớn hơn điểm tối đa của câu (vd LLM chấm lại theo thang 10 cho câu 1đ)
       ⇒ quy đổi TỈ LỆ từng tiêu chí (điểm đạt & điểm tối đa) về thang của câu.
    2. Điểm câu = Σ điểm tiêu chí (phần thầy cô kiểm được), KHÔNG dùng `suggested_score` tự khai của LLM.
    3. Kẹp điểm câu trong [0, điểm tối đa].
    Không có tiêu chí ⇒ quy đổi theo tỉ lệ thang cũ/thang mới rồi kẹp.
    """
    old_total = float(res.total_points or res.max_score or 0.0)
    target = float(new_max if new_max is not None else old_total)
    evals = _unique_evals(res)

    if evals and target > 0:
        crit_max = sum(float(e.max_points or e.max_score or 0.0) for e in evals)
        scale_from = crit_max if crit_max > 0 else old_total
        if scale_from > target + _SCORE_EPS:
            k = target / scale_from
            for e in evals:
                m = round(float(e.max_points or e.max_score or 0.0) * k, 2)
                s = round(min(m, float(e.earned_points or e.score or 0.0) * k), 2)
                e.max_score = e.max_points = m
                e.score = e.earned_points = s
        earned = sum(float(e.earned_points or e.score or 0.0) for e in evals)
    else:
        earned = float(res.suggested_score or res.earned_points or 0.0)
        if target > 0 and old_total > target + _SCORE_EPS:
            earned *= target / old_total

    if target > 0:
        res.max_score = res.total_points = round(target, 2)
        earned = min(earned, target)
    earned = round(max(0.0, earned), 2)
    res.suggested_score = res.earned_points = earned
    return res


class ReasoningGradingAgent:
    """Agent 2: Chấm điểm theo Barem từng bước + Kiểm chứng SymPy + Error Propagation."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.openai_api_key = settings.OPENAI_API_KEY
        self.openai_base_url: str = ""
        self.openai_extra_headers: dict[str, str] = {}
        self.active_provider_name: str = ""
        self.active_account_id: str = ""
        self.gemini_oauth_credentials: dict | None = None
        self.model_name = settings.DEFAULT_REASONING_MODEL
        self.openai_model = settings.DEFAULT_OPENAI_MODEL
        self.last_usage: dict = {
            "provider": "rule",
            "model": "rule-based",
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 0,
        }

    def reset_last_usage(self) -> None:
        self.last_usage = {
            "provider": "rule",
            "model": "rule-based",
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 0,
        }

    @property
    def has_openai_live(self) -> bool:
        if self.active_provider_name == "custom" and self.openai_base_url:
            return True
        return bool(self.openai_api_key and not self.openai_api_key.startswith("mock_"))

    @property
    def has_gemini_live(self) -> bool:
        has_key = bool(self.api_key and not self.api_key.startswith("mock_"))
        has_oauth = bool(self.gemini_oauth_credentials and self.gemini_oauth_credentials.get("access_token"))
        return has_key or has_oauth

    @property
    def is_live(self) -> bool:
        return self._active_provider() != "mock"

    def _active_provider(self) -> str:
        if self.active_provider_name:
            if self.active_provider_name == "gemini":
                return "gemini" if self.has_gemini_live else "mock"
            return "openai" if self.has_openai_live else "mock"
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

    def _openai_chat_json(self, client, prompt: str):
        kwargs: dict = {
            "model": self.openai_model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
        }
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as exc:
            err_s = str(exc).lower()
            if "response_format" in err_s or "400" in err_s or "json_object" in err_s:
                kwargs.pop("response_format", None)
                return client.chat.completions.create(**kwargs)
            raise

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

    def grade_auto_rubric_multi(
        self,
        questions: list[ExtractedQuestion],
        mv_map: dict[str, MathVerificationSummary] | None = None,
    ) -> tuple[str, list[QuestionGradingResult]]:
        """
        Chấm TRỌN BỘ bài làm gồm nhiều câu hỏi (sau khi đã sắp xếp lại từ Folder lộn xộn)
        trong MỘT lần gọi LLM duy nhất để vừa nhanh vừa phân bổ thang điểm 10.0đ hợp lý.
        """
        if not questions:
            return "Bài thi trống", []
        if len(questions) == 1:
            mv = mv_map.get(questions[0].question_id) if mv_map else None
            title, single_res = self.grade_auto_rubric(questions[0], math_verification=mv)
            return title, [single_res]

        provider = self._active_provider()
        if provider == "mock":
            # Offline fallback: chấm từng câu bằng rule engine, chia đều 10.0đ
            per_q = round(10.0 / len(questions), 2)
            results: list[QuestionGradingResult] = []
            for idx, q in enumerate(questions):
                pts = round(10.0 - per_q * (len(questions) - 1), 2) if idx == len(questions) - 1 else per_q
                rubric = QuestionRubric(
                    question_id=q.question_id,
                    question_text=q.question_text or f"Giải {q.question_id}",
                    max_score=pts,
                    total_points=pts,
                    criteria=[
                        RubricCriterion(criterion_id=f"{q.question_id}_C1_BIEN_DOI", name="Biến đổi và lời giải", max_score=round(pts * 0.7, 2)),
                        RubricCriterion(criterion_id=f"{q.question_id}_C2_KET_LUAN", name="Kết luận", max_score=round(pts - round(pts * 0.7, 2), 2)),
                    ],
                )
                q_mv = mv_map.get(q.question_id) if mv_map else None
                results.append(self._grade_with_rules(q, rubric, math_verification=q_mv))
            return f"Bài thi Toán ({len(questions)} câu — Chế độ Offline)", results

        # Build 1 prompt duy nhất chứa toàn bộ các câu đã sắp đúng thứ tự Câu 1 -> 2 -> 3...
        blocks: list[str] = []
        for q in questions:
            src_note = f" [Từ ảnh: {', '.join(q.source_files)}]" if q.source_files else ""
            blocks.append(f"=== {q.question_id}: {q.question_text or 'Bài làm học sinh'}{src_note} ===")
            for l in _active_lines_of(q):
                blocks.append(f"  Dòng {l.line_no} [{l.line_type.value}]: {l.raw_text or l.latex}")
            q_mv = mv_map.get(q.question_id) if mv_map else None
            if q_mv and q_mv.equations_checked > 0:
                blocks.append(f"  [SymPy kiểm chứng {q.question_id}]: {q_mv.summary}")

        all_solutions = "\n".join(blocks)
        prompt = f"""Bạn là Giám khảo chấm thi môn Toán THCS/THPT Việt Nam.
Dưới đây là toàn bộ bài làm của 1 học sinh đã được hệ thống gom từ nhiều ảnh chụp và sắp xếp lại ĐÚNG THỨ TỰ CÂU HỎI:

{all_solutions}

NHIỆM VỤ:
1. Chấm ĐỘC LẬP TỪNG CÂU HỎI đúng theo `question_id` đã liệt kê ở trên (KHÔNG gộp câu, KHÔNG bỏ sót câu nào).
2. Phân bổ hợp lý tổng thang điểm 10.0đ cho tất cả các câu (hoặc tổng điểm thực tế nếu đề có ghi sẵn điểm từng câu).
3. Đối chiếu với kết quả kiểm chứng SymPy cho từng câu. Nếu có bước tính sai, chỉ rõ dòng sai và áp dụng Error Propagation (bảo lưu 50% điểm phương pháp các bước sau).
4. Mọi công thức Toán đặt trong cặp dấu `$...$` để KaTeX hiển thị chuẩn.

TRẢ VỀ JSON HỢP LỆ:
{{
  "detected_exam_title": "<Tên bài kiểm tra và khối lớp>",
  "questions": [
    {{
      "question_id": "Câu 1",
      "total_points": 3.0,
      "suggested_score": 2.5,
      "error_propagation_detected": false,
      "affected_steps": [],
      "confidence": 0.95,
      "confidence_level": "green",
      "teacher_summary_comment": "<Nhận xét sư phạm riêng cho câu này>",
      "criteria_evaluations": [
        {{
          "criterion_id": "C1_1",
          "name": "<Tên bước chấm>",
          "score": 1.5,
          "max_score": 1.5,
          "status": "passed",
          "error_type": "none",
          "feedback": "<Nhận xét chi tiết>"
        }}
      ]
    }}
  ]
}}
"""
        if provider == "openai":
            from backend.app.services.provider_auth import build_openai_client

            client = build_openai_client(
                api_key=self.openai_api_key,
                base_url=self.openai_base_url,
                extra_headers=self.openai_extra_headers,
                timeout=45.0,
            )
            resp = self._openai_chat_json(client, prompt)
            usage = getattr(resp, "usage", None)
            if usage:
                in_t = int(getattr(usage, "prompt_tokens", 0) or 0)
                out_t = int(getattr(usage, "completion_tokens", 0) or 0)
                tot_t = int(getattr(usage, "total_tokens", in_t + out_t) or (in_t + out_t))
                self.last_usage = {
                    "provider": self.active_provider_name or "openai",
                    "model": self.openai_model,
                    "input_tokens": in_t,
                    "output_tokens": out_t,
                    "thinking_tokens": 0,
                    "total_tokens": tot_t,
                }
            data = _extract_json_dict(resp.choices[0].message.content or "")
        else:
            data = self._generate_gemini_json(prompt)

        # Một số phiên bản Gemini bọc toàn bộ object kết quả trong list 1 phần tử: [{"detected_exam_title": ..., "questions": [...]}]
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict) and "questions" in data[0]:
            data = data[0]

        if isinstance(data, list):
            exam_title = f"Bài kiểm tra Toán ({len(questions)} câu)"
            raw_q_list = data
        else:
            exam_title = str(data.get("detected_exam_title", f"Bài kiểm tra Toán ({len(questions)} câu)"))
            raw_q_list = data.get("questions", [])
            if not raw_q_list and "criteria_evaluations" in data:
                raw_q_list = [data]

        parsed_results: list[QuestionGradingResult] = []
        for idx, q_item in enumerate(raw_q_list):
            if not isinstance(q_item, dict):
                continue
            fallback_qid = questions[idx].question_id if idx < len(questions) else f"Câu {idx + 1}"
            qid = str(q_item.get("question_id") or fallback_qid)
            _, q_res = self._parse_auto_rubric_response(q_item, qid)
            parsed_results.append(q_res)

        return exam_title, parsed_results

    def grade_with_exam_rubric(
        self,
        student_qs: list[ExtractedQuestion],
        exam_rubric: ExamRubric,
        mv_map: dict[str, MathVerificationSummary] | None = None,
    ) -> tuple[list[QuestionGradingResult], dict[str, ExtractedQuestion]]:
        """
        Chấm toàn bộ bài làm của 1 học sinh theo ĐÚNG 1 Biểu điểm chuẩn cố định (`exam_rubric`).
        - Khớp thông minh câu viết tắt (`a, b, c` -> `Câu 2a, 2b, 2c`).
        - Câu học sinh không làm -> 0.0 / max_score (chống chấm phồng thang điểm 10!).
        - Chấm toàn bộ các câu đã làm trong 1 lượt LLM duy nhất (kèm Đề bài + Đáp án chuẩn + SymPy).
        """
        matched_map = match_student_questions_to_rubric(student_qs, exam_rubric.questions)
        provider = self._active_provider()

        attempted_pairs: list[tuple[QuestionRubric, ExtractedQuestion]] = []
        for rq in exam_rubric.questions:
            sq = matched_map.get(rq.question_id)
            if sq and _active_lines_of(sq):
                attempted_pairs.append((rq, sq))

        if provider == "mock" or not attempted_pairs:
            results: list[QuestionGradingResult] = []
            for rq in exam_rubric.questions:
                sq = matched_map.get(rq.question_id)
                if not sq or not _active_lines_of(sq):
                    results.append(
                        QuestionGradingResult(
                            question_id=rq.question_id,
                            suggested_score=0.0,
                            earned_points=0.0,
                            max_score=rq.max_score,
                            total_points=rq.total_points or rq.max_score,
                            confidence=1.0,
                            confidence_level="green",
                            teacher_summary_comment="Học sinh chưa làm câu này (không tìm thấy trong bài nộp).",
                        )
                    )
                else:
                    q_mv = mv_map.get(sq.question_id) if mv_map else None
                    res = self._grade_with_rules(sq, rq, run_self_critique=True, math_verification=q_mv)
                    res.question_id = rq.question_id
                    results.append(res)
            return results, matched_map

        blocks: list[str] = []
        for rq, sq in attempted_pairs:
            crits_txt = "\n".join(
                f"    - [{c.criterion_id}] {c.name} (tối đa {c.max_points or c.max_score}đ): {c.required_logic or c.description}"
                for c in rq.criteria
            )
            lines_txt = "\n".join(
                f"    Dòng {l.line_no} [{l.line_type.value}]: {l.raw_text or l.latex}"
                for l in _active_lines_of(sq)
            )
            q_mv = mv_map.get(sq.question_id) if mv_map else None
            sympy_txt = f"\n    [SymPy kiểm chứng]: {q_mv.summary}" if (q_mv and q_mv.equations_checked > 0) else ""
            blocks.append(
                f"=== {rq.question_id} (Tối đa: {rq.total_points or rq.max_score}đ) ===\n"
                f"  ĐỀ BÀI CHUẨN: {rq.question_text}\n"
                f"  BIỂU ĐIỂM & ĐÁP ÁN CHUẨN:\n{crits_txt}\n"
                f"  BÀI LÀM HỌC SINH (mã gốc: {sq.question_id}):\n{lines_txt}{sympy_txt}"
            )

        prompt = f"""Bạn là Giám khảo chấm thi môn Toán THCS/THPT Việt Nam.
ĐỀ THI CHUẨN: {exam_rubric.title} (Tổng thang điểm cả đề: {exam_rubric.total_points}đ).

Dưới đây là các câu học sinh ĐÃ LÀM kèm Đề bài chuẩn và Biểu điểm chuẩn:
{chr(10).join(blocks)}

QUY TẮC CHẤM CHUẨN XÁC TUYỆT ĐỐI:
1. Chấm CHÍNH XÁC theo `ĐỀ BÀI CHUẨN` và `BIỂU ĐIỂM & ĐÁP ÁN CHUẨN` ở trên (KHÔNG tự bịa đề khác, KHÔNG đổi `max_score` của từng câu).
2. Giữ NGUYÊN `question_id` đúng như trong Biểu điểm chuẩn.
3. Học sinh giải bằng cách khác hợp lệ nhưng ra đúng kết quả vẫn được trọn điểm.
4. Nếu có lỗi tính toán ở bước trước nhưng các bước sau vẫn đúng phương pháp -> bảo lưu 50% điểm phương pháp (`"error_propagation_detected": true`).
5. Mọi công thức Toán đặt trong cặp dấu `$...$`.

TRẢ VỀ JSON HỢP LỆ:
{{
  "questions": [
    {{
      "question_id": "Câu 2a",
      "total_points": 1.0,
      "suggested_score": 1.0,
      "error_propagation_detected": false,
      "affected_steps": [],
      "confidence": 0.96,
      "confidence_level": "green",
      "teacher_summary_comment": "<Nhận xét cụ thể theo đúng biểu điểm>",
      "criteria_evaluations": [
        {{
          "criterion_id": "C2A_1",
          "name": "<Tên tiêu chí>",
          "score": 0.5,
          "max_score": 0.5,
          "status": "passed",
          "error_type": "none",
          "feedback": "<Nhận xét>"
        }}
      ]
    }}
  ]
}}
"""
        if provider == "openai":
            from backend.app.services.provider_auth import build_openai_client

            client = build_openai_client(
                api_key=self.openai_api_key,
                base_url=self.openai_base_url,
                extra_headers=self.openai_extra_headers,
                timeout=45.0,
            )
            resp = self._openai_chat_json(client, prompt)
            usage = getattr(resp, "usage", None)
            if usage:
                in_t = int(getattr(usage, "prompt_tokens", 0) or 0)
                out_t = int(getattr(usage, "completion_tokens", 0) or 0)
                tot_t = int(getattr(usage, "total_tokens", in_t + out_t) or (in_t + out_t))
                self.last_usage = {
                    "provider": self.active_provider_name or "openai",
                    "model": self.openai_model,
                    "input_tokens": in_t,
                    "output_tokens": out_t,
                    "thinking_tokens": 0,
                    "total_tokens": tot_t,
                }
            data = _extract_json_dict(resp.choices[0].message.content or "")
        else:
            data = self._generate_gemini_json(prompt)

        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict) and "questions" in data[0]:
            data = data[0]
        raw_q_list = data if isinstance(data, list) else data.get("questions", [])

        graded_by_qid: dict[str, QuestionGradingResult] = {}
        rq_by_qid = {rq.question_id: rq for rq, _ in attempted_pairs}
        for idx, q_item in enumerate(raw_q_list):
            if not isinstance(q_item, dict):
                continue
            fallback_rq = attempted_pairs[idx][0] if idx < len(attempted_pairs) else None
            qid = str(q_item.get("question_id") or (fallback_rq.question_id if fallback_rq else f"Câu {idx + 1}"))
            target_rq = rq_by_qid.get(qid) or fallback_rq
            if target_rq:
                q_res = self._parse_rubric_response(q_item, target_rq)
                graded_by_qid[target_rq.question_id] = q_res

        final_results: list[QuestionGradingResult] = []
        for rq in exam_rubric.questions:
            if rq.question_id in graded_by_qid:
                final_results.append(graded_by_qid[rq.question_id])
            elif rq.question_id not in matched_map or not _active_lines_of(matched_map[rq.question_id]):
                final_results.append(
                    QuestionGradingResult(
                        question_id=rq.question_id,
                        suggested_score=0.0,
                        earned_points=0.0,
                        max_score=rq.max_score,
                        total_points=rq.total_points or rq.max_score,
                        confidence=1.0,
                        confidence_level="green",
                        teacher_summary_comment="Học sinh chưa làm câu này (không tìm thấy trong bài nộp).",
                    )
                )
            else:
                sq = matched_map[rq.question_id]
                q_mv = mv_map.get(sq.question_id) if mv_map else None
                res = self._grade_with_rules(sq, rq, run_self_critique=True, math_verification=q_mv)
                res.question_id = rq.question_id
                final_results.append(res)

        return final_results, matched_map

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
        data: dict | list,
        primary_qid: str,
    ) -> tuple[str, QuestionGradingResult]:
        if isinstance(data, list):
            data = next((item for item in data if isinstance(item, dict)), {})
        if not isinstance(data, dict):
            data = {}
        if "criteria_evaluations" not in data and isinstance(data.get("questions"), list) and data["questions"]:
            first_q = data["questions"][0]
            if isinstance(first_q, dict):
                data = {**data, **first_q}

        evals: list[CriterionEvaluation] = []
        for idx, c_data in enumerate(data.get("criteria_evaluations", [])):
            max_c = max(0.0, _safe_float(c_data.get("max_score", c_data.get("score")), 5.0))
            earned_c = max(0.0, min(max_c, _safe_float(c_data.get("score"), 0.0)))
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

        total_max = max(0.1, _safe_float(data.get("total_points"), 10.0))
        total_pts = max(0.0, min(total_max, _safe_float(data.get("suggested_score"), sum(e.score for e in evals))))
        exam_title = str(data.get("detected_exam_title", "Bài thi Toán học"))
        conf = max(0.0, min(1.0, _safe_float(data.get("confidence"), 0.92)))
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
        # v0.7.1: điểm câu = Σ tiêu chí (không tin suggested_score tự khai), quy đổi nếu tiêu chí vượt thang câu
        return exam_title, fit_result_to_max(q_res, total_max)

    def _call_openai_auto_reasoning_grader(
        self,
        question: ExtractedQuestion | list[ExtractedQuestion],
        math_verification: MathVerificationSummary | None = None,
    ) -> tuple[str, QuestionGradingResult]:
        from backend.app.services.provider_auth import build_openai_client

        client = build_openai_client(
            api_key=self.openai_api_key,
            base_url=self.openai_base_url,
            extra_headers=self.openai_extra_headers,
            timeout=30.0,
        )
        q_list = question if isinstance(question, list) else [question]
        primary_qid = q_list[0].question_id if q_list else "Q1"
        prompt = self._build_auto_rubric_prompt(q_list, math_verification)

        response = self._openai_chat_json(client, prompt)
        usage = getattr(response, "usage", None)
        if usage:
            in_t = int(getattr(usage, "prompt_tokens", 0) or 0)
            out_t = int(getattr(usage, "completion_tokens", 0) or 0)
            tot_t = int(getattr(usage, "total_tokens", in_t + out_t) or (in_t + out_t))
            self.last_usage = {
                "provider": self.active_provider_name or "openai",
                "model": self.openai_model,
                "input_tokens": in_t,
                "output_tokens": out_t,
                "thinking_tokens": 0,
                "total_tokens": tot_t,
            }
        data = _extract_json_dict(response.choices[0].message.content or "")
        return self._parse_auto_rubric_response(data, primary_qid)

    def _generate_gemini_json(self, prompt: str) -> dict:
        """Gọi Gemini Reasoning kèm chuỗi model fallback tự động & tối ưu thinking_budget để nhanh."""
        from google.genai import types
        from backend.app.services.provider_auth import build_gemini_client

        client = build_gemini_client(
            api_key=self.api_key,
            oauth_credentials=self.gemini_oauth_credentials,
            account_id=self.active_account_id,
        )
        candidate_models: list[str] = []
        for m in (
            self.model_name,
            "gemini-3.1-flash-lite",
            "gemini-3.1-flash-lite-preview",
            "gemini-flash-lite-latest",
            "gemini-3-flash-preview",
        ):
            if m and m not in candidate_models:
                candidate_models.append(m)

        last_exc: Exception | None = None
        for model_id in candidate_models:
            try:
                cfg_kwargs: dict = {
                    "response_mime_type": "application/json",
                    "temperature": 0.0,
                }
                try:
                    # Giới hạn thinking budget nhỏ (256) giúp chấm nhanh ~2-4s mà vẫn chuẩn toán
                    cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=256)
                except Exception:
                    pass

                try:
                    response = client.models.generate_content(
                        model=model_id,
                        contents=[prompt],
                        config=types.GenerateContentConfig(**cfg_kwargs),
                    )
                except Exception as tc_err:
                    if any(k in str(tc_err).lower() for k in ("thinking", "invalid_argument", "400")) and "thinking_config" in cfg_kwargs:
                        cfg_kwargs.pop("thinking_config", None)
                        response = client.models.generate_content(
                            model=model_id,
                            contents=[prompt],
                            config=types.GenerateContentConfig(**cfg_kwargs),
                        )
                    else:
                        raise

                um = getattr(response, "usage_metadata", None)
                if um:
                    in_t = int(getattr(um, "prompt_token_count", 0) or 0)
                    out_t = int(getattr(um, "candidates_token_count", 0) or 0)
                    th_t = int(getattr(um, "thoughts_token_count", 0) or 0)
                    tot_t = int(getattr(um, "total_token_count", in_t + out_t + th_t) or (in_t + out_t + th_t))
                    self.last_usage = {
                        "provider": "gemini",
                        "model": model_id,
                        "input_tokens": in_t,
                        "output_tokens": out_t,
                        "thinking_tokens": th_t,
                        "total_tokens": tot_t,
                    }
                return _extract_json_dict(response.text or "")
            except Exception as e:
                last_exc = e
                err_s = str(e)
                if any(code in err_s for code in ("401", "403", "API_KEY_INVALID")):
                    raise
                print(f"[Agent 2 Fallback] Model {model_id} lỗi ({err_s[:100]}), thử model kế tiếp...")
                continue

        raise last_exc or RuntimeError("Gemini Reasoning thất bại trên tất cả model dự phòng.")

    def _call_gemini_auto_reasoning_grader(
        self,
        question: ExtractedQuestion | list[ExtractedQuestion],
        math_verification: MathVerificationSummary | None = None,
    ) -> tuple[str, QuestionGradingResult]:
        q_list = question if isinstance(question, list) else [question]
        primary_qid = q_list[0].question_id if q_list else "Q1"
        prompt = self._build_auto_rubric_prompt(q_list, math_verification)
        data = self._generate_gemini_json(prompt)
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

    def _parse_rubric_response(self, data: dict | list, rubric: QuestionRubric) -> QuestionGradingResult:
        if isinstance(data, list):
            data = next((item for item in data if isinstance(item, dict)), {})
        if not isinstance(data, dict):
            data = {}
        if "criteria_evaluations" not in data and isinstance(data.get("questions"), list) and data["questions"]:
            first_q = data["questions"][0]
            if isinstance(first_q, dict):
                data = {**data, **first_q}

        evals: list[CriterionEvaluation] = []
        for c_data in data.get("criteria_evaluations", []):
            max_c = max(0.0, _safe_float(c_data.get("max_score"), 0.5))
            earned_c = max(0.0, min(max_c, _safe_float(c_data.get("score"), 0.0)))
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
        total_pts = max(0.0, min(max_total, _safe_float(data.get("suggested_score"), sum(e.score for e in evals))))
        conf = max(0.0, min(1.0, _safe_float(data.get("confidence"), 0.9)))
        conf_lvl = "green" if conf >= 0.90 else ("yellow" if conf >= 0.70 else "red")

        q_res = QuestionGradingResult(
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
        # v0.7.1: điểm câu = Σ tiêu chí, kẹp theo điểm tối đa của barem
        return fit_result_to_max(q_res, max_total)

    def _call_openai_reasoning_grader(
        self,
        question: ExtractedQuestion,
        rubric: QuestionRubric,
        math_verification: MathVerificationSummary | None = None,
    ) -> QuestionGradingResult:
        from backend.app.services.provider_auth import build_openai_client

        client = build_openai_client(
            api_key=self.openai_api_key,
            base_url=self.openai_base_url,
            extra_headers=self.openai_extra_headers,
            timeout=30.0,
        )
        prompt = self._build_rubric_prompt(question, rubric, math_verification)
        response = self._openai_chat_json(client, prompt)
        usage = getattr(response, "usage", None)
        if usage:
            in_t = int(getattr(usage, "prompt_tokens", 0) or 0)
            out_t = int(getattr(usage, "completion_tokens", 0) or 0)
            tot_t = int(getattr(usage, "total_tokens", in_t + out_t) or (in_t + out_t))
            self.last_usage = {
                "provider": self.active_provider_name or "openai",
                "model": self.openai_model,
                "input_tokens": in_t,
                "output_tokens": out_t,
                "thinking_tokens": 0,
                "total_tokens": tot_t,
            }
        data = _extract_json_dict(response.choices[0].message.content or "")
        return self._parse_rubric_response(data, rubric)

    def _call_gemini_reasoning_grader(
        self,
        question: ExtractedQuestion,
        rubric: QuestionRubric,
        math_verification: MathVerificationSummary | None = None,
    ) -> QuestionGradingResult:
        prompt = self._build_rubric_prompt(question, rubric, math_verification)
        data = self._generate_gemini_json(prompt)
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
