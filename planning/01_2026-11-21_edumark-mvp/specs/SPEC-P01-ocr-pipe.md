# SPEC-P01 — Agent 1: Vision OCR Pipeline

> **Phạm vi:** Nhận ảnh bài viết tay → trả `OCRResult` (structured JSON).
> **Tier:** 🟠 (Standard — logic tính năng chính)
> **Modules:** `services/preprocessor.py`, `services/agent1_ocr.py`, `core/cache.py`

---

## 1. Luồng Xử Lý (Pipeline Flow)

```
Ảnh gốc (JPEG/PNG)
    │
    ▼
[Preprocessor]
    ├── EXIF normalize (auto-rotate theo metadata)
    ├── Resize nếu > 4096px cạnh dài
    ├── Contrast enhancement (CLAHE)
    ├── Blur detection (Laplacian variance)
    │   └── variance < BLUR_THRESHOLD → warning "blurry"
    └── Output: processed image bytes
    │
    ▼
[MD5 Cache Check]
    ├── cache hit  → trả OCRResult từ cache (cached=True)
    └── cache miss → tiếp tục
    │
    ▼
[Gemini Vision API Call]
    ├── System prompt + ảnh → structured JSON response
    ├── Parse JSON → OCRResult
    └── Lưu vào cache (TTL = 24h)
    │
    ▼
[Output Validation]
    ├── Validate OCRResult schema
    ├── Confidence check: overall < 0.5 → raise E004
    └── Return OCRResult
```

---

## 2. Preprocessor — Chi Tiết Kỹ Thuật

### 2.1. EXIF Auto-Rotate

```python
def normalize_exif(image: Image.Image) -> Image.Image:
    """Xoay ảnh theo EXIF orientation tag. Luôn trả ảnh orientation=1."""
```

**BẮT BUỘC:** Xử lý tất cả 8 giá trị EXIF orientation (1-8). Ảnh không có EXIF → giữ nguyên.

### 2.2. Resize

```python
def resize_if_needed(image: Image.Image, max_side: int = 4096) -> Image.Image:
    """Resize giữ tỷ lệ nếu cạnh dài > max_side. Dùng Lanczos resampling."""
```

**BẮT BUỘC:** Giữ aspect ratio. Dùng `Image.LANCZOS`. Ảnh nhỏ hơn max → không resize.

### 2.3. Contrast Enhancement

```python
def enhance_contrast(image: Image.Image) -> Image.Image:
    """Tăng tương phản bằng CLAHE hoặc ImageEnhance. Factor = 1.3."""
```

### 2.4. Blur Detection

```python
def detect_blur(image: Image.Image, threshold: float = 100.0) -> tuple[bool, float]:
    """
    Trả (is_blurry, variance).
    Dùng Laplacian variance trên grayscale image.
    variance < threshold → is_blurry = True.
    """
```

**BẮT BUỘC:** Luôn trả variance để log. Ảnh blur vẫn tiếp tục OCR (không block), chỉ thêm warning.

**CẤM:** CẤM reject ảnh chỉ vì blur — teacher có thể muốn thử OCR dù ảnh mờ.

---

## 3. Gemini Vision API — Prompt Engineering

### 3.1. System Prompt Template

```
Bạn là chuyên gia OCR cho bài thi Toán viết tay tiếng Việt (THCS, lớp 8-9).

NHIỆM VỤ:
Phân tích ảnh bài làm và trích xuất TỪNG BƯỚC GIẢI thành cấu trúc JSON.

QUY TẮC:
1. Mỗi dòng/bước giải = 1 ExtractedLine
2. Phân loại line_type chính xác:
   - "condition": ĐKXĐ, điều kiện xác định
   - "equation": phương trình, biểu thức, phép biến đổi
   - "text": chú thích, giải thích bằng lời
   - "conclusion": kết luận, đáp số cuối cùng
   - "scratch": nháp, gạch xóa
   - "unknown": không đọc được
3. Viết LaTeX cho biểu thức toán: dùng $ ... $ hoặc \( ... \)
4. Confidence: 1.0 = rất rõ, 0.5 = khó đọc, 0.0 = không đọc được
5. Nếu có nhiều câu hỏi, tách riêng từng question_id

OUTPUT FORMAT: JSON theo schema OCRResult (không markdown, không giải thích thêm)
```

### 3.2. API Call Parameters

```python
GEMINI_OCR_CONFIG = {
    "model": "gemini-2.0-flash",        # hoặc "gemini-1.5-flash"
    "temperature": 0.1,                   # Deterministic cho OCR
    "max_output_tokens": 4096,
    "response_mime_type": "application/json",
}
```

**BẮT BUỘC:**
- `temperature` ≤ 0.2 cho OCR (cần deterministic).
- `response_mime_type` = `"application/json"` để enforce structured output.
- Retry logic: max 3 retries, exponential backoff (1s, 2s, 4s).

**CẤM:**
- CẤM dùng `temperature > 0.5` cho OCR.
- CẤM gửi ảnh chưa qua preprocessor lên API.

---

## 4. Cache Layer

### 4.1. Cache Key

```python
def compute_image_hash(image_bytes: bytes) -> str:
    """MD5 hex digest lowercase. Dùng hashlib.md5."""
    return hashlib.md5(image_bytes).hexdigest()
```

### 4.2. Cache Interface

```python
class CacheEngine(Protocol):
    async def get(self, key: str) -> OCRResult | None: ...
    async def set(self, key: str, value: OCRResult, ttl_seconds: int = 86400) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def clear(self) -> None: ...
```

**BẮT BUỘC:**
- TTL mặc định = 86400 (24 giờ).
- Cache key = MD5 hash của **processed** image bytes (sau preprocessor).
- Cache value = serialized OCRResult (JSON string).

**CẤM:**
- CẤM cache ảnh gốc (trước preprocessor) — sẽ miss khi preprocessor thay đổi.
- CẤM cache GradingResult — chỉ cache OCRResult.

---

## 5. Output Validation

```python
def validate_ocr_result(result: OCRResult) -> OCRResult:
    """
    Post-validation sau khi parse Gemini response.
    Raises ValueError nếu invalid.
    """
```

**Checks bắt buộc:**
1. `len(result.questions) > 0` — ít nhất 1 câu hỏi.
2. Mọi `line.confidence` trong [0.0, 1.0].
3. `overall_confidence` = mean(all line confidences).
4. `image_hash` đúng 32 ký tự hex.
5. `overall_confidence < 0.5` → raise `OCRLowConfidenceError` (map to E004).

---

## 6. Bảng Lỗi Module

| Lỗi                        | Nguyên nhân                      | Hành vi bắt buộc                                    |
|:----------------------------|:---------------------------------|:-----------------------------------------------------|
| `PreprocessorError`         | Ảnh corrupt, format lạ           | Trả E001, log ảnh lỗi                               |
| `OCRLowConfidenceError`     | Ảnh mờ/xấu, confidence < 0.5    | Trả E004 + warnings, hiện ảnh để teacher quyết       |
| `GeminiAPIError`            | API timeout/quota/unavailable    | Retry 3 lần → E008 nếu vẫn fail                      |
| `JSONParseError`            | Gemini trả format sai            | Retry 1 lần với prompt nhắc "JSON only" → E005       |
| `CacheError`                | Redis/memory lỗi                 | Log warning, bỏ qua cache, tiếp tục OCR              |
