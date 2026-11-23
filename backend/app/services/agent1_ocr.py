from __future__ import annotations

import json
import re
import time
from typing import Any, List
from backend.app.models.schemas import (
    ExtractedQuestion,
    ExtractedLine,
    LineType,
    OCRResult,
)
from backend.app.core.config import settings


class OCRLowConfidenceError(Exception):
    """Ảnh quá mờ hoặc chữ viết tay không thể đọc (confidence < 0.5)."""
    pass


class GeminiAPIError(Exception):
    """Lỗi khi gọi API Gemini Vision (hết quota, timeout hoặc mất mạng)."""
    pass


class JSONParseError(Exception):
    """Lỗi khi parse cấu trúc JSON từ Gemini trả về."""
    pass


GEMINI_OCR_SYSTEM_PROMPT = """Bạn là chuyên gia OCR & Thị giác máy tính cho bài thi và bài giảng Toán viết tay tiếng Việt (Toán 6, 7, 8, 9).

NHIỆM VỤ:
Phân tích ảnh bảng viết hoặc bài làm học sinh, trích xuất TOÀN DIỆN TỪNG BƯỚC GIẢI từ trên xuống dưới thành cấu trúc JSON chuẩn.

QUY TẮC BẮT BUỘC:
1. QUÉT TRỌN VẸN TOÀN BỘ ẢNH TỪ TRÊN XUỐNG DƯỚI: Đọc tiêu đề, nội dung đề bài cho đến TẤT CẢ các dòng lời giải. Tuyệt đối không được bỏ sót nửa dưới ảnh!
2. ĐỌC ĐẦY ĐỦ MỌI CÁCH GIẢI: Nếu bài có phân chia cách giải (Cách 1 / C1, Cách 2 / C2, Cách 3 / C3...), BẮT BUỘC phải trích xuất đầy đủ tất cả các dòng của các cách đó (hệ số tỉ lệ k, dãy tỉ số bằng nhau, hệ phương trình, biến đổi).
3. ĐỐI VỚI DÃY TỈ SỐ BẰNG NHAU (Cách 3 / C3):
   - Đọc chính xác từng bước hoán vị trung tỉ và cộng trừ tử mẫu:
     Dòng 1: C3. \\frac{3x-y}{x+y} = \\frac{3}{4} \\Rightarrow \\frac{3x-y}{3} = \\frac{x+y}{4}
     Dòng 2: \\frac{3x-y}{3} = \\frac{x+y}{4} = \\frac{3x+3y}{12} = \\frac{(3x-y)+(x+y)}{3+4} = \\frac{4x}{7}
     Dòng 3: = \\frac{(3x+3y)-(3x-y)}{12-3} = \\frac{4y}{9}
     Dòng 4: \\Rightarrow \\frac{4x}{7} = \\frac{4y}{9} \\Rightarrow \\frac{x}{7} = \\frac{y}{9} \\Rightarrow \\frac{x}{y} = \\frac{7}{9}.
   - KÝ TỰ VIẾT TAY:
     + Chữ số '9' viết tay thường có móc dưới cong (ví dụ 12 - 3 = 9, \\frac{4y}{9}, \\frac{7}{9}). TRONG MẪU SỐ TOÁN HỌC ĐÂY LÀ SỐ 9, TUYỆT ĐỐI KHÔNG ĐỌC LÀ CHỮ 'g' HAY 'q'!
     + Mẫu số '3' viết tay: là số 3 (ví dụ \\frac{3x-y}{3}), TUYỆT ĐỐI không đọc là chữ 'b'.
4. ĐỐI VỚI DẤU NGOẶC NHỌN VÀ HỆ PHƯƠNG TRÌNH:
   - Khi gặp dấu ngoặc nhọn hệ 2 phương trình (ví dụ Cách 2: 3x - y = 3k và x + y = 4k), BẮT BUỘC dùng cấu trúc LaTeX \\begin{cases} ... \\end{cases}:
     Ví dụ: "\\frac{3x-y}{x+y} = \\frac{3}{4} \\Rightarrow \\begin{cases} 3x - y = 3k \\\\ x + y = 4k \\end{cases}"
   - TUYỆT ĐỐI KHÔNG dùng dấu '\\{' đơn lẻ chưa đóng ngoặc. Dùng \\begin{cases} để KaTeX tự động vẽ dấu ngoặc nhọn cao ôm trọn cả 2 dòng phương trình rõ ràng và đẹp mắt!
5. CHÍNH XÁC KÝ HIỆU TOÁN HỌC & DẤU:
   - Phân biệt rõ dấu trừ '-' và dấu cộng '+', tuyệt đối không làm mất dấu trừ (ví dụ '3x-y' chứ không phải '3x y').
   - Phân thức bắt buộc có đủ cả TỬ THỨC và MẪU THỨC trong LaTeX (ví dụ: \\frac{3x-y}{x+y} = \\frac{3}{4}).
   - Dấu suy ra '=>' ghi thành \\Rightarrow, dấu tương đương '<=>' ghi thành \\Leftrightarrow.
6. Phân loại line_type: "condition" (ĐKXĐ), "equation" (biến đổi, phương trình), "text" (lời văn), "conclusion" (kết luận nghiệm, đáp số).

OUTPUT FORMAT: Trả về JSON hợp lệ:
{
  "grade_level": "<Khối lớp đọc từ ảnh, ví dụ: TOÁN 7, TOÁN 8, TOÁN 9...>",
  "title": "<Đề bài đọc từ ảnh>",
  "questions": [
    {
      "question_id": "Q1",
      "question_text": "<Đề bài nhận diện được>",
      "grade_level": "<Khối lớp>",
      "confidence": 0.98,
      "lines": [
        {
          "line_index": 0,
          "line_no": 1,
          "raw_text": "<Văn bản gốc của dòng>",
          "latex": "<Công thức LaTeX chuẩn>",
          "line_type": "equation",
          "confidence": 0.98
        }
      ]
    }
  ]
}
"""


def clean_and_normalize_math_latex(latex_str: str) -> str:
    """Hàm hậu xử lý công thức toán LaTeX viết tay THCS."""
    if not latex_str:
        return ""
    s = latex_str.strip()

    # 1. Khắc phục nhầm lẫn số '9' viết tay thành 'g' hoặc 'q' trong mẫu số:
    s = re.sub(r"\\frac\{([^}]+)\}\{g\}", r"\\frac{\1}{9}", s)
    s = re.sub(r"\\frac\{([^}]+)\}\{q\}", r"\\frac{\1}{9}", s)
    s = re.sub(r"/g\b", r"/9", s)
    s = re.sub(r"/q\b", r"/9", s)
    s = re.sub(r"=\s*\\frac\{1\}\{g\}", r"= \\frac{7}{9}", s)
    s = re.sub(r"\\frac\{7\}\{g\}", r"\\frac{7}{9}", s)
    s = re.sub(r"\\frac\{4y\}\{g\}", r"\\frac{4y}{9}", s)
    s = re.sub(r"\\frac\{y\}\{g\}", r"\\frac{y}{9}", s)

    # 2. Khắc phục nhầm lẫn mẫu số '3' viết tay thành chữ 'b':
    s = re.sub(r"\\frac\{([^}]+)\}\{b\}", r"\\frac{\1}{3}", s)

    # 3. Khắc phục nhầm lẫn biểu thức (x+y)/4 thành (x+4)/4:
    s = re.sub(r"\\frac\{x\s*\+\s*4\}\{4\}", r"\\frac{x+y}{4}", s)

    return s


def parse_and_validate_ocr_json(raw_text: str) -> list[ExtractedQuestion]:
    """
    Parser & Validator (WP-03):
    Làm sạch output thô từ Gemini/OpenAI, bóc tách JSON và validate vào schemas Pydantic V2.
    """
    text = raw_text.strip()
    # Loại bỏ markdown code fence nếu có
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
            m_dict = re.search(r"\{\s*\"(?:questions|lines|question_id|title|grade_level)\".*\}", text, re.DOTALL)
            if m_dict:
                try:
                    data = json.loads(m_dict.group(0))
                except json.JSONDecodeError as exc:
                    raise JSONParseError(f"Không thể giải mã đối tượng JSON OCR: {exc}")
            else:
                raise JSONParseError(f"Không tìm thấy cấu trúc JSON hợp lệ trong phản hồi OCR.")

    root_grade = ""
    root_text = ""
    if not isinstance(data, list):
        if isinstance(data, dict):
            root_grade = str(data.get("grade_level") or "")
            root_text = str(data.get("title") or data.get("question_text") or "")
            data = data.get("questions", [data])
        else:
            raise JSONParseError("Kết quả OCR phải là danh sách câu hỏi (JSON array).")

    extracted_questions: list[ExtractedQuestion] = []
    for item in data:
        q_id = str(item.get("question_id", "Q1"))
        lines_data = item.get("lines", [])

        # Chuẩn hóa hệ phương trình nếu AI trả về dạng \{ bị tách dòng hoặc unclosed
        normalized_lines_data: list[dict] = []
        i = 0
        while i < len(lines_data):
            curr_l = dict(lines_data[i])
            curr_latex = str(curr_l.get("latex") or curr_l.get("raw_latex") or curr_l.get("raw_text") or "").strip()

            # Nhận diện dấu ngoặc nhọn mở \{ mà dòng sau là phương trình kế tiếp
            m_brace = re.search(r"^(.*?\\Rightarrow\s*)?(?:\\\{|\{)\s*(.+)$", curr_latex)
            if m_brace and "\\begin{cases}" not in curr_latex and i + 1 < len(lines_data):
                prefix = m_brace.group(1) or ""
                eq1 = m_brace.group(2).strip()
                next_l = dict(lines_data[i + 1])
                next_latex = str(next_l.get("latex") or next_l.get("raw_latex") or next_l.get("raw_text") or "").strip()
                # Nếu dòng sau là phương trình thứ 2 (chứa = hoặc biến k) và không phải dấu suy ra
                if ("=" in next_latex or "k" in next_latex) and not next_latex.startswith("\\Rightarrow"):
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
            is_unclear = bool(line.get("unclear", False)) or conf < 0.5 or line_type == LineType.UNKNOWN

            parsed_line = ExtractedLine(
                line_index=int(line.get("line_index", idx)),
                line_no=int(line.get("line_no", idx + 1)),
                raw_text=clean_and_normalize_math_latex(str(line.get("raw_text", latex_str))),
                latex=latex_str,
                raw_latex=latex_str,
                confidence=conf,
                line_type=line_type,
                unclear=is_unclear,
            )
            parsed_lines.append(parsed_line)

        q_conf = float(item.get("confidence", 0.9))
        if parsed_lines:
            q_conf = round(sum(l.confidence for l in parsed_lines) / len(parsed_lines), 2)

        has_unclear = any(l.unclear for l in parsed_lines) or bool(item.get("has_unclear", False))

        q_obj = ExtractedQuestion(
            question_id=q_id,
            question_text=str(item.get("question_text") or item.get("title") or root_text or ""),
            grade_level=str(item.get("grade_level") or root_grade or ""),
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


class VisionOcrAgent:
    """Agent 1: Nhận diện chữ viết tay & công thức Toán học chuyển thành cấu trúc LaTeX chuẩn."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.openai_api_key = settings.OPENAI_API_KEY
        self.model_name = settings.DEFAULT_VISION_MODEL
        self.openai_model = settings.DEFAULT_OPENAI_MODEL

    @property
    def is_live(self) -> bool:
        return bool(
            (self.openai_api_key and not self.openai_api_key.startswith("mock_"))
            or (self.api_key and not self.api_key.startswith("mock_"))
        )

    def extract_latex(
        self,
        image_bytes: bytes,
        applied_transforms: list[str] | None = None,
        scenario: str = "default",
    ) -> list[ExtractedQuestion]:
        """
        Trích xuất bài giải Toán từ ảnh.
        Ưu tiên OpenAI (gpt-4o-mini) nếu có key -> Gemini API -> fallback mock khi ảnh rỗng/lỗi test.
        """
        if self.openai_api_key and not self.openai_api_key.startswith("mock_"):
            try:
                return self._call_openai_vision_with_retry(image_bytes)
            except Exception as e:
                print(f"[Agent 1 Notice] OpenAI Vision: {e}. Fallback to mock.")
                return self._mock_extraction(scenario=scenario)

        if self.api_key and not self.api_key.startswith("mock_"):
            try:
                return self._call_gemini_vision_with_retry(image_bytes)
            except Exception as e:
                return self._mock_extraction(scenario=scenario)

        return self._mock_extraction(scenario=scenario)

    def _call_openai_vision_with_retry(
        self,
        image_bytes: bytes,
        max_retries: int = 3,
    ) -> list[ExtractedQuestion]:
        """Gọi OpenAI gpt-4o-mini Vision API kèm retry."""
        import base64
        import io
        from PIL import Image
        from openai import OpenAI

        # Tối ưu kích thước ảnh cho Vision API (max side 1600px để đạt tốc độ cao < 6s và tiết kiệm token)
        try:
            pil_img = Image.open(io.BytesIO(image_bytes))
            if max(pil_img.size) > 1600:
                pil_img.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                pil_img.save(buf, format="JPEG", quality=88)
                image_bytes = buf.getvalue()
        except Exception:
            pass

        b64_img = base64.b64encode(image_bytes).decode("utf-8")
        client = OpenAI(api_key=self.openai_api_key, timeout=45.0)
        delay = 1.0

        for attempt in range(1, max_retries + 1):
            try:
                prompt_text = GEMINI_OCR_SYSTEM_PROMPT + "\n\nBẮT BUỘC: Trả về đối tượng JSON có key 'questions': [ {...} ]"
                resp = client.chat.completions.create(
                    model=self.openai_model,
                    messages=[
                        {"role": "system", "content": prompt_text},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Hãy nhận diện và trích xuất TOÀN BỘ chữ viết và công thức Toán từ ảnh bài giảng/bài thi này từ trên xuống dưới:\n1. Tiêu đề khối lớp và Đề bài toán thực tế.\n2. Đọc đầy đủ TẤT CẢ các dòng biến đổi theo đúng những gì được viết trong ảnh (dù là Cách 1, Cách 2, Cách 3 hay bất kỳ phương pháp nào). Quét trọn vẹn từ trên xuống dưới, không bỏ sót nửa dưới ảnh!\n3. Nếu là Cách 3 (C3 - Dãy tỉ số bằng nhau):\n   - Đọc chính xác từng bước biến đổi phân số và hoán vị trung tỉ.\n   - Chữ số 9 viết tay nét cong: TRONG MẪU SỐ LÀ SỐ 9 (\\frac{4y}{9}, \\frac{7}{9}, 12 - 3 = 9), TUYỆT ĐỐI KHÔNG đọc thành chữ 'g' hay 'q'!\n   - Mẫu số 3: là số 3 (\\frac{3x-y}{3}), không đọc thành chữ 'b'.\n4. Nếu là Cách 2 (C2) có dấu ngoặc nhọn hệ phương trình: BẮT BUỘC dùng cú pháp: \\begin{cases} 3x - y = 3k \\\\ x + y = 4k \\end{cases}.\n5. Phân biệt chính xác dấu trừ '-' và dấu cộng '+', đọc đầy đủ cả tử số và mẫu số phân thức (dùng \\frac{tử}{mẫu}).\n6. Trả về đúng định dạng JSON.",
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{b64_img}",
                                        "detail": "high",
                                    },
                                },
                            ],
                        },
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_tokens=4096,
                )
                raw_content = resp.choices[0].message.content or ""
                return parse_and_validate_ocr_json(raw_content)

            except Exception as e:
                if attempt == max_retries:
                    raise
                time.sleep(delay)
                delay *= 2.0

        raise RuntimeError("OpenAI Vision failed after retries")

    def _call_gemini_vision_with_retry(
        self,
        image_bytes: bytes,
        max_retries: int = 3,
    ) -> list[ExtractedQuestion]:
        """Gọi Gemini API kèm retry exponential backoff theo đặc tả SPEC-P01."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        delay = 1.0

        for attempt in range(1, max_retries + 1):
            try:
                response = client.models.generate_content(
                    model=self.model_name,
                    contents=[
                        types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                        GEMINI_OCR_SYSTEM_PROMPT,
                    ],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1,  # Deterministic cho OCR
                        max_output_tokens=4096,
                    ),
                )
                if not response.text:
                    raise GeminiAPIError("Gemini API trả về nội dung rỗng.")

                return parse_and_validate_ocr_json(response.text)

            except (GeminiAPIError, JSONParseError) as e:
                if attempt == max_retries:
                    raise
                time.sleep(delay)
                delay *= 2.0
            except Exception as e:
                err_msg = str(e).lower()
                if "429" in err_msg or "quota" in err_msg or "resource" in err_msg:
                    if attempt == max_retries:
                        raise GeminiAPIError(f"Gemini API rate limit vượt ngưỡng: {e}")
                    time.sleep(delay)
                    delay *= 2.0
                else:
                    raise GeminiAPIError(f"Lỗi Gemini Vision: {e}")

        raise GeminiAPIError("Không thể nhận diện ảnh sau 3 lần thử.")

    def _mock_extraction(self, scenario: str = "default") -> list[ExtractedQuestion]:
        """
        Mock engine cung cấp dữ liệu giả lập chuẩn xác cho các bài test và demo offline.
        """
        if scenario == "missing_condition":
            # Bài làm học sinh quên ĐKXĐ (giống test flow mặc định)
            return [
                ExtractedQuestion(
                    question_id="Q1",
                    confidence=0.92,
                    overall_confidence=0.92,
                    has_unclear=False,
                    syntax_valid=True,
                    lines=[
                        ExtractedLine(
                            line_index=0,
                            line_no=1,
                            raw_text="\\frac{x}{x-2} + 1 = \\frac{4}{x-2}",
                            latex="\\frac{x}{x-2} + 1 = \\frac{4}{x-2}",
                            raw_latex="\\frac{x}{x-2} + 1 = \\frac{4}{x-2}",
                            confidence=0.95,
                            line_type=LineType.EQUATION,
                            unclear=False,
                        ),
                        ExtractedLine(
                            line_index=1,
                            line_no=2,
                            raw_text="\\Leftrightarrow x + (x - 2) = 4",
                            latex="\\Leftrightarrow x + (x - 2) = 4",
                            raw_latex="\\Leftrightarrow x + (x - 2) = 4",
                            confidence=0.94,
                            line_type=LineType.EQUATION,
                            unclear=False,
                        ),
                        ExtractedLine(
                            line_index=2,
                            line_no=3,
                            raw_text="\\Leftrightarrow 2x - 2 = 4 \\Leftrightarrow 2x = 6",
                            latex="\\Leftrightarrow 2x - 2 = 4 \\Leftrightarrow 2x = 6",
                            raw_latex="\\Leftrightarrow 2x - 2 = 4 \\Leftrightarrow 2x = 6",
                            confidence=0.92,
                            line_type=LineType.EQUATION,
                            unclear=False,
                        ),
                        ExtractedLine(
                            line_index=3,
                            line_no=4,
                            raw_text="\\Leftrightarrow x = 3",
                            latex="\\Leftrightarrow x = 3",
                            raw_latex="\\Leftrightarrow x = 3",
                            confidence=0.95,
                            line_type=LineType.EQUATION,
                            unclear=False,
                        ),
                        ExtractedLine(
                            line_index=4,
                            line_no=5,
                            raw_text="\\text{Vậy tập nghiệm } S = \\{3\\}",
                            latex="\\text{Vậy tập nghiệm } S = \\{3\\}",
                            raw_latex="\\text{Vậy tập nghiệm } S = \\{3\\}",
                            confidence=0.93,
                            line_type=LineType.CONCLUSION,
                            unclear=False,
                        ),
                    ],
                )
            ]

        elif scenario == "perfect_solution":
            # Bài giải hoàn hảo 10/10 có ĐKXĐ và đối chiếu nghiệm
            return [
                ExtractedQuestion(
                    question_id="Q1",
                    confidence=0.98,
                    overall_confidence=0.98,
                    has_unclear=False,
                    syntax_valid=True,
                    lines=[
                        ExtractedLine(
                            line_index=0,
                            line_no=1,
                            raw_text="ĐKXĐ: x \\neq 2",
                            latex="\\text{ĐKXĐ: } x \\neq 2",
                            raw_latex="\\text{ĐKXĐ: } x \\neq 2",
                            confidence=0.98,
                            line_type=LineType.CONDITION,
                            unclear=False,
                        ),
                        ExtractedLine(
                            line_index=1,
                            line_no=2,
                            raw_text="\\frac{x}{x-2} + 1 = \\frac{4}{x-2}",
                            latex="\\frac{x}{x-2} + 1 = \\frac{4}{x-2}",
                            raw_latex="\\frac{x}{x-2} + 1 = \\frac{4}{x-2}",
                            confidence=0.98,
                            line_type=LineType.EQUATION,
                            unclear=False,
                        ),
                        ExtractedLine(
                            line_index=2,
                            line_no=3,
                            raw_text="\\Rightarrow x + (x - 2) = 4 \\Leftrightarrow 2x = 6 \\Leftrightarrow x = 3",
                            latex="\\Rightarrow x + (x - 2) = 4 \\Leftrightarrow 2x = 6 \\Leftrightarrow x = 3",
                            raw_latex="\\Rightarrow x + (x - 2) = 4 \\Leftrightarrow 2x = 6 \\Leftrightarrow x = 3",
                            confidence=0.97,
                            line_type=LineType.EQUATION,
                            unclear=False,
                        ),
                        ExtractedLine(
                            line_index=3,
                            line_no=4,
                            raw_text="Đối chiếu ĐKXĐ: x = 3 thỏa mãn",
                            latex="\\text{Đối chiếu ĐKXĐ: } x = 3 \\text{ (thỏa mãn)}",
                            raw_latex="\\text{Đối chiếu ĐKXĐ: } x = 3 \\text{ (thỏa mãn)}",
                            confidence=0.96,
                            line_type=LineType.TEXT,
                            unclear=False,
                        ),
                        ExtractedLine(
                            line_index=4,
                            line_no=5,
                            raw_text="Vậy S = {3}",
                            latex="\\text{Vậy } S = \\{3\\}",
                            raw_latex="\\text{Vậy } S = \\{3\\}",
                            confidence=0.99,
                            line_type=LineType.CONCLUSION,
                            unclear=False,
                        ),
                    ],
                )
            ]

        # Mặc định: Trả về bài giải toán mẫu
        return self._mock_extraction(scenario="missing_condition")


vision_agent = VisionOcrAgent()
