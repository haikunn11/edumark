# OPERATIONS — Vận Hành & Bảo Mật (v0.2.0)

> **Tier:** 🟢

## 1. Lưu Trữ SQLite
- File mặc định: `backend/data/edumark.db` (tự tạo bảng `exams`, `submissions`, `batch_jobs` khi boot).
- Bật `PRAGMA journal_mode=WAL;` và `PRAGMA synchronous=NORMAL;` để hỗ trợ đọc/ghi đồng thời an toàn trên FastAPI.
- Có thể đổi đường dẫn qua biến môi trường `EDUMARK_DB_PATH` (dùng `:memory:` hoặc temp file trong test).

## 2. Bảo Mật Cấu Hình Runtime
- Biến môi trường `ADMIN_API_TOKEN` (tùy chọn):
  - Khi `ENVIRONMENT != "local"` hoặc `ADMIN_API_TOKEN` được set: `POST /api/v1/config/key` yêu cầu header `X-Admin-Token` khớp chính xác, nếu không trả `403`.
  - Khi `ENVIRONMENT == "local"` và không set `ADMIN_API_TOKEN`: cho phép cấu hình thuận tiện trên máy dev.
- CORS: mặc định cho phép origin cấu hình qua `CORS_ORIGINS` (mặc định `*` với `allow_credentials=False` để đúng chuẩn trình duyệt).

## 3. Packaging & Files Chuẩn
- `requirements.txt`: khóa đủ dependencies chạy server + test (`fastapi`, `uvicorn`, `pydantic-settings`, `python-multipart`, `pillow`, `opencv-python-headless`, `numpy`, `pymupdf`, `openpyxl`, `sympy`, `openai`, `google-genai`, `pytest`, `httpx`).
- `.env.example`: mẫu đầy đủ các biến cấu hình cho dev và staging.
- `.gitignore`: loại trừ `backend/data/uploads/`, `backend/data/*.db*`, `.env`.
