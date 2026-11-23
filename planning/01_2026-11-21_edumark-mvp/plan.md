# Plan: EduMark AI — MVP Chấm Bài Tự Luận Toán THCS

| Metadata        | Giá trị                                           |
| :-------------- | :------------------------------------------------ |
| **Plan ID**     | `01_2026-11-21_edumark-mvp`                       |
| **SemVer**      | `v0.1.0` → `v1.0.0`                              |
| **Phân loại**   | `MINOR` → `MAJOR`                                 |
| **Tạo lúc**     | 2026-11-21T01:40:00+07:00                         |
| **Tác giả**     | AI Agent (Claude Opus 4.6) + User                 |
| **Trạng thái**  | ✅ ĐÃ HOÀN THÀNH TẦNG 1 (MVP READY)                |

---

## 📑 Mục Lục SPEC

| File                         | Mô tả                                                 | Tier |
| :--------------------------- | :----------------------------------------------------- | :--: |
| `specs/00-ARCHITECTURE.md`   | Mục tiêu, Non-goals, Bất biến kiến trúc, Router       | 🔴   |
| `specs/01-CONTRACTS.md`      | API Schemas, Rubric JSON, Pydantic Models, Error Codes | 🔴   |
| `specs/SPEC-P01-ocr-pipe.md` | Agent 1 — Vision OCR Pipeline                          | 🟠   |
| `specs/SPEC-P02-grader.md`   | Agent 2 — Step-by-Step Rubric Grading Engine           | 🟠   |
| `specs/SPEC-P03-dashboard.md`| Split-Screen Web Dashboard + Teacher HITL              | 🟠   |
| `specs/SPEC-P04-batch.md`    | Batch Upload + PDF Split + Excel Export                | 🟠   |
| `specs/SPEC-P05-english.md`  | Module Tiếng Anh (Trắc nghiệm + Writing CEFR)         | 🟠   |
| `specs/OPERATIONS.md`        | Deploy, Runbook, Rollback                              | 🟢   |
| `specs/TESTING-ACCEPTANCE.md`| Ma trận test + Bằng chứng nghiệm thu + Exit Gates     | 🔴   |

---

## 📊 Nhật Ký Quyết Định (Decision Log)

### Quyết định đang hiệu lực

| # | Thời điểm            | Quyết định                                                                 | Lý do                                                              |
|---|----------------------|---------------------------------------------------------------------------|---------------------------------------------------------------------|
| 1 | 2026-11-18T15:00     | Chọn Gemini 1.5/2.0 Flash Multimodal làm OCR Engine                      | Chi phí thấp nhất (~80-120đ/bài), hỗ trợ Vision tốt                |
| 2 | 2026-11-18T15:30     | Swarm 2-Agent thay vì single-model                                       | Tách biệt OCR vs Reasoning → dễ debug, thay thế model độc lập      |
| 3 | 2026-11-19T17:00     | Toán THCS Đại số (lớp 8-9) là target đầu tiên                            | Bài có cấu trúc rõ (ĐKXĐ → Biến đổi → Kết luận), dễ rubric hóa    |
| 4 | 2026-11-20T14:00     | FastAPI + Pydantic V2 + TailwindCSS Split-Screen                          | Stack Python thuần, không cần frontend framework riêng              |
| 5 | 2026-11-20T15:00     | MD5 Image Hash cho cache OCR (TTL 24h)                                    | Tránh gọi API trùng, giảm 50%+ chi phí khi teacher chấm lại       |
| 6 | 2026-11-20T16:00     | Error Propagation: giữ điểm phương pháp dù sai tính toán ở bước trước    | Đúng logic sư phạm, teacher Toán chấm như vậy trên giấy            |
| 7 | 2026-11-21T01:40     | Chia kế hoạch thành 3 tầng: Ngắn hạn / Trung hạn / Dài hạn              | Phân bổ nguồn lực rõ ràng, từng milestone có thể demo/ship         |

### Quyết định bị thay thế

| # | Thời điểm         | Quyết định cũ                        | Thay bằng                       | Lý do                                       |
|---|--------------------|--------------------------------------|----------------------------------|----------------------------------------------|
| — | (chưa có)          | —                                    | —                                | —                                            |

---

## 🎯 Phân Tầng Mục Tiêu & Work Packages

### 🏁 TẦNG 1 — Ngắn Hạn (1-2 tháng): MVP Core Toán THCS
> **Mục tiêu:** Ship được phiên bản MVP có thể demo cho teacher, chấm 1 bài Toán tự luận end-to-end.

#### Phase 1.1: Foundation & OCR Pipeline (Tuần 1-2)
**Checkpoint:** Agent 1 OCR chấp nhận ảnh viết tay → trả cấu trúc LaTeX chính xác ≥ 80% dòng.

| WP    | Task                                              | Tier | Spec                    | Status |
|-------|---------------------------------------------------|:----:|-------------------------|:------:|
| WP-01 | Hoàn thiện preprocessor: auto-rotate, deskew, denoise | 🟠 | `SPEC-P01-ocr-pipe.md` | [x]    |
| WP-02 | OCR Agent 1 — prompt engineering cho Gemini Vision    | 🔴 | `SPEC-P01-ocr-pipe.md` | [x]    |
| WP-03 | OCR output parser: LaTeX → structured ExamAnswer      | 🟠 | `SPEC-P01-ocr-pipe.md` | [x]    |
| WP-04 | Unit tests OCR với 20 ảnh mẫu viết tay thực tế       | 🟠 | `TESTING-ACCEPTANCE.md` | [x]    |

#### Phase 1.2: Grading Engine & Rubric System (Tuần 3-4)
**Checkpoint:** Agent 2 chấm đúng ≥ 85% bài có rubric chuẩn, detect Error Propagation.

| WP    | Task                                                     | Tier | Spec                  | Status |
|-------|----------------------------------------------------------|:----:|------------------------|:------:|
| WP-05 | Rubric JSON schema + template cho Toán THCS               | 🔴 | `01-CONTRACTS.md`      | [x]    |
| WP-06 | Agent 2 Grader — step matching engine (ĐKXĐ/biến đổi/KL) | 🔴 | `SPEC-P02-grader.md`   | [x]    |
| WP-07 | Error Propagation detection & partial credit logic         | 🔴 | `SPEC-P02-grader.md`   | [x]    |
| WP-08 | Confidence scoring (green/yellow/red thresholds)           | 🟠 | `SPEC-P02-grader.md`   | [x]    |
| WP-09 | Unit tests Grader với 30 bài mẫu (đúng/sai/nửa)          | 🟠 | `TESTING-ACCEPTANCE.md` | [x]    |

#### Phase 1.3: Web Dashboard MVP (Tuần 5-6)
**Checkpoint:** Teacher upload ảnh → xem kết quả Split-Screen → approve/override → export điểm.

| WP    | Task                                                    | Tier | Spec                    | Status |
|-------|---------------------------------------------------------|:----:|--------------------------|:------:|
| WP-10 | Split-Screen UI hoàn chỉnh (ảnh trái, kết quả phải)     | 🟠 | `SPEC-P03-dashboard.md`  | [x]    |
| WP-11 | Keyboard shortcuts (Space/A/D/+/-) cho teacher review    | 🟢 | `SPEC-P03-dashboard.md`  | [x]    |
| WP-12 | API endpoint `/submissions/grade` — real Gemini call      | 🟠 | `01-CONTRACTS.md`        | [x]    |
| WP-13 | Teacher override flow (sửa điểm + ghi nhận feedback)     | 🟠 | `SPEC-P03-dashboard.md`  | [x]    |

#### Phase 1.4: Batch & Export (Tuần 7-8)
**Checkpoint:** Upload ZIP/PDF nhiều bài → chấm hàng loạt → export Excel.

| WP    | Task                                                  | Tier | Spec                 | Status |
|-------|-------------------------------------------------------|:----:|----------------------|:------:|
| WP-14 | Batch upload: ZIP nhiều ảnh hoặc PDF multi-page        | 🟠 | `SPEC-P04-batch.md`  | [x]    |
| WP-15 | PDF page splitter (mỗi trang = 1 bài)                 | 🟠 | `SPEC-P04-batch.md`  | [x]    |
| WP-16 | Excel export (format tương thích VnEdu/SMAS)           | 🟠 | `SPEC-P04-batch.md`  | [x]    |
| WP-17 | Progress bar + background job queue cho batch           | 🟠 | `SPEC-P04-batch.md`  | [x]    |
| WP-18 | Integration test E2E: upload 10 bài → batch grade → export | 🟠 | `TESTING-ACCEPTANCE.md` | [x] |

---

### 🚀 TẦNG 2 — Trung Hạn (3-5 tháng): Mở Rộng Chức Năng

#### Phase 2.1: Module Tiếng Anh (Tháng 3-4)
**Checkpoint:** Chấm trắc nghiệm Anh ≥ 95% chính xác, Writing ≥ 80% tương đồng với teacher.

| WP    | Task                                                     | Tier | Spec                   | Status |
|-------|----------------------------------------------------------|:----:|------------------------|:------:|
| WP-19 | Trắc nghiệm scanner (OMR bubble detection)               | 🟠 | `SPEC-P05-english.md`  | [ ]    |
| WP-20 | Writing grader — CEFR rubric (A1-B2)                      | 🔴 | `SPEC-P05-english.md`  | [ ]    |
| WP-21 | Grammar/spelling error highlighting                       | 🟠 | `SPEC-P05-english.md`  | [ ]    |
| WP-22 | Rubric templates cho các dạng bài Anh (letter, essay)     | 🟠 | `SPEC-P05-english.md`  | [ ]    |

#### Phase 2.2: Analytics & Insights (Tháng 4-5)
**Checkpoint:** Dashboard hiển thị heatmap lỗi + phân phối điểm + so sánh lớp.

| WP    | Task                                                     | Tier | Spec                   | Status |
|-------|----------------------------------------------------------|:----:|------------------------|:------:|
| WP-23 | Error pattern analytics (loại lỗi phổ biến / câu khó)    | 🟠 | `SPEC-P03-dashboard.md` | [ ]   |
| WP-24 | Class heatmap: điểm mạnh/yếu từng lớp                    | 🟠 | `SPEC-P03-dashboard.md` | [ ]   |
| WP-25 | Student progress tracking theo thời gian                  | 🟠 | `SPEC-P03-dashboard.md` | [ ]   |
| WP-26 | Plagiarism/similarity clustering (bài giống nhau bất thường) | 🔴 | `SPEC-P02-grader.md` | [ ]   |

#### Phase 2.3: Mobile & Offline (Tháng 5)
**Checkpoint:** Teacher chụp ảnh bằng điện thoại → app gửi lên → nhận kết quả.

| WP    | Task                                             | Tier | Spec                   | Status |
|-------|--------------------------------------------------|:----:|------------------------|:------:|
| WP-27 | Mobile-responsive web (PWA shell)                 | 🟠 | `SPEC-P03-dashboard.md` | [ ]   |
| WP-28 | Camera scan trực tiếp từ browser mobile            | 🟠 | `SPEC-P03-dashboard.md` | [ ]   |
| WP-29 | Offline queue: chụp trước, chấm khi có mạng       | 🟠 | `SPEC-P04-batch.md`     | [ ]   |

---

### 🌟 TẦNG 3 — Dài Hạn (6+ tháng): Platform & B2B SaaS

#### Phase 3.1: Adaptive Learning (Tháng 6-8)
**Checkpoint:** Hệ thống gợi ý bài tập dựa trên pattern lỗi của từng học sinh.

| WP    | Task                                                    | Tier | Spec          | Status |
|-------|---------------------------------------------------------|:----:|---------------|:------:|
| WP-30 | Student error profile database                           | 🔴 | (future spec) | [ ]    |
| WP-31 | Recommendation engine: bài tập phù hợp theo lỗ hổng     | 🔴 | (future spec) | [ ]    |
| WP-32 | Teacher AI assistant (gợi ý giải thích cho lỗi phổ biến)| 🟠 | (future spec) | [ ]    |

#### Phase 3.2: Enterprise Integration (Tháng 8-10)
**Checkpoint:** API tích hợp được với VnEdu, SMAS, hệ thống quản lý trường học.

| WP    | Task                                                  | Tier | Spec          | Status |
|-------|-------------------------------------------------------|:----:|---------------|:------:|
| WP-33 | VnEdu API integration (import lớp, export điểm)       | 🟠 | (future spec) | [ ]    |
| WP-34 | SMAS connector (đồng bộ sổ điểm)                      | 🟠 | (future spec) | [ ]    |
| WP-35 | OAuth2 / SSO cho giáo viên (Google Workspace schools)  | 🔴 | (future spec) | [ ]    |

#### Phase 3.3: B2B SaaS (Tháng 10-12)
**Checkpoint:** Multi-tenant, billing, onboarding flow cho trường/phòng GD.

| WP    | Task                                                  | Tier | Spec          | Status |
|-------|-------------------------------------------------------|:----:|---------------|:------:|
| WP-36 | Multi-tenant architecture (school isolation)            | 🔴 | (future spec) | [ ]    |
| WP-37 | Subscription billing (free tier / school tier / district)| 🔴 | (future spec) | [ ]   |
| WP-38 | Admin dashboard cho trường (quản lý GV, lớp, quota)     | 🟠 | (future spec) | [ ]    |
| WP-39 | Onboarding wizard + template library cho barem chuẩn     | 🟠 | (future spec) | [ ]   |
| WP-40 | Multi-subject expansion (Lý, Hóa, Văn)                  | 🔴 | (future spec) | [ ]   |

---

## ✅ Execution Checklist (MVP — Tầng 1)

### Phase 1.1: Foundation & OCR
- [x] WP-01: Preprocessor auto-rotate, deskew, denoise
- [x] WP-02: OCR Agent 1 prompt engineering
- [x] WP-03: LaTeX parser → ExamAnswer
- [x] WP-04: 20 ảnh mẫu unit test

### Phase 1.2: Grading Engine
- [x] WP-05: Rubric JSON schema
- [x] WP-06: Step matching engine
- [x] WP-07: Error Propagation logic
- [x] WP-08: Confidence scoring
- [x] WP-09: 30 bài mẫu test

### Phase 1.3: Web Dashboard
- [x] WP-10: Split-Screen UI hoàn chỉnh
- [x] WP-11: Keyboard shortcuts
- [x] WP-12: Real Gemini API endpoint
- [x] WP-13: Teacher override flow

### Phase 1.4: Batch & Export
- [x] WP-14: Batch upload ZIP/PDF
- [x] WP-15: PDF page splitter
- [x] WP-16: Excel export VnEdu/SMAS
- [x] WP-17: Progress bar + job queue
- [x] WP-18: E2E integration test 10 bài

### Acceptance Gate (Tầng 1)
- [x] `python -m pytest tests/ -v` → all green (42 passed)
- [x] `uvicorn backend.app.main:app` → server chạy, 0 error
- [x] Upload 1 ảnh viết tay thật → OCR → grade → review → export → ✅
- [x] Upload 10 bài batch → grade all → export Excel → ✅
- [x] Chấm đúng ≥ 85% so với teacher chấm tay (20 bài benchmark)

---

## ⏸ Ghi Chú

- Tầng 2 và Tầng 3 sẽ được tách thành kế hoạch riêng (`planning/02_*`, `planning/03_*`) khi Tầng 1 hoàn thành.
- Mọi SPEC file chỉ cover chi tiết cho Tầng 1. Tầng 2-3 được liệt kê để có cái nhìn toàn cảnh.
