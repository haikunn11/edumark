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
   - **TÁCH ĐÚNG TỪNG CÂU HỎI:** Nếu trong ảnh có nhiều câu/bài (ví dụ `Câu 1`, `Câu 2`, `Bài 1a`, `Bài 3`...), BẮT BUỘC tách thành từng phần tử riêng trong mảng `"questions"` với `"question_id"` ghi đúng số câu trên giấy (ví dụ `"Câu 1"`, `"Câu 1a"`, `"Câu 2"`, `"Câu 3"`). Không gộp chung các câu khác nhau vào 1 `Q1`!
2. TRUNG THỰC TUYỆT ĐỐI — CẤM TỰ SỬA BÀI HỌC SINH & BỎ QUA MỰC ĐỎ GIÁO VIÊN:
   - Ghi lại CHÍNH XÁC những gì học sinh thực sự viết trên giấy, KỂ CẢ KHI học sinh tính sai, viết sai dấu, hoặc thiếu bước!
   - TUYỆT ĐỐI KHÔNG đọc hoặc tính các dấu tích (`✓`, `đ`), con điểm mực đỏ của giáo viên ở lề giấy vào bài làm của học sinh.
3. PHÁT HIỆN HƯỚNG XOAY ẢNH & DẤU HIỆU NHẬN DẠNG BÀI THI (QUAN TRỌNG ĐỂ TÁCH HỌC SINH):
   - `"orientation_deg"`: góc cần xoay **theo chiều kim đồng hồ** (`0`, `90`, `180`, hoặc `270`) để chữ trên ảnh đứng thẳng đọc bình thường từ trái sang phải, trên xuống dưới. Ví dụ: ảnh đã thẳng đứng = `0`; ảnh chụp bị nằm ngang cần xoay 90° theo chiều kim đồng hồ mới đứng = `90`; cần xoay ngược chiều kim đồng hồ 90° = `270`.
   - `"paper_brand_detected"`: tên nhãn hiệu giấy/vở in ở góc/mép giấy nếu nhìn thấy (vd `"HAI TIEN"`, `"KOKUYO"`, `"KLONG"`, `"ClassMate"`, `"THIEN LONG"`, `"CAMPUS"`, để rỗng `""` nếu không thấy).
   - `"handwriting_style"`: mô tả ngắn 3-6 từ về nét chữ & màu mực (vd `"chữ tròn nhỏ mực xanh đậm"`).
4. PHÁT HIỆN NÉT GẠCH XÓA / NHÁP BỎ (`is_strikethrough`):
   - Nếu học sinh gạch chéo, gạch ngang bỏ một dòng hoặc đoạn làm nhầm: vẫn ghi nhận nhưng BẮT BUỘC đặt `"line_type": "scratch"` và `"is_strikethrough": true`.
5. CHUẨN HÓA CÔNG THỨC TOÁN HỌC LATEX:
   - Phân thức dùng `\\frac{tử}{mẫu}` đầy đủ tử và mẫu; căn bậc hai `\\sqrt{...}`; dấu suy ra `\\Rightarrow`; tương đương `\\Leftrightarrow`; khác `\\neq`; nhỏ hơn/lớn hơn hoặc bằng `\\le`, `\\ge`.
   - Hệ phương trình có dấu ngoặc nhọn BẮT BUỘC dùng `\\begin{cases} pt1 \\\\ pt2 \\end{cases}`. Không dùng `\\{` đơn lẻ chưa đóng ngoặc.
   - Chú ý phân biệt chữ số viết tay dễ nhầm trong ngữ cảnh Toán học (ví dụ số 9 vs g/q, số 5 vs s, số 2 vs z, dấu trừ `-` vs dấu cộng `+`).
6. PHÂN LOẠI `line_type`:
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
  "paper_brand_detected": "<Tên hãng giấy in trên mép tờ giấy nếu có, vd HAI TIEN, KOKUYO, KLONG, ClassMate, THIEN LONG; để rỗng nếu không thấy>",
  "handwriting_style": "<Mô tả ngắn nét chữ và màu mực>",
  "orientation_deg": 0,
  "grade_level": "<Khối lớp nếu nhận diện được, ví dụ: Toán 7>",
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


_LATEX_BS_CMD_PATTERN = (
    r"(?:frac|forall|text|times|to|tau|theta|tan|triangle|therefore|"
    r"neq|nu|nabla|notin|not|begin|beta|boxed|bar|binom|bot|big|bmod|"
    r"right|rightarrow|rho|radical|rangle)"
)
_LATEX_BS_CMD_RE = re.compile(rf"^{_LATEX_BS_CMD_PATTERN}\b")
_UNESCAPED_LATEX_JSON_RE = re.compile(rf"(?:^|[^\\])(?:\\\\)*\\{_LATEX_BS_CMD_PATTERN}\b")


def _safe_ocr_float(val, default: float = 0.9) -> float:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _extract_balanced_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if not in_str:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


def _repair_json_backslashes(s: str) -> str:
    """
    Quét tuyến tính từ trái sang phải để sửa lỗi escape backslash LaTeX trong JSON từ LLM:
    - Giữ nguyên mọi cặp escape hợp lệ (\\\\, \\\", \\/, \\uXXXX, \\n, \\t...) — KHÔNG phá \\\\Rightarrow thành \\\\\\Rightarrow.
    - Tự nhân đôi dấu \\ đơn đứng trước lệnh LaTeX (\\Rightarrow, \\underline, \\frac, \\begin, \\neq, \\text, \\{, \\}...).
    """
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\":
            if i + 1 < n:
                nxt = s[i + 1]
                if nxt in ('"', "\\", "/"):
                    out.append(ch)
                    out.append(nxt)
                    i += 2
                    continue
                if nxt == "u" and i + 5 < n and all(c in "0123456789abcdefABCDEF" for c in s[i + 2 : i + 6]):
                    out.append(s[i : i + 6])
                    i += 6
                    continue
                if nxt in ("b", "f", "n", "r", "t"):
                    if _LATEX_BS_CMD_RE.match(s[i + 1 : i + 14]):
                        out.append("\\\\")
                        i += 1
                        continue
                    out.append(ch)
                    out.append(nxt)
                    i += 2
                    continue
            out.append("\\\\")
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _safe_json_loads(s: str):
    """Parse JSON chịu lỗi backslash LaTeX và ký tự điều khiển từ LLM."""
    if _UNESCAPED_LATEX_JSON_RE.search(s):
        repaired = _repair_json_backslashes(s)
        return json.loads(repaired, strict=False)
    try:
        return json.loads(s, strict=False)
    except json.JSONDecodeError:
        repaired = _repair_json_backslashes(s)
        return json.loads(repaired, strict=False)


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
        data = _safe_json_loads(text)
    except json.JSONDecodeError:
        m_arr = re.search(r"\[\s*\{.*\}\s*\]", text, re.DOTALL)
        if m_arr:
            try:
                data = _safe_json_loads(m_arr.group(0))
            except json.JSONDecodeError as exc:
                raise JSONParseError(f"Không thể giải mã mảng JSON OCR: {exc}")
        else:
            m_dict = re.search(
                r"\{\s*\"(?:questions|lines|question_id|title|grade_level|student_name_detected)\".*\}",
                text,
                re.DOTALL,
            )
            candidate_obj = m_dict.group(0) if m_dict else _extract_balanced_json_object(text)
            if candidate_obj:
                try:
                    data = _safe_json_loads(candidate_obj)
                except json.JSONDecodeError as exc:
                    raise JSONParseError(f"Không thể giải mã đối tượng JSON OCR: {exc}")
            else:
                raise JSONParseError("Không tìm thấy cấu trúc JSON hợp lệ trong phản hồi OCR.")

    root_grade = ""
    root_text = ""
    root_student_name = ""
    root_student_class = ""
    root_paper_brand = ""
    root_handwriting = ""
    root_orientation = 0
    if not isinstance(data, list):
        if isinstance(data, dict):
            root_grade = str(data.get("grade_level") or "")
            root_text = str(data.get("title") or data.get("question_text") or "")
            root_student_name = str(data.get("student_name_detected") or "")
            root_student_class = str(data.get("student_class_detected") or "")
            root_paper_brand = str(data.get("paper_brand_detected") or "")
            root_handwriting = str(data.get("handwriting_style") or "")
            try:
                deg = int(data.get("orientation_deg") or 0)
                root_orientation = deg if deg in (0, 90, 180, 270) else 0
            except Exception:
                root_orientation = 0
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

            conf = _safe_ocr_float(line.get("confidence"), 0.9)
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
        q_conf = _safe_ocr_float(item.get("confidence"), 0.9)
        if active_lines:
            q_conf = round(sum(l.confidence for l in active_lines) / len(active_lines), 2)

        has_unclear = any(l.unclear and not l.is_strikethrough for l in parsed_lines) or bool(item.get("has_unclear", False))

        q_obj = ExtractedQuestion(
            question_id=q_id,
            question_text=str(item.get("question_text") or item.get("title") or root_text or ""),
            grade_level=str(item.get("grade_level") or root_grade or ""),
            student_name_detected=str(item.get("student_name_detected") or root_student_name or ""),
            student_class_detected=str(item.get("student_class_detected") or root_student_class or ""),
            paper_brand_detected=str(item.get("paper_brand_detected") or root_paper_brand or ""),
            handwriting_style=str(item.get("handwriting_style") or root_handwriting or ""),
            orientation_deg=root_orientation,
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
        self.openai_base_url: str = ""
        self.openai_extra_headers: dict[str, str] = {}
        self.active_provider_name: str = ""
        self.active_account_id: str = ""
        self.gemini_oauth_credentials: dict | None = None
        self.model_name = settings.DEFAULT_VISION_MODEL
        self.openai_model = settings.DEFAULT_OPENAI_MODEL
        self.last_usage: dict = {
            "provider": "mock",
            "model": "mock",
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

    def _openai_chat_json(self, client, messages: list[dict], max_tokens: int | None = None):
        kwargs: dict = {
            "model": self.openai_model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as exc:
            err_s = str(exc).lower()
            if "response_format" in err_s or "400" in err_s or "json_object" in err_s:
                kwargs.pop("response_format", None)
                return client.chat.completions.create(**kwargs)
            raise

    def extract_latex(
        self,
        image_bytes: bytes,
        applied_transforms: list[str] | None = None,
        scenario: str = "default",
    ) -> list[ExtractedQuestion]:
        """Trích xuất toàn bộ bài làm từ 1 ảnh."""
        return self.extract_latex_multipage([image_bytes], applied_transforms=applied_transforms, scenario=scenario)

    def extract_latex_with_usage(
        self,
        image_bytes: bytes,
        applied_transforms: list[str] | None = None,
        scenario: str = "default",
    ) -> tuple[list[ExtractedQuestion], dict]:
        """Phiên bản thread-safe trả về đồng thời (questions, usage_dict) cho xử lý song song folder."""
        provider = self._active_provider()
        usage_out: dict = {
            "provider": provider,
            "model": "mock",
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 0,
        }
        if provider == "openai":
            qs = self._call_openai_vision_with_retry([image_bytes], usage_sink=usage_out)
            self.last_usage = dict(usage_out)
            return qs, usage_out
        if provider == "gemini":
            qs = self._call_gemini_vision_with_retry([image_bytes], usage_sink=usage_out)
            self.last_usage = dict(usage_out)
            return qs, usage_out
        qs = self._mock_extraction(scenario=scenario)
        self.last_usage = dict(usage_out)
        return qs, usage_out

    def reread_question_from_image(
        self,
        image_bytes: bytes,
        question_id: str,
        expected_prompt: str = "",
        previous_lines: list[str] | None = None,
        suspected_issue: str = "",
    ) -> tuple[ExtractedQuestion | None, dict]:
        """
        2-Pass Targeted OCR Verification (v0.4.0):
        Soi lại thật kỹ ảnh gốc độ phân giải cao cho RIÊNG 1 câu bị nghi lỗi/điểm thấp
        để phát hiện các lỗi OCR vi mô (sót `= 1`, nhầm `2 . 250.000` thành `2.250.000`, dấu âm `-`, số mũ).
        CẤM tự bịa cho đúng nếu học sinh thực sự viết sai!
        """
        provider = self._active_provider()
        usage_out: dict = {
            "provider": provider,
            "model": "mock",
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 0,
        }
        if provider == "mock":
            return None, usage_out

        prev_block = "\n".join(f"  - {ln}" for ln in (previous_lines or [])) or "  (Chưa có)"
        prompt = f"""Bạn đang thực hiện KIỂM TRA LẠI ẢNH GỐC (2-PASS FORENSIC OCR) cho RIÊNG câu `{question_id}`.
- Đề bài chuẩn (nếu có): {expected_prompt or '(Không cung cấp)'}
- Bản OCR lần 1 đọc được:
{prev_block}
- Nghi vấn cần soi lại thật kỹ trên ảnh gốc: {suspected_issue or 'Kiểm tra lại từng dấu phép tính và số liệu.'}

QUY TẮC SỐNG CÒN:
1. Chỉ trích xuất RIÊNG bài làm của câu `{question_id}` trên ảnh này.
2. Soi cực kỹ: dấu bằng cuối dòng (vd `= 1`, `= 0`), dấu chấm nhân (vd `2 . 250.000` tức `2 \\times 250000` khác với `2.250.000`), dấu âm `-`, phân số, số mũ, dấu ngoặc.
3. TUYỆT ĐỐI TRUNG THỰC: Chỉ sửa lại nếu ảnh gốc THỰC SỰ viết khác bản OCR lần 1. Nếu học sinh thực sự viết sai trên giấy, PHẢI giữ nguyên đúng cái sai của học sinh!
4. Bỏ qua các dấu tích/số điểm màu ĐỎ của giáo viên.
5. Trong JSON, mọi dấu `\\` của LaTeX PHẢI viết thành `\\\\`.

TRẢ VỀ JSON OBJECT HỢP LỆ:
{{
  "questions": [
    {{
      "question_id": "{question_id}",
      "question_text": "{expected_prompt}",
      "lines": [
        {{"line_no": 1, "raw_text": "...", "latex": "...", "line_type": "equation", "confidence": 0.96, "is_strikethrough": false, "unclear": false}}
      ]
    }}
  ]
}}
"""
        hi_res = _resize_for_vision_api(image_bytes, max_side=2400)
        try:
            if provider == "gemini":
                from google.genai import types
                from backend.app.services.provider_auth import build_gemini_client

                client = build_gemini_client(
                    api_key=self.api_key,
                    oauth_credentials=self.gemini_oauth_credentials,
                    account_id=self.active_account_id,
                )
                candidates = []
                for m in (
                    self.model_name,
                    "gemini-3.1-flash-lite",
                    "gemini-3.1-flash-lite-preview",
                    "gemini-flash-lite-latest",
                    "gemini-3-flash-preview",
                ):
                    if m and m not in candidates:
                        candidates.append(m)

                raw_text = ""
                for model_id in candidates:
                    try:
                        cfg_kwargs: dict = {
                            "response_mime_type": "application/json",
                            "temperature": 0.0,
                            "max_output_tokens": 4096,
                        }
                        try:
                            cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
                        except Exception:
                            pass
                        try:
                            resp = client.models.generate_content(
                                model=model_id,
                                contents=[prompt, types.Part.from_bytes(data=hi_res, mime_type="image/jpeg")],
                                config=types.GenerateContentConfig(**cfg_kwargs),
                            )
                        except Exception as tc_err:
                            if any(k in str(tc_err).lower() for k in ("thinking", "invalid_argument", "400")) and "thinking_config" in cfg_kwargs:
                                cfg_kwargs.pop("thinking_config", None)
                                resp = client.models.generate_content(
                                    model=model_id,
                                    contents=[prompt, types.Part.from_bytes(data=hi_res, mime_type="image/jpeg")],
                                    config=types.GenerateContentConfig(**cfg_kwargs),
                                )
                            else:
                                raise
                        um = getattr(resp, "usage_metadata", None)
                        if um:
                            in_t = int(getattr(um, "prompt_token_count", 0) or 0)
                            out_t = int(getattr(um, "candidates_token_count", 0) or 0)
                            th_t = int(getattr(um, "thoughts_token_count", 0) or 0)
                            tot_t = int(getattr(um, "total_token_count", in_t + out_t + th_t) or (in_t + out_t + th_t))
                            usage_out.update(
                                {
                                    "provider": "gemini",
                                    "model": model_id,
                                    "input_tokens": in_t,
                                    "output_tokens": out_t,
                                    "thinking_tokens": th_t,
                                    "total_tokens": tot_t,
                                }
                            )
                        raw_text = resp.text or ""
                        break
                    except Exception:
                        continue
                if not raw_text:
                    return None, usage_out
                parsed = parse_and_validate_ocr_json(raw_text)
                return (parsed[0] if parsed else None), usage_out

            if provider == "openai":
                import base64
                from backend.app.services.provider_auth import build_openai_client

                client = build_openai_client(
                    api_key=self.openai_api_key,
                    base_url=self.openai_base_url,
                    extra_headers=self.openai_extra_headers,
                    timeout=45.0,
                )
                b64 = base64.b64encode(hi_res).decode("utf-8")
                resp = self._openai_chat_json(
                    client,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "high"}},
                            ],
                        }
                    ],
                )
                u = getattr(resp, "usage", None)
                if u:
                    in_t = int(getattr(u, "prompt_tokens", 0) or 0)
                    out_t = int(getattr(u, "completion_tokens", 0) or 0)
                    usage_out.update(
                        {
                            "provider": self.active_provider_name or "openai",
                            "model": self.openai_model,
                            "input_tokens": in_t,
                            "output_tokens": out_t,
                            "thinking_tokens": 0,
                            "total_tokens": in_t + out_t,
                        }
                    )
                parsed = parse_and_validate_ocr_json(resp.choices[0].message.content or "")
                return (parsed[0] if parsed else None), usage_out
        except Exception as exc:
            print(f"[Agent 1 2-Pass Warning] Không thể soi lại {question_id}: {exc}")
        return None, usage_out

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
        self.last_usage = {
            "provider": provider,
            "model": "mock",
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "total_tokens": 0,
        }
        if provider == "openai":
            return self._call_openai_vision_with_retry(pages_bytes)
        if provider == "gemini":
            return self._call_gemini_vision_with_retry(pages_bytes)
        return self._mock_extraction(scenario=scenario)

    def _call_openai_vision_with_retry(
        self,
        pages_bytes: list[bytes],
        max_retries: int = 3,
        usage_sink: dict | None = None,
    ) -> list[ExtractedQuestion]:
        """Gọi OpenAI / OpenAI-compatible Vision API cho 1 hoặc nhiều trang kèm exponential backoff."""
        from backend.app.services.provider_auth import build_openai_client

        client = build_openai_client(
            api_key=self.openai_api_key,
            base_url=self.openai_base_url,
            extra_headers=self.openai_extra_headers,
            timeout=45.0,
        )
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
                resp = self._openai_chat_json(
                    client,
                    messages=[
                        {"role": "system", "content": FULL_PAGE_HANDWRITING_OCR_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    max_tokens=4096,
                )
                raw_content = resp.choices[0].message.content or ""
                usage = getattr(resp, "usage", None)
                if usage:
                    in_t = int(getattr(usage, "prompt_tokens", 0) or 0)
                    out_t = int(getattr(usage, "completion_tokens", 0) or 0)
                    tot_t = int(getattr(usage, "total_tokens", in_t + out_t) or (in_t + out_t))
                    u = {
                        "provider": self.active_provider_name or "openai",
                        "model": self.openai_model,
                        "input_tokens": in_t,
                        "output_tokens": out_t,
                        "thinking_tokens": 0,
                        "total_tokens": tot_t,
                    }
                    self.last_usage = u
                    if usage_sink is not None:
                        usage_sink.update(u)
                return parse_and_validate_ocr_json(raw_content)
            except OCRLowConfidenceError:
                raise
            except Exception as e:
                last_exc = e
                err_s = str(e)
                # Lỗi 400/401/403/404 không thể tự hết khi retry -> báo lỗi ngay
                if any(code in err_s for code in ("404", "400", "401", "403", "NOT_FOUND", "INVALID_ARGUMENT")):
                    break
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
        max_retries: int = 2,
        usage_sink: dict | None = None,
    ) -> list[ExtractedQuestion]:
        """Gọi Gemini Vision API kèm fallback tự động giữa các model khi gặp 503/429/404."""
        from google.genai import types
        from backend.app.services.provider_auth import build_gemini_client

        client = build_gemini_client(
            api_key=self.api_key,
            oauth_credentials=self.gemini_oauth_credentials,
            account_id=self.active_account_id,
        )
        contents: list = []
        for idx, page_bytes in enumerate(pages_bytes, start=1):
            opt_bytes = _resize_for_vision_api(page_bytes)
            contents.append(f"Trang {idx}/{len(pages_bytes)}:")
            contents.append(types.Part.from_bytes(data=opt_bytes, mime_type="image/jpeg"))
        contents.append(FULL_PAGE_HANDWRITING_OCR_PROMPT)

        # Chuỗi fallback chỉ gồm các model có quota thật (loại bỏ Pro limit=0 trên Free Tier)
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
            delay = 1.5
            for attempt in range(1, max_retries + 1):
                try:
                    # Tắt thinking budget cho bước OCR thuần để giảm độ trễ từ ~15s xuống ~3-5s
                    cfg_kwargs: dict = {
                        "response_mime_type": "application/json",
                        "temperature": 0.0,
                        "max_output_tokens": 8192,
                    }
                    try:
                        cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
                    except Exception:
                        pass

                    try:
                        response = client.models.generate_content(
                            model=model_id,
                            contents=contents,
                            config=types.GenerateContentConfig(**cfg_kwargs),
                        )
                    except Exception as tc_err:
                        if any(k in str(tc_err).lower() for k in ("thinking", "invalid_argument", "400")) and "thinking_config" in cfg_kwargs:
                            cfg_kwargs.pop("thinking_config", None)
                            response = client.models.generate_content(
                                model=model_id,
                                contents=contents,
                                config=types.GenerateContentConfig(**cfg_kwargs),
                            )
                        else:
                            raise

                    if not response.text:
                        raise GeminiAPIError("Gemini API trả về nội dung rỗng.", error_code="E008")
                    um = getattr(response, "usage_metadata", None)
                    if um:
                        in_t = int(getattr(um, "prompt_token_count", 0) or 0)
                        out_t = int(getattr(um, "candidates_token_count", 0) or 0)
                        th_t = int(getattr(um, "thoughts_token_count", 0) or 0)
                        tot_t = int(getattr(um, "total_token_count", in_t + out_t + th_t) or (in_t + out_t + th_t))
                        u = {
                            "provider": "gemini",
                            "model": model_id,
                            "input_tokens": in_t,
                            "output_tokens": out_t,
                            "thinking_tokens": th_t,
                            "total_tokens": tot_t,
                        }
                        self.last_usage = u
                        if usage_sink is not None:
                            usage_sink.update(u)
                    return parse_and_validate_ocr_json(response.text)
                except OCRLowConfidenceError:
                    raise
                except Exception as e:
                    last_exc = e
                    err_s = str(e)
                    # Lỗi xác thực key (401/403) -> dừng ngay
                    if any(code in err_s for code in ("401", "403", "API_KEY_INVALID")):
                        raise GeminiAPIError(f"Gemini API Key không hợp lệ: {err_s}", error_code="E008")
                    # Model 404 hoặc 429 hết quota -> chuyển NGAY sang model dự phòng kế tiếp (không lãng phí sleep)
                    if any(code in err_s for code in ("404", "NOT_FOUND", "429", "RESOURCE_EXHAUSTED", "limit: 0")):
                        print(f"[Agent 1 Fallback] Model {model_id} hết quota/không khả dụng, chuyển ngay sang model kế tiếp...")
                        break
                    # 503 tạm thời: thử lại 1 lần sau delay ngắn, nếu vẫn lỗi thì chuyển model kế tiếp
                    if attempt < max_retries:
                        print(f"[Agent 1 Retry] Model {model_id} gặp lỗi tạm ({err_s[:80]}), thử lại sau {delay}s...")
                        time.sleep(delay)
                        delay *= 2.0
                    else:
                        print(f"[Agent 1 Fallback] Model {model_id} quá tải, chuyển model dự phòng kế tiếp...")
                        break

        err_msg = str(last_exc or "Unknown Gemini Vision error")
        err_code = "E006" if ("429" in err_msg or "quota" in err_msg.lower() or "resource" in err_msg.lower()) else "E008"
        raise GeminiAPIError(f"Gemini Vision OCR thất bại trên toàn bộ các model dự phòng: {err_msg}", error_code=err_code)

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
