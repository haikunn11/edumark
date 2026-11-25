from __future__ import annotations

import base64
import io
import json
import re
import time
from PIL import Image
from backend.app.models.schemas import (
    ExtractedQuestion,
    ExtractedLine,
    LineType,
)
from backend.app.core.config import settings


class OCRLowConfidenceError(Exception):
    """Ảnh quá mờ hoặc chữ viết tay không thể đọc (confidence < 0.5)."""
    pass


class OCRApiError(Exception):
    """Lỗi khi gọi API Vision (OpenAI / Gemini) ở chế độ live."""

    def __init__(self, message: str, error_code: str = "E008") -> None:
        super().__init__(message)
        self.error_code = error_code


class GeminiAPIError(OCRApiError):
    """Lỗi khi gọi API Gemini Vision (hết quota, timeout hoặc mất mạng)."""
    pass


class JSONParseError(Exception):
    """Lỗi khi parse cấu trúc JSON từ Vision API trả về."""
    pass


FULL_PAGE_HANDWRITING_OCR_PROMPT = """Bạn là chuyên gia Thị giác Máy tính & OCR chữ viết tay bài thi học sinh Việt Nam (Toán và các môn tự luận THCS/THPT).

NHIỆM VỤ TỐI THƯỢNG:
Đọc TRỌN VẸN 100% toàn bộ dữ liệu chữ viết tay trên MỌI TRANG ẢNH được cung cấp và chuyển thành cấu trúc JSON chuẩn xác tuyệt đối.

QUY TẮC BẮT BUỘC (KHÔNG ĐƯỢC VI PHẠM):
1. QUÉT TRỌN VẸN TOÀN TRANG (TOP-TO-BOTTOM, LEFT-TO-RIGHT):
   - Đọc từ tiêu đề, họ tên/lớp học sinh ở đầu trang (nếu có), đề bài, cho đến dòng cuối cùng của trang cuối cùng.
   - Nếu bài viết chia 2 cột hoặc nhiều trang, đọc trọn cột trái rồi cột phải, trang 1 rồi trang 2... và ghi đúng số trang `page_no` (1-based).
2. TRUNG THỰC TUYỆT ĐỐI — CẤM TỰ SỬA BÀI HỌC SINH:
   - Ghi lại CHÍNH XÁC những gì học sinh thực sự viết trên giấy, KỂ CẢ KHI học sinh tính sai, viết sai dấu, hoặc thiếu bước!
   - TUYỆT ĐỐI KHÔNG tự sửa đáp số sai thành đúng ở bước OCR.
3. PHÁT HIỆN NÉT GẠCH XÓA / NHÁP BỎ (`is_strikethrough`):
   - Nếu học sinh gạch chéo, gạch ngang bỏ một dòng hoặc đoạn làm nhầm: vẫn ghi nhận nhưng BẮT BUỘC đặt `"line_type": "scratch"` và `"is_strikethrough": true`.
4. CHUẨN HÓA CÔNG THỨC TOÁN HỌC LATEX:
   - Phân thức dùng `\\frac{tử}{mẫu}` đầy đủ tử và mẫu; căn bậc hai `\\sqrt{...}`; dấu suy ra `\\Rightarrow`; tương đương `\\Leftrightarrow`; khác `\\neq`; nhỏ hơn/lớn hơn hoặc bằng `\\le`, `\\ge`.
   - Hệ phương trình có dấu ngoặc nhọn BẮT BUỘC dùng `\\begin{cases} pt1 \\\\ pt2 \\end{cases}`. Không dùng `\\{` đơn lẻ chưa đóng ngoặc.
   - Chú ý phân biệt chữ số viết tay dễ nhầm trong ngữ cảnh Toán học (ví dụ số 9 vs g/q, số 5 vs s, số 2 vs z, dấu trừ `-` vs dấu cộng `+`).
5. PHÂN LOẠI `line_type`:
   - `"condition"`: Đặt điều kiện xác định (ĐKXĐ), điều kiện của ẩn.
   - `"equation"`: Phương trình, bất phương trình, biểu thức, bước biến đổi đại số.
   - `"text"`: Lời văn giải thích, gọi ẩn, lập luận.
   - `"conclusion"`: Kết luận nghiệm, tập nghiệm, đáp số cuối cùng.
   - `"scratch"`: Dòng bị học sinh gạch bỏ.
   - `"unknown"`: Nét chữ quá mờ, không thể đọc chắc chắn (`"unclear": true`, `"confidence" < 0.5`).

OUTPUT FORMAT — CHỈ TRẢ VỀ JSON HỢP LỆ:
{
  "student_name_detected": "<Họ tên học sinh đọc ở đầu trang, để rỗng nếu không có>",
  "student_class_detected": "<Lớp học sinh đọc ở đầu trang, để rỗng nếu không có>",
  "grade_level": "<Khối lớp nếu nhận diện được, ví dụ: Toán 9>",
  "title": "<Đề bài đọc từ ảnh nếu có>",
  "questions": [
    {
      "question_id": "Q1",
      "question_text": "<Đề bài câu hỏi nếu có>",
      "grade_level": "<Khối lớp>",
      "confidence": 0.96,
      "lines": [
        {
          "line_index": 0,
          "line_no": 1,
          "page_no": 1,
          "raw_text": "<Văn bản gốc đúng như học sinh viết>",
          "latex": "<Công thức LaTeX chuẩn>",
          "line_type": "equation",
          "confidence": 0.96,
          "unclear": false,
          "is_strikethrough": false
        }
      ]
    }
  ]
}
"""

# Alias giữ tương thích ngược cho các module cũ có import tên cũ
GEMINI_OCR_SYSTEM_PROMPT = FULL_PAGE_HANDWRITING_OCR_PROMPT


def clean_and_normalize_math_latex(latex_str: str) -> str:
    """
    Hậu xử lý AN TOÀN & TỔNG QUÁT cho công thức LaTeX viết tay.
    Tuyệt đối KHÔNG dùng regex overfit sửa biểu thức theo một bài mẫu cụ thể.
    """
    if not latex_str:
        return ""
    s = latex_str.strip()
    # Chuẩn hóa ký hiệu Unicode phổ biến sang LaTeX chuẩn
    s = s.replace("⇔", "\\Leftrightarrow").replace("⇒", "\\Rightarrow").replace("≠", "\\neq")
    s = s.replace("≤", "\\le").replace("≥", "\\ge").replace("−", "-")
    # Loại bỏ khoảng trắng thừa liên tiếp
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s


def parse_and_validate_ocr_json(raw_text: str) -> list[ExtractedQuestion]:
    """
    Parser & Validator:
    Làm sạch output thô từ Vision LLM, bóc tách JSON và validate vào Pydantic V2 schemas.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m_arr = re.search(r"\[\s*\{.*\}\s*\]", text, re.DOTALL)
        if m_arr:
            try:
                data = json.loads(m_arr.group(0))
            except json.JSONDecodeError as exc:
                raise JSONParseError(f"Không thể giải mã mảng JSON OCR: {exc}")
        else:
            m_dict = re.search(
                r"\{\s*\"(?:questions|lines|question_id|title|grade_level|student_name_detected)\".*\}",
                text,
                re.DOTALL,
            )
            if m_dict:
                try:
                    data = json.loads(m_dict.group(0))
                except json.JSONDecodeError as exc:
                    raise JSONParseError(f"Không thể giải mã đối tượng JSON OCR: {exc}")
            else:
                raise JSONParseError("Không tìm thấy cấu trúc JSON hợp lệ trong phản hồi OCR.")

    root_grade = ""
    root_text = ""
    root_student_name = ""
    root_student_class = ""
    if not isinstance(data, list):
        if isinstance(data, dict):
            root_grade = str(data.get("grade_level") or "")
            root_text = str(data.get("title") or data.get("question_text") or "")
            root_student_name = str(data.get("student_name_detected") or "")
            root_student_class = str(data.get("student_class_detected") or "")
            data = data.get("questions", [data])
        else:
            raise JSONParseError("Kết quả OCR phải là danh sách câu hỏi (JSON array).")

    extracted_questions: list[ExtractedQuestion] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        q_id = str(item.get("question_id", "Q1"))
        lines_data = item.get("lines", [])

        # Chuẩn hóa hệ phương trình nếu AI trả về dấu ngoặc nhọn \{ bị tách thành 2 dòng
        normalized_lines_data: list[dict] = []
        i = 0
        while i < len(lines_data):
            curr_l = dict(lines_data[i])
            curr_latex = str(curr_l.get("latex") or curr_l.get("raw_latex") or curr_l.get("raw_text") or "").strip()

            m_brace = re.search(r"^(.*?\\Rightarrow\s*)?(?:\\\{|\{)\s*(.+)$", curr_latex)
            if m_brace and "\\begin{cases}" not in curr_latex and i + 1 < len(lines_data):
                prefix = m_brace.group(1) or ""
                eq1 = m_brace.group(2).strip()
                next_l = dict(lines_data[i + 1])
                next_latex = str(next_l.get("latex") or next_l.get("raw_latex") or next_l.get("raw_text") or "").strip()
                if ("=" in next_latex) and not next_latex.startswith("\\Rightarrow"):
                    eq2 = next_latex.lstrip("\\{").lstrip("{").strip()
                    full_cases = f"{prefix}\\begin{{cases}} {eq1} \\\\ {eq2} \\end{{cases}}"
                    curr_l["latex"] = full_cases
                    curr_l["raw_latex"] = full_cases
                    curr_l["raw_text"] = full_cases
                    normalized_lines_data.append(curr_l)
                    i += 2
                    continue

            normalized_lines_data.append(curr_l)
            i += 1

        parsed_lines: list[ExtractedLine] = []
        for idx, line in enumerate(normalized_lines_data):
            lt_str = str(line.get("line_type", "equation")).lower()
            try:
                line_type = LineType(lt_str)
            except ValueError:
                line_type = LineType.EQUATION

            conf = float(line.get("confidence", 0.9))
            conf = max(0.0, min(1.0, conf))

            latex_str = str(line.get("latex") or line.get("raw_latex") or line.get("raw_text") or "")
            latex_str = clean_and_normalize_math_latex(latex_str)
            is_strike = bool(line.get("is_strikethrough", False)) or line_type == LineType.SCRATCH
            is_unclear = bool(line.get("unclear", False)) or conf < 0.5 or line_type == LineType.UNKNOWN

            parsed_line = ExtractedLine(
                line_index=int(line.get("line_index", idx)),
                line_no=int(line.get("line_no", idx + 1)),
                page_no=int(line.get("page_no", 1)),
                raw_text=clean_and_normalize_math_latex(str(line.get("raw_text", latex_str))),
                latex=latex_str,
                raw_latex=latex_str,
                confidence=conf,
                line_type=LineType.SCRATCH if is_strike else line_type,
                unclear=is_unclear,
                is_strikethrough=is_strike,
            )
            parsed_lines.append(parsed_line)

        active_lines = [l for l in parsed_lines if not l.is_strikethrough] or parsed_lines
        q_conf = float(item.get("confidence", 0.9))
        if active_lines:
            q_conf = round(sum(l.confidence for l in active_lines) / len(active_lines), 2)

        has_unclear = any(l.unclear and not l.is_strikethrough for l in parsed_lines) or bool(item.get("has_unclear", False))

        q_obj = ExtractedQuestion(
            question_id=q_id,
            question_text=str(item.get("question_text") or item.get("title") or root_text or ""),
            grade_level=str(item.get("grade_level") or root_grade or ""),
            student_name_detected=str(item.get("student_name_detected") or root_student_name or ""),
            student_class_detected=str(item.get("student_class_detected") or root_student_class or ""),
            confidence=q_conf,
            overall_confidence=q_conf,
            has_unclear=has_unclear,
            syntax_valid=bool(item.get("syntax_valid", True)),
            lines=parsed_lines,
        )
        extracted_questions.append(q_obj)

    if not extracted_questions:
        raise OCRLowConfidenceError("Không tìm thấy nội dung bài làm hợp lệ trong ảnh.")

    return extracted_questions


def _resize_for_vision_api(image_bytes: bytes, max_side: int = 1800) -> bytes:
    """Tối ưu dung lượng ảnh trước khi gửi Vision API nhưng vẫn giữ nét chữ viết tay sắc."""
    try:
        pil_img = Image.open(io.BytesIO(image_bytes))
        if max(pil_img.size) > max_side:
            pil_img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            pil_img.convert("RGB").save(buf, format="JPEG", quality=90)
            return buf.getvalue()
    except Exception:
        pass
    return image_bytes


class VisionOcrAgent:
    """Agent 1: Nhận diện toàn trang chữ viết tay & công thức Toán học (hỗ trợ 1 hoặc nhiều trang)."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.openai_api_key = settings.OPENAI_API_KEY
        self.model_name = settings.DEFAULT_VISION_MODEL
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

    def extract_latex(
        self,
        image_bytes: bytes,
        applied_transforms: list[str] | None = None,
        scenario: str = "default",
    ) -> list[ExtractedQuestion]:
        """Trích xuất toàn bộ bài làm từ 1 ảnh."""
        return self.extract_latex_multipage([image_bytes], applied_transforms=applied_transforms, scenario=scenario)

    def extract_latex_multipage(
        self,
        pages_bytes: list[bytes],
        applied_transforms: list[str] | None = None,
        scenario: str = "default",
    ) -> list[ExtractedQuestion]:
        """
        Trích xuất trọn vẹn chữ viết tay từ 1 hoặc nhiều trang ảnh bài làm của cùng 1 học sinh.
        - Nếu ở chế độ offline/mock (chưa cài API Key): trả về dữ liệu mock để test.
        - Nếu ĐÃ cài Live API Key mà API lỗi: NÉM OCRApiError tường minh (CẤM âm thầm trả bài mẫu giả!).
        """
        provider = self._active_provider()
        if provider == "openai":
            return self._call_openai_vision_with_retry(pages_bytes)
        if provider == "gemini":
            return self._call_gemini_vision_with_retry(pages_bytes)
        return self._mock_extraction(scenario=scenario)

    def _call_openai_vision_with_retry(
        self,
        pages_bytes: list[bytes],
        max_retries: int = 3,
    ) -> list[ExtractedQuestion]:
        """Gọi OpenAI Vision API cho 1 hoặc nhiều trang kèm exponential backoff."""
        from openai import OpenAI

        client = OpenAI(api_key=self.openai_api_key, timeout=45.0)
        user_content: list[dict] = [
            {
                "type": "text",
                "text": (
                    f"Đây là bài làm viết tay của học sinh gồm {len(pages_bytes)} trang. "
                    "Hãy đọc TRỌN VẸN mọi chữ viết tay, công thức, tên học sinh (nếu có) theo đúng thứ tự từng trang, "
                    "gắn is_strikethrough=true cho dòng gạch bỏ, KHÔNG tự sửa lỗi tính toán của học sinh, và trả về JSON."
                ),
            }
        ]
        for page_bytes in pages_bytes:
            opt_bytes = _resize_for_vision_api(page_bytes)
            b64_img = base64.b64encode(opt_bytes).decode("utf-8")
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64_img}", "detail": "high"},
                }
            )

        delay = 1.0
        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                resp = client.chat.completions.create(
                    model=self.openai_model,
                    messages=[
                        {"role": "system", "content": FULL_PAGE_HANDWRITING_OCR_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                    max_tokens=4096,
                )
                raw_content = resp.choices[0].message.content or ""
                return parse_and_validate_ocr_json(raw_content)
            except Exception as e:
                last_exc = e
                if attempt == max_retries:
                    break
                time.sleep(delay)
                delay *= 2.0

        err_msg = str(last_exc or "Unknown OpenAI Vision error")
        err_code = "E006" if ("429" in err_msg or "rate" in err_msg.lower() or "quota" in err_msg.lower()) else "E008"
        raise OCRApiError(f"OpenAI Vision OCR thất bại sau {max_retries} lần thử: {err_msg}", error_code=err_code)

    def _call_gemini_vision_with_retry(
        self,
        pages_bytes: list[bytes],
        max_retries: int = 3,
    ) -> list[ExtractedQuestion]:
        """Gọi Gemini Vision API cho 1 hoặc nhiều trang kèm exponential backoff."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        contents: list = []
        for idx, page_bytes in enumerate(pages_bytes, start=1):
            opt_bytes = _resize_for_vision_api(page_bytes)
            contents.append(f"Trang {idx}/{len(pages_bytes)}:")
            contents.append(types.Part.from_bytes(data=opt_bytes, mime_type="image/jpeg"))
        contents.append(FULL_PAGE_HANDWRITING_OCR_PROMPT)

        delay = 1.0
        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                response = client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.0,
                        max_output_tokens=4096,
                    ),
                )
                if not response.text:
                    raise GeminiAPIError("Gemini API trả về nội dung rỗng.", error_code="E008")
                return parse_and_validate_ocr_json(response.text)
            except Exception as e:
                last_exc = e
                if attempt == max_retries:
                    break
                time.sleep(delay)
                delay *= 2.0

        err_msg = str(last_exc or "Unknown Gemini Vision error")
        err_code = "E006" if ("429" in err_msg or "quota" in err_msg.lower() or "resource" in err_msg.lower()) else "E008"
        raise GeminiAPIError(f"Gemini Vision OCR thất bại sau {max_retries} lần thử: {err_msg}", error_code=err_code)

    def _mock_extraction(self, scenario: str = "default") -> list[ExtractedQuestion]:
        """Mock engine dùng cho unit test & demo offline khi chưa cấu hình API key."""
        if scenario == "perfect_solution":
            return [
                ExtractedQuestion(
                    question_id="Q1",
                    confidence=0.96,
                    overall_confidence=0.96,
                    has_unclear=False,
                    syntax_valid=True,
                    lines=[
                        ExtractedLine(
                            line_index=0, line_no=1,
                            raw_text="ĐKXĐ: x \\neq 2",
                            latex="x \\neq 2", raw_latex="x \\neq 2",
                            confidence=0.97, line_type=LineType.CONDITION,
                        ),
                        ExtractedLine(
                            line_index=1, line_no=2,
                            raw_text="\\frac{x}{x-2} + 1 = \\frac{4}{x-2}",
                            latex="\\frac{x}{x-2} + 1 = \\frac{4}{x-2}",
                            raw_latex="\\frac{x}{x-2} + 1 = \\frac{4}{x-2}",
                            confidence=0.96, line_type=LineType.EQUATION,
                        ),
                        ExtractedLine(
                            line_index=2, line_no=3,
                            raw_text="\\Leftrightarrow x + (x - 2) = 4 \\Leftrightarrow 2x = 6 \\Leftrightarrow x = 3",
                            latex="x + (x - 2) = 4 \\Leftrightarrow 2x = 6 \\Leftrightarrow x = 3",
                            raw_latex="x + (x - 2) = 4 \\Leftrightarrow 2x = 6 \\Leftrightarrow x = 3",
                            confidence=0.95, line_type=LineType.EQUATION,
                        ),
                        ExtractedLine(
                            line_index=3, line_no=4,
                            raw_text="Đối chiếu ĐKXĐ: x = 3 thỏa mãn. Vậy S = \\{3\\}",
                            latex="S = \\{3\\}", raw_latex="S = \\{3\\}",
                            confidence=0.96, line_type=LineType.CONCLUSION,
                        ),
                    ],
                )
            ]

        # Default / missing_condition scenario
        return [
            ExtractedQuestion(
                question_id="Q1",
                confidence=0.94,
                overall_confidence=0.94,
                has_unclear=False,
                syntax_valid=True,
                lines=[
                    ExtractedLine(
                        line_index=0, line_no=1,
                        raw_text="\\frac{x}{x-2} + 1 = \\frac{4}{x-2}",
                        latex="\\frac{x}{x-2} + 1 = \\frac{4}{x-2}",
                        raw_latex="\\frac{x}{x-2} + 1 = \\frac{4}{x-2}",
                        confidence=0.95, line_type=LineType.EQUATION,
                    ),
                    ExtractedLine(
                        line_index=1, line_no=2,
                        raw_text="\\Leftrightarrow x + (x - 2) = 4",
                        latex="\\Leftrightarrow x + (x - 2) = 4",
                        raw_latex="\\Leftrightarrow x + (x - 2) = 4",
                        confidence=0.94, line_type=LineType.EQUATION,
                    ),
                    ExtractedLine(
                        line_index=2, line_no=3,
                        raw_text="\\Leftrightarrow 2x - 2 = 4 \\Leftrightarrow 2x = 6",
                        latex="\\Leftrightarrow 2x - 2 = 4 \\Leftrightarrow 2x = 6",
                        raw_latex="\\Leftrightarrow 2x - 2 = 4 \\Leftrightarrow 2x = 6",
                        confidence=0.92, line_type=LineType.EQUATION,
                    ),
                    ExtractedLine(
                        line_index=3, line_no=4,
                        raw_text="\\Leftrightarrow x = 3",
                        latex="\\Leftrightarrow x = 3",
                        raw_latex="\\Leftrightarrow x = 3",
                        confidence=0.95, line_type=LineType.EQUATION,
                    ),
                    ExtractedLine(
                        line_index=4, line_no=5,
                        raw_text="\\text{Vậy tập nghiệm } S = \\{3\\}",
                        latex="\\text{Vậy tập nghiệm } S = \\{3\\}",
                        raw_latex="\\text{Vậy tập nghiệm } S = \\{3\\}",
                        confidence=0.93, line_type=LineType.CONCLUSION,
                    ),
                ],
            )
        ]


vision_agent = VisionOcrAgent()
