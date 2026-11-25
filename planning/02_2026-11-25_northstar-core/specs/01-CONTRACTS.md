# 01-CONTRACTS — Hợp Đồng Dữ Liệu & API (v0.2.0)

> **Tier:** 🔴 | **Bổ sung & tương thích ngược hoàn toàn với `v0.1.0`**

---

## 1. Schema Bổ Sung (`backend/app/models/schemas.py`)

- `ExtractedLine`:
  - `page_no: int = 1` — Số trang ảnh chụp mà dòng xuất hiện.
  - `is_strikethrough: bool = False` — `True` nếu học sinh gạch bỏ dòng này (Agent 2 bắt buộc bỏ qua khi chấm).
- `ImageQualityReport`:
  - `brightness: float = 128.0` — Độ sáng trung bình [0..255].
  - `is_too_dark: bool = False`, `is_overexposed: bool = False`.
  - `perspective_corrected: bool = False`, `shadow_removed: bool = False`.
  - `warnings: list[str] = []`, `recommendation: str = ""`.
- `MathVerificationSummary`:
  - `equations_checked: int = 0`, `valid_transitions: int = 0`, `invalid_transitions: int = 0`.
  - `detected_roots: list[str] = []`, `issues: list[str] = []`, `summary: str = ""`.
- `SubmissionGradingRecord`:
  - `page_count: int = 1`
  - `detected_student_name: str = ""`
  - `math_verification: MathVerificationSummary | None = None`

---

## 2. REST API Endpoints Mới & Cập Nhật

| Method | Path | Mô tả | Error Codes |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` & `/api/v1/health` | Health check trả `{status, service, version, db_ok}` | — |
| `PUT` | `/api/v1/exams/{exam_id}` | Cập nhật barem đề thi (lưu SQLite) | `404 E002` |
| `DELETE` | `/api/v1/exams/{exam_id}` | Xóa đề thi (cấm xóa `AUTO`) | `400 E001`, `404 E002` |
| `GET` | `/api/v1/submissions` | Danh sách bài đã chấm (filter `exam_id`, `status`, `limit`) | — |
| `POST` | `/api/v1/submissions/quality-check` | Kiểm tra độ nét & ánh sáng tức thì cho ảnh chụp điện thoại | `400 E001` |
| `POST` | `/api/v1/submissions/grade-multipage` | Chấm 1 bài gồm `1..10` ảnh chụp (`files: list[UploadFile]`) | `400 E001`, `413 E003`, `429 E006`, `503 E008` |
| `POST` | `/api/v1/submissions/batch` | Thêm `async_mode: bool = Form(False)` để chạy nền qua `BackgroundTasks` | `400 E001`, `404 E002` |
| `GET` | `/api/v1/exports/excel/{exam_id}` | Xóa fallback rò rỉ điểm exam khác; thêm `approved_only: bool = False` | `400 E001` |
| `GET` | `/app` | Giao diện Mobile PWA Camera Capture & Grading | — |

---

## 3. Hành Vi Lỗi Bắt Buộc
- Khi Live API key đã được cấu hình (`vision_agent.is_live == True`) mà gọi OpenAI/Gemini thất bại:
  - Rate limit → HTTP `429` với `{"error_code": "E006", "message": ...}`.
  - API unavailable / timeout / parse error → HTTP `503` với `{"error_code": "E008", "message": ...}`.
  - **CẤM** trả `200 OK` kèm dữ liệu bài mẫu giả!
