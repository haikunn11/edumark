from __future__ import annotations

import base64
import json
import re
import uuid

from backend.app.core.config import settings
from backend.app.models.schemas import (
    ExamRubric,
    QuestionRubric,
    RubricCriterion,
    SubjectType,
)
from backend.app.services.agent1_ocr import _repair_json_backslashes, _resize_for_vision_api
from backend.app.services.agent2_grader import reasoning_agent
from backend.app.services.batch import split_pdf_pages
from backend.app.services.folder_reassembler import _slugify_vi, normalize_question_id


def _build_rubric_extraction_prompt(title: str, grade: int, total_points: float, extra_text: str = "") -> str:
    text_section = f"\nNỘI DUNG ĐỀ THI / ĐÁP ÁN GIÁO VIÊN CUNG CẤP:\n{extra_text}\n" if extra_text.strip() else ""
    return f"""Bạn là Chuyên gia Khảo thí & Giáo viên Toán giỏi của Bộ GD&ĐT Việt Nam.
Hãy đọc kỹ TỜ ĐỀ THI (và/hoặc ĐÁP ÁN, BIỂU ĐIỂM) dưới đây để trích xuất thành một **BIỂU ĐIỂM CHUẨN (RUBRIC)** cố định dùng để chấm đồng nhất cho cả lớp.{text_section}

YÊU CẦU BẮT BUỘC:
1. Đọc CHÍNH XÁC 100% nội dung từng câu hỏi trong đề thi (giữ nguyên mọi số liệu, phân số, dấu âm, phương trình, đơn vị).
2. Tách rõ từng ý nhỏ thành câu độc lập nếu là ý chấm điểm riêng (ví dụ: `"Câu 1"`, `"Câu 2a"`, `"Câu 2b"`, `"Câu 2c"`, `"Câu 3"`, `"Câu 4a"`, `"Câu 4b"`, `"Câu 4c"`).
3. Nếu đề thi KHÔNG kèm đáp án, BẮT BUỘC tự giải chính xác từng câu và ghi rõ ĐÁP SỐ CHUẨN + CÁC BƯỚC GIẢI CHÍNH vào `required_logic` của từng tiêu chí (để máy chấm không bao giờ đoán sai đề!).
4. Nếu đề thi có sẵn điểm từng câu, dùng đúng điểm đó. Nếu không ghi điểm, phân bổ hợp lý sao cho tổng điểm toàn bài = `{total_points}`.
5. Mọi công thức Toán đặt gọn gàng theo chuẩn LaTeX (ví dụ `$x = \\frac{{23}}{{20}}$`).
6. Trong JSON, mọi dấu `\\` của LaTeX PHẢI viết thành `\\\\`.

TRẢ VỀ JSON OBJECT HỢP LỆ (KHÔNG markdown fence):
{{
  "title": "{title or '<Tên đề kiểm tra trích từ đề>'}",
  "grade": {grade},
  "total_points": {total_points},
  "questions": [
    {{
      "question_id": "Câu 2a",
      "question_text": "Tìm x biết: $x - \\\\frac{{3}}{{4}} = \\\\frac{{2}}{{5}}$",
      "max_score": 1.0,
      "criteria": [
        {{
          "criterion_id": "C2A_1",
          "name": "Chuyển vế và quy đồng đúng",
          "max_score": 0.5,
          "required_logic": "$x = \\\\frac{{2}}{{5}} + \\\\frac{{3}}{{4}} = \\\\frac{{8}}{{20}} + \\\\frac{{15}}{{20}}$"
        }},
        {{
          "criterion_id": "C2A_2",
          "name": "Tính đúng đáp số cuối cùng",
          "max_score": 0.5,
          "required_logic": "$x = \\\\frac{{23}}{{20}}$"
        }}
      ]
    }}
  ]
}}
"""


def _parse_rubric_json(raw_text: str, default_title: str, grade: int, total_points: float) -> ExamRubric:
    cleaned = (raw_text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    try:
        data = json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        repaired = _repair_json_backslashes(cleaned)
        try:
            data = json.loads(repaired, strict=False)
        except json.JSONDecodeError:
            m = re.search(r"(\{.*\})", repaired, re.DOTALL)
            if not m:
                raise ValueError("Không thể phân tích JSON Biểu điểm từ phản hồi AI.")
            data = json.loads(m.group(1), strict=False)

    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError("Phản hồi AI không phải đối tượng JSON ExamRubric hợp lệ.")

    final_title = str(data.get("title") or data.get("exam_title") or default_title or "Đề kiểm tra Toán").strip()
    final_grade = int(data.get("grade") or grade or 7)
    raw_qs = data.get("questions", [])
    if not isinstance(raw_qs, list) or not raw_qs:
        raise ValueError("AI không trích xuất được câu hỏi nào từ đề thi.")

    parsed_qs: list[QuestionRubric] = []
    for q_idx, q in enumerate(raw_qs, start=1):
        if not isinstance(q, dict):
            continue
        raw_qid = str(q.get("question_id") or f"Câu {q_idx}").strip()
        _, canon_qid = normalize_question_id(raw_qid)
        q_text = str(q.get("question_text") or "").strip()
        q_max = max(0.0, float(q.get("max_score", q.get("total_points", 0.0)) or 0.0))

        raw_crits = q.get("criteria", [])
        crits: list[RubricCriterion] = []
        if isinstance(raw_crits, list):
            for c_idx, c in enumerate(raw_crits, start=1):
                if not isinstance(c, dict):
                    continue
                c_max = max(0.0, float(c.get("max_score", c.get("max_points", 0.0)) or 0.0))
                crits.append(
                    RubricCriterion(
                        criterion_id=str(c.get("criterion_id") or f"Q{q_idx}_C{c_idx}").strip(),
                        name=str(c.get("name") or c.get("description") or f"Bước {c_idx}").strip(),
                        description=str(c.get("description") or c.get("name") or "").strip(),
                        max_score=c_max,
                        max_points=c_max,
                        required_logic=str(c.get("required_logic") or "").strip(),
                    )
                )

        if not crits:
            pts = q_max if q_max > 0 else round(total_points / max(1, len(raw_qs)), 2)
            crits = [
                RubricCriterion(
                    criterion_id=f"Q{q_idx}_C1",
                    name="Lời giải và đáp số chính xác",
                    max_score=pts,
                    max_points=pts,
                    required_logic=q_text,
                )
            ]
        elif q_max <= 0:
            q_max = round(sum(c.max_score for c in crits), 2)

        parsed_qs.append(
            QuestionRubric(
                question_id=canon_qid if canon_qid != "Câu chưa đánh số" else raw_qid,
                question_text=q_text,
                max_score=round(q_max, 2),
                total_points=round(q_max, 2),
                allow_alternative_solutions=True,
                allow_error_propagation=True,
                criteria=crits,
            )
        )

    computed_total = round(sum(q.max_score for q in parsed_qs), 2) or float(total_points or 10.0)
    slug = _slugify_vi(final_title)[:18].upper()
    exam_id = f"EXAM_{slug}_{uuid.uuid4().hex[:4].upper()}"

    return ExamRubric(
        exam_id=exam_id,
        title=final_title,
        exam_title=final_title,
        subject=SubjectType.MATH_HIGH if final_grade >= 10 else SubjectType.MATH_SECONDARY,
        grade=final_grade,
        total_points=computed_total,
        rubric_version=1,
        questions=parsed_qs,
    )


def _offline_parse_exam_text(exam_text: str, title: str, grade: int, total_points: float) -> ExamRubric:
    """Bộ phân tích đề thi ngoại tuyến (khi chưa cấu hình API Key hoặc chạy unit test)."""
    lines = [ln.strip() for ln in (exam_text or "").splitlines() if ln.strip()]
    chunks: list[tuple[str, list[str]]] = []
    q_header_re = re.compile(
        r"^(?:(?:câu|bài|bai|cau)\s*\d+\s*[a-zA-Z]?|[a-zA-Z]\)|q\d+[a-zA-Z]?)\s*[:.\-)]?\s*(.*)$",
        re.IGNORECASE,
    )

    for ln in lines:
        m = q_header_re.match(ln)
        if m:
            raw_prefix = ln[: len(ln) - len(m.group(1))].strip().rstrip(":.-)")
            _, canon = normalize_question_id(raw_prefix)
            qid = canon if canon != "Câu chưa đánh số" else raw_prefix
            rest = m.group(1).strip()
            chunks.append((qid, [rest] if rest else []))
        elif chunks:
            chunks[-1][1].append(ln)
        else:
            chunks.append(("Câu 1", [ln]))

    if not chunks:
        chunks = [("Câu 1", ["Giải bài toán theo đề thi đã tải lên"])]

    per_q = round(total_points / len(chunks), 2)
    questions: list[QuestionRubric] = []
    for idx, (qid, body_lines) in enumerate(chunks, start=1):
        pts = round(total_points - per_q * (len(chunks) - 1), 2) if idx == len(chunks) else per_q
        q_text = " ".join(body_lines).strip() or f"Nội dung {qid}"
        c1_pts = round(pts * 0.6, 2)
        c2_pts = round(pts - c1_pts, 2)
        questions.append(
            QuestionRubric(
                question_id=qid,
                question_text=q_text,
                max_score=pts,
                total_points=pts,
                criteria=[
                    RubricCriterion(
                        criterion_id=f"Q{idx}_C1",
                        name="Phương pháp & biến đổi đúng",
                        max_score=c1_pts,
                        max_points=c1_pts,
                        required_logic=q_text,
                    ),
                    RubricCriterion(
                        criterion_id=f"Q{idx}_C2",
                        name="Tính toán & kết luận đáp số",
                        max_score=c2_pts,
                        max_points=c2_pts,
                        required_logic=q_text,
                    ),
                ],
            )
        )

    final_title = title.strip() or "Đề kiểm tra Toán (Tạo từ văn bản)"
    slug = _slugify_vi(final_title)[:18].upper()
    return ExamRubric(
        exam_id=f"EXAM_{slug}_{uuid.uuid4().hex[:4].upper()}",
        title=final_title,
        exam_title=final_title,
        subject=SubjectType.MATH_HIGH if grade >= 10 else SubjectType.MATH_SECONDARY,
        grade=grade,
        total_points=round(sum(q.max_score for q in questions), 2),
        rubric_version=1,
        questions=questions,
    )


def _active_provider() -> str:
    return reasoning_agent._active_provider()


def _call_gemini_rubric(prompt: str, image_pages: list[bytes]) -> str:
    from google.genai import types
    from backend.app.services.provider_auth import build_gemini_client

    client = build_gemini_client(
        api_key=reasoning_agent.api_key,
        oauth_credentials=reasoning_agent.gemini_oauth_credentials,
        account_id=reasoning_agent.active_account_id,
    )
    contents: list = [prompt]
    for img_b in image_pages:
        opt_b = _resize_for_vision_api(img_b, max_side=2000)
        contents.append(types.Part.from_bytes(data=opt_b, mime_type="image/jpeg"))

    candidate_models: list[str] = []
    for m in (
        reasoning_agent.model_name,
        settings.DEFAULT_REASONING_MODEL,
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
                "max_output_tokens": 8192,
            }
            try:
                cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=512)
            except Exception:
                pass
            try:
                resp = client.models.generate_content(
                    model=model_id,
                    contents=contents,
                    config=types.GenerateContentConfig(**cfg_kwargs),
                )
            except Exception as tc_err:
                if any(k in str(tc_err).lower() for k in ("thinking", "invalid_argument", "400")) and "thinking_config" in cfg_kwargs:
                    cfg_kwargs.pop("thinking_config", None)
                    resp = client.models.generate_content(
                        model=model_id,
                        contents=contents,
                        config=types.GenerateContentConfig(**cfg_kwargs),
                    )
                else:
                    raise
            return resp.text or ""
        except Exception as exc:
            last_exc = exc
            if any(c in str(exc) for c in ("401", "403", "API_KEY_INVALID")):
                raise
            continue
    raise last_exc or RuntimeError("Gemini không thể trích xuất biểu điểm từ đề thi.")


def _call_openai_rubric(prompt: str, image_pages: list[bytes]) -> str:
    from backend.app.services.provider_auth import build_openai_client

    client = build_openai_client(
        api_key=reasoning_agent.openai_api_key,
        base_url=reasoning_agent.openai_base_url,
        extra_headers=reasoning_agent.openai_extra_headers,
        timeout=60.0,
    )
    content_blocks: list[dict] = [{"type": "text", "text": prompt}]
    for img_b in image_pages:
        b64 = base64.b64encode(_resize_for_vision_api(img_b, max_side=2000)).decode("utf-8")
        content_blocks.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}})

    model = reasoning_agent.openai_model or settings.DEFAULT_OPENAI_MODEL
    kwargs: dict = {
        "model": model,
        "messages": [{"role": "user", "content": content_blocks}],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
    }
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as exc:
        err_s = str(exc).lower()
        if "response_format" in err_s or "400" in err_s or "json_object" in err_s:
            kwargs.pop("response_format", None)
            resp = client.chat.completions.create(**kwargs)
        else:
            raise
    return resp.choices[0].message.content or ""


def extract_rubric_from_files(
    files_data: list[tuple[str, bytes]],
    title: str = "",
    grade: int = 7,
    total_points: float = 10.0,
    extra_notes: str = "",
) -> ExamRubric:
    """Trích xuất Biểu điểm chuẩn (ExamRubric) từ 1 hoặc nhiều ảnh/PDF tờ Đề thi & Đáp án."""
    if not files_data:
        raise ValueError("Vui lòng tải lên ít nhất 1 ảnh hoặc file PDF đề thi.")

    image_pages: list[bytes] = []
    for fname, raw in files_data:
        if (fname or "").lower().endswith(".pdf") or raw[:4] == b"%PDF":
            pdf_pages = split_pdf_pages(raw, max_pages=20)
            image_pages.extend(img_b for _, img_b in pdf_pages)
        else:
            image_pages.append(raw)

    provider = _active_provider()
    if provider == "mock":
        return _offline_parse_exam_text(
            extra_notes or f"Câu 1: Giải bài toán từ ảnh đề {files_data[0][0]}\nCâu 2: Tính toán và kết luận",
            title=title or f"Đề thi từ {files_data[0][0]}",
            grade=grade,
            total_points=total_points,
        )

    prompt = _build_rubric_extraction_prompt(title=title, grade=grade, total_points=total_points, extra_text=extra_notes)
    raw_json = _call_gemini_rubric(prompt, image_pages) if provider == "gemini" else _call_openai_rubric(prompt, image_pages)
    return _parse_rubric_json(raw_json, default_title=title, grade=grade, total_points=total_points)


def extract_rubric_from_text(
    exam_text: str,
    title: str = "",
    grade: int = 7,
    total_points: float = 10.0,
) -> ExamRubric:
    """Tạo Biểu điểm chuẩn (ExamRubric) tự động từ văn bản Đề thi / Đáp án do giáo viên dán vào."""
    if not (exam_text or "").strip():
        raise ValueError("Nội dung đề thi không được để trống.")

    provider = _active_provider()
    if provider == "mock":
        return _offline_parse_exam_text(exam_text, title=title, grade=grade, total_points=total_points)

    prompt = _build_rubric_extraction_prompt(title=title, grade=grade, total_points=total_points, extra_text=exam_text)
    raw_json = _call_gemini_rubric(prompt, []) if provider == "gemini" else _call_openai_rubric(prompt, [])
    return _parse_rubric_json(raw_json, default_title=title, grade=grade, total_points=total_points)
