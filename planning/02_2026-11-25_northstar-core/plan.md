# Plan: EduMark AI v0.2.0 — North Star Core & Mobile Capture PWA

| Metadata | Giá trị |
| :--- | :--- |
| **Plan ID** | `02_2026-11-25_northstar-core` |
| **SemVer** | `v0.1.0` → `v0.2.0` |
| **Phân loại** | `MINOR` |
| **Tạo lúc** | `2026-11-25T15:00:00+07:00` |
| **Tác giả** | AI Agent + Chủ dự án |
| **Trạng thái** | ✅ ĐÃ HOÀN THÀNH LOCAL (`2026-11-25T15:11:00+07:00`) |

---

## 🎯 Mục Tiêu (North Star Alignment)
Hiện thực hóa trực tiếp mục tiêu cuối cùng của chủ dự án: **Dùng App chụp bài làm viết tay của học sinh → Nhìn trọn vẹn toàn bộ dữ liệu chữ viết tay → Chấm bài chuẩn xác**, đồng thời xử lý dứt điểm các nợ kỹ thuật nghiêm trọng từ `v0.1.0`.

---

## 📑 Mục Lục SPEC

| File | Mô tả | Tier |
| :--- | :--- | :---: |
| `specs/00-ARCHITECTURE.md` | Bất biến mới cho Mobile Capture, Full-Page OCR, SymPy Math Verifier & SQLite | 🔴 |
| `specs/01-CONTRACTS.md` | Contracts mới: multi-page grade, quality-check, CRUD exams, SQLite tables, error codes | 🔴 |
| `specs/OPERATIONS.md` | Packaging (`requirements.txt`, `.env.example`), SQLite WAL, bảo mật `/config/key` | 🟢 |
| `specs/TESTING-ACCEPTANCE.md` | Ma trận kiểm thử mới (SymPy, perspective/shadow, SQLite, multi-page, PWA) & Exit Gates | 🔴 |

---

## 📊 Nhật Ký Quyết Định (Decision Log)

### Quyết định đang hiệu lực
| # | Thời điểm | Quyết định | Lý do |
|---|-----------|-----------|-------|
| 1 | 2026-11-25T14:43 | Chốt North Star: App chụp trực tiếp bài viết tay → nhìn trọn vẹn → chấm chuẩn | Định hướng trực tiếp từ Chủ dự án |
| 2 | 2026-11-25T15:00 | Dựng **Mobile-First PWA (`/app`)** trước khi build Native Flutter/RN | Dùng ngay trên iOS/Android qua trình duyệt, 0 thời gian duyệt store, dùng chung 1 Backend API |
| 3 | 2026-11-25T15:00 | Tích hợp **SymPy Symbolic Math Verifier** bổ trợ Agent 2 | Kiểm chứng đại số/nghiệm tất định, giảm ảo giác tính nhầm của LLM |
| 4 | 2026-11-25T15:00 | Dùng **SQLite (`sqlite3` stdlib, WAL mode)** cho persistence local/staging | Zero-dependency, khắc phục ngay lỗi mất dữ liệu khi restart, dễ chuyển PostgreSQL |
| 5 | 2026-11-25T15:00 | Gỡ toàn bộ regex/prompt overfit bài Toán 7 và bỏ silent fallback mock khi live API lỗi | Đảm bảo trung thực 100% với chữ viết tay thật của học sinh |

### Quyết định bị thay thế
| # | Thời điểm | Quyết định cũ | Thay bằng | Lý do |
|---|-----------|--------------|-----------|-------|
| 1 | 2026-11-25T14:43 | Đẩy Mobile Camera sang Phase 2.3 (Tháng 5) | Đẩy lên ưu tiên ngay tại `v0.2.0` | Khớp mục tiêu cuối cùng là chụp bài trực tiếp bằng App |
| 2 | 2026-11-25T15:00 | Lưu `EXAM_DB`/`SUBMISSION_DB` trong RAM dict | Lưu bền vững bằng SQLite (`core/database.py`) | Khắc phục lỗi mất dữ liệu & vi phạm Stateless Backend |

---

## ✅ Execution Checklist (`v0.2.0`)

- [x] **WP-201** 🔴 `preprocessor.py`: Document 4-point perspective warp, shadow removal, pre-capture quality check (`assess_capture_quality`).
- [x] **WP-202** 🔴 `agent1_ocr.py` + `schemas.py`: Tổng quát hóa prompt đọc toàn trang + Header HS + gạch xóa (`is_strikethrough`), gỡ regex overfit, hỗ trợ multi-page, bỏ silent mock fallback khi live API lỗi.
- [x] **WP-203** 🔴 `math_verifier.py` + `agent2_grader.py`: Tích hợp SymPy kiểm chứng phương trình/nghiệm/bước biến đổi, bỏ qua dòng gạch xóa, hỗ trợ đủ cả Gemini và OpenAI cho `grade_auto_rubric()`.
- [x] **WP-204** 🔴 `core/database.py` + `routes.py` + `batch.py`: SQLite persistence (exams, submissions, batch_jobs), hoàn thiện CRUD exams (`PUT`/`DELETE`), `/health`, `/submissions` list, `/submissions/quality-check`, `/submissions/grade-multipage`, async batch mode, fix export fallback, bảo mật `/config/key`.
- [x] **WP-205** 🟠 `static/mobile.html` + `manifest.json` + `sw.js` + `index.html`: App Mobile PWA chụp bài trực tiếp (camera viewfinder, kiểm tra nét/sáng, ghép nhiều trang, xem toàn bộ chữ đọc được + kết quả chấm KaTeX, lịch sử chấm).
- [x] **WP-206** 🟢 Packaging & Acceptance: `requirements.txt`, `.env.example`, `.gitignore`, bộ test mới (`tests/test_v02_northstar.py`) + toàn bộ `pytest` xanh (52/52) + `run_test_flow.py` (10/10) + đồng bộ `brain4agent/`.
