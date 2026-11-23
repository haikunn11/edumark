# 01-CONTRACTS — Data Contracts, API Schema & Error Codes

> **Mục đích:** Định nghĩa tất cả contracts giữa các module. Mọi function signature, endpoint, schema đều là BẤT BIẾN trong scope MVP.
> **Tier:** 🔴 (Strongest — contract dữ liệu nền tảng)

---

## 1. Pydantic V2 Core Schemas

### 1.1. Rubric System (Input — Teacher tạo)

```python
class RubricCriterion(BaseModel):
    """Một tiêu chí chấm điểm cho một bước giải."""
    criterion_id: str                    # Unique ID, e.g. "dkxd", "bien_doi_1"
    description: str                     # Mô tả tiêu chí
    max_points: float                    # Điểm tối đa
    keywords: list[str]                  # Từ khóa nhận diện bước (regex patterns)
    error_patterns: list[str] = []       # Mẫu lỗi phổ biến
    partial_credit_rules: dict = {}      # Quy tắc cho điểm thành phần

class QuestionRubric(BaseModel):
    """Barem chấm cho một câu hỏi."""
    question_id: str                     # e.g. "1a", "2b"
    question_text: str                   # Đề bài
    total_points: float                  # Tổng điểm câu
    criteria: list[RubricCriterion]      # Danh sách tiêu chí chấm
    allow_error_propagation: bool = True # Cho phép Error Propagation

class ExamRubric(BaseModel):
    """Barem chấm toàn bộ bài thi."""
    exam_id: str                         # Unique exam ID
    exam_title: str                      # Tên bài thi
    subject: Literal["math", "english"]  # Môn học
    grade: int                           # Khối lớp (8, 9)
    total_points: float                  # Tổng điểm
    questions: list[QuestionRubric]      # Danh sách câu hỏi
    created_at: datetime                 # Thời điểm tạo
    created_by: str = "teacher"          # Người tạo
```

**BẮT BUỘC:**
- `sum(q.total_points for q in questions)` PHẢI bằng `total_points`. Pydantic validator enforce.
- `sum(c.max_points for c in criteria)` PHẢI bằng `total_points` của question tương ứng.
- `criterion_id` PHẢI unique trong scope 1 question.

**CẤM:**
- CẤM thêm field vào schema mà không cập nhật `01-CONTRACTS.md` trước.
- CẤM dùng `Any` type trong schema.

---

### 1.2. OCR Output (Agent 1 → Agent 2)

```python
class ExtractedLine(BaseModel):
    """Một dòng/bước giải được OCR trích xuất."""
    line_index: int                      # Thứ tự dòng (0-based)
    raw_text: str                        # Text gốc OCR
    latex: str = ""                      # Biểu diễn LaTeX (nếu có)
    confidence: float                    # 0.0 - 1.0
    line_type: Literal[
        "equation", "text", "condition",
        "conclusion", "scratch", "unknown"
    ]                                    # Phân loại dòng

class ExtractedQuestion(BaseModel):
    """Toàn bộ bài giải của 1 câu hỏi."""
    question_id: str                     # Map với QuestionRubric.question_id
    lines: list[ExtractedLine]           # Các dòng/bước giải
    overall_confidence: float            # Trung bình confidence
    ocr_warnings: list[str] = []         # Cảnh báo (blur, unclear, etc.)

class OCRResult(BaseModel):
    """Output hoàn chỉnh của Agent 1."""
    submission_id: str                   # Unique ID bài nộp
    image_hash: str                      # MD5 hash ảnh gốc
    questions: list[ExtractedQuestion]   # Kết quả OCR từng câu
    processing_time_ms: int              # Thời gian xử lý (ms)
    model_used: str                      # e.g. "gemini-2.0-flash"
    cached: bool = False                 # True nếu từ cache
```

**BẮT BUỘC:**
- `image_hash` PHẢI là MD5 hex digest lowercase 32 ký tự.
- `confidence` trong range [0.0, 1.0]. Validator enforce.
- `overall_confidence` = mean([line.confidence for line in lines]).

---

### 1.3. Grading Output (Agent 2 → Dashboard)

```python
class CriterionEvaluation(BaseModel):
    """Đánh giá một tiêu chí của bài làm."""
    criterion_id: str                    # Map với RubricCriterion
    earned_points: float                 # Điểm đạt được
    max_points: float                    # Điểm tối đa
    matched: bool                        # Có match với bài làm không
    feedback: str                        # Nhận xét chi tiết
    error_type: Literal[
        "none", "calculation", "method",
        "missing_step", "wrong_conclusion",
        "error_propagation"
    ] = "none"

class QuestionGradingResult(BaseModel):
    """Kết quả chấm 1 câu hỏi."""
    question_id: str
    earned_points: float
    total_points: float
    criteria_evaluations: list[CriterionEvaluation]
    error_propagation_detected: bool = False
    affected_steps: list[str] = []       # criterion_ids bị ảnh hưởng
    confidence: float                    # 0.0 - 1.0
    confidence_level: Literal["green", "yellow", "red"]

class GradingResult(BaseModel):
    """Kết quả chấm toàn bộ bài thi."""
    submission_id: str
    exam_id: str
    student_id: str = ""
    total_earned: float
    total_possible: float
    percentage: float                    # 0-100
    questions: list[QuestionGradingResult]
    overall_confidence: float
    overall_confidence_level: Literal["green", "yellow", "red"]
    status: Literal[
        "pending_review", "approved",
        "overridden", "rejected"
    ] = "pending_review"
    graded_at: datetime
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    teacher_notes: str = ""
```

**BẮT BUỘC:**
- `earned_points` ≤ `max_points` cho mọi criterion. Validator enforce.
- `total_earned` = sum(q.earned_points for q in questions).
- `percentage` = round(total_earned / total_possible * 100, 1).
- `confidence_level` tính theo ngưỡng: ≥0.9 → "green", ≥0.7 → "yellow", else → "red".
- `status` khởi tạo LUÔN là `"pending_review"`.

**CẤM:**
- CẤM set `status = "approved"` ở Agent 2. Chỉ Teacher review mới được approve.
- CẤM `earned_points < 0`.

---

## 2. REST API Endpoints

### 2.1. Exam Management

```
POST   /api/exams                    → Tạo exam mới (body: ExamRubric)
GET    /api/exams                    → List tất cả exams
GET    /api/exams/{exam_id}          → Chi tiết 1 exam
PUT    /api/exams/{exam_id}          → Cập nhật rubric
DELETE /api/exams/{exam_id}          → Xóa exam
```

### 2.2. Submission & Grading

```
POST   /api/submissions/grade        → Upload ảnh + chấm điểm
  Request:  multipart/form-data
    - file: UploadFile (image/jpeg, image/png, application/pdf)
    - exam_id: str
    - student_id: str (optional)
  Response: GradingResult (JSON)
  Errors: 400 (invalid file), 404 (exam not found), 422 (OCR failed)

POST   /api/submissions/batch        → Upload ZIP/PDF nhiều bài
  Request:  multipart/form-data
    - file: UploadFile (application/zip, application/pdf)
    - exam_id: str
  Response: { job_id: str, total_submissions: int }
  Errors: 400 (invalid archive), 413 (file too large, >50MB)

GET    /api/submissions/batch/{job_id} → Trạng thái batch job
  Response: { job_id, status, completed, total, results: GradingResult[] }
```

### 2.3. Teacher Review (HITL)

```
POST   /api/submissions/review       → Teacher approve/override
  Request: JSON
    - submission_id: str
    - action: "approve" | "override" | "reject"
    - overrides: dict[question_id, { earned_points, feedback }] (optional)
    - teacher_notes: str (optional)
    - reviewed_by: str
  Response: GradingResult (updated)
  Errors: 404 (submission not found), 400 (invalid override points)
```

### 2.4. Export

```
GET    /api/exports/excel/{exam_id}   → Download Excel điểm
  Query params: format=vneddu|smas|standard (default: standard)
  Response: application/vnd.openxmlformats (xlsx file)
  Errors: 404 (exam not found), 400 (no approved submissions)
```

### 2.5. Health

```
GET    /health                        → { status: "ok", version: str }
```

---

## 3. Bảng Phân Loại Lỗi & Hành Vi Caller

| Error Code | HTTP | Loại lỗi                  | Hành vi Caller bắt buộc                        |
|:-----------|:----:|:--------------------------|:------------------------------------------------|
| `E001`     | 400  | Invalid file format        | Hiện thông báo "Chỉ hỗ trợ JPEG, PNG, PDF"     |
| `E002`     | 404  | Exam not found             | Redirect về trang chọn exam                      |
| `E003`     | 413  | File too large (>50MB)     | Hiện "File quá lớn, tối đa 50MB"                |
| `E004`     | 422  | OCR failed (blur/unclear)  | Hiện ảnh + "Ảnh không rõ, vui lòng chụp lại"    |
| `E005`     | 422  | Grading failed             | Hiện OCR result + "Không chấm được, kiểm tra lại barem" |
| `E006`     | 429  | API rate limit             | Retry after `Retry-After` header, max 3 retries  |
| `E007`     | 500  | Internal server error      | Log error, hiện "Lỗi hệ thống, thử lại sau"     |
| `E008`     | 503  | Gemini API unavailable     | Fallback sang mock/cache, thông báo user          |

**BẮT BUỘC:**
- Mọi error response PHẢI có format: `{ "error_code": "E0xx", "message": str, "details": dict | null }`.
- Caller (frontend) PHẢI handle tất cả 8 error codes trên.
- E006 (rate limit): Caller retry với exponential backoff, cap 3 lần.

**CẤM:**
- CẤM trả error 500 cho validation errors (phải 400/422).
- CẤM swallow errors — mọi exception PHẢI log + trả response.
