# EduMark AI — Hệ Thống Chấm Bài Tự Luận Viết Tay Thông Minh (`v0.2.0`)

> **🎯 Mục tiêu cuối cùng (North Star):** Mở **App** → **Chụp bài làm viết tay của học sinh** → **Nhìn trọn vẹn 100% dữ liệu chữ viết tay** → **Chấm bài chuẩn xác**.

Ứng dụng kiến trúc **Swarm 2-Agent (Vision OCR + Reasoning Grader) + SymPy Symbolic Math Verifier + SQLite Persistence + Human-in-the-Loop**.

---

## 📁 Cấu Trúc Dự Án

```text
edumark/
├── backend/app/
│   ├── main.py                      # FastAPI Server, /health, Desktop (/) & Mobile PWA (/app)
│   ├── core/
│   │   ├── config.py                # Settings (.env, AI_PROVIDER, SQLite path, Admin token)
│   │   ├── database.py              # SQLite WAL Persistence (exams, submissions, batch_jobs)
│   │   ├── cache.py                 # MD5 Image Hash Cache (TTL 24h)
│   │   └── storage.py               # Local Storage Engine cho ảnh bài thi
│   ├── models/
│   │   └── schemas.py               # Pydantic V2 Schemas (Rubric, Multi-Page OCR, SymPy, Grading)
│   ├── services/
│   │   ├── preprocessor.py          # EXIF, 4-pt Perspective Warp, Shadow Removal, Deskew, Quality Check
│   │   ├── agent1_ocr.py            # Agent 1: Full-Page Multi-Page OCR (OpenAI/Gemini + Strikethrough + Header)
│   │   ├── math_verifier.py         # Kiểm chứng toán học tất định bằng SymPy
│   │   ├── agent2_grader.py         # Agent 2: Rule-based + OpenAI/Gemini Auto-Rubric + Error Propagation
│   │   ├── pipeline.py              # Orchestrator đa trang (1..10 ảnh/bài)
│   │   └── batch.py                 # PDF Split (PyMuPDF), ZIP Extract, Async Batch, Excel VnEdu/SMAS
│   ├── api/
│   │   └── routes.py                # REST API /api/v1/* (CRUD Exams, Quality Check, Multi-Page, Review, Export)
│   └── static/
│       ├── index.html               # Desktop Split-Screen Dashboard (TailwindCSS + KaTeX)
│       ├── mobile.html              # Mobile PWA App Chụp Bài Trực Tiếp (/app)
│       ├── manifest.json            # PWA Manifest
│       └── sw.js                    # Service Worker
├── tests/                           # 8 bộ pytest (52 test cases)
├── planning/                        # Bộ SPEC theo chuẩn Spec-First (v0.1.0 & v0.2.0)
├── brain4agent/                     # Bộ nhớ Não Bộ Đa Tầng V5.2
├── requirements.txt                 # Dependencies chuẩn
├── .env.example                     # Mẫu cấu hình môi trường
└── run_test_flow.py                 # Kịch bản kiểm thử E2E 10 bước
```

---

## 🚀 Cài Đặt & Khởi Chạy Nhanh

### 1. Cài đặt môi trường
```powershell
pip install -r requirements.txt
copy .env.example .env
# Điền GEMINI_API_KEY hoặc OPENAI_API_KEY vào file .env
```

### 2. Chạy Server (Desktop & Mobile PWA)
```powershell
python -m uvicorn backend.app.main:app --reload --port 8000
```
Truy cập:
- **📱 Mobile App Chụp Bài (PWA):** `http://127.0.0.1:8000/app`
- **🖥️ Desktop Split-Screen Dashboard:** `http://127.0.0.1:8000/`
- **📘 Swagger API Docs:** `http://127.0.0.1:8000/docs`
- **❤️ Health Check:** `http://127.0.0.1:8000/health`

### 3. Chạy Kiểm Thử Tự Động
```powershell
python -m pytest tests/ -v
python run_test_flow.py
```
