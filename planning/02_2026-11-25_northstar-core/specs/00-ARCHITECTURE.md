# 00-ARCHITECTURE — Kiến Trúc EduMark AI v0.2.0 (North Star Core)

> **Tier:** 🔴 | **Kế thừa:** `planning/01_2026-11-21_edumark-mvp/specs/00-ARCHITECTURE.md`

---

## 1. Bất Biến Kiến Trúc Bổ Sung (v0.2.0)

1. **Trung Thực Tuyệt Đối Ở Agent 1 (Zero Hallucination / Zero Auto-Fix OCR):**
   - **BẮT BUỘC:** Agent 1 chỉ ghi lại đúng những gì học sinh viết trên giấy (kể cả khi học sinh tính sai), ghi nhận thêm `student_name_detected`, `page_number`, và `is_strikethrough` cho phần học sinh gạch bỏ.
   - **CẤM:** Cấm viết regex tự sửa biểu thức toán học đặc thù theo một đề thi cụ thể; cấm âm thầm trả về `_mock_extraction()` khi người dùng đã cấu hình Live API Key mà API gặp lỗi.
2. **Kiểm Chứng Toán Học Tất Định (Symbolic Verification Gate):**
   - **BẮT BUỘC:** Trước khi Agent 2 kết luận điểm số, module `services/math_verifier.py` (SymPy) kiểm tra các phương trình/đẳng thức và nghiệm từ dòng OCR (bỏ qua dòng `SCRATCH` / `is_strikethrough=True`) để cung cấp bằng chứng toán học cho bộ chấm.
3. **Persistence Bắt Buộc (No In-Memory-Only State):**
   - **BẮT BUỘC:** Mọi `ExamRubric`, `SubmissionGradingRecord` và `BatchJob` phải được lưu vào SQLite (`core/database.py`, WAL mode) ngay khi tạo/cập nhật.
4. **Mobile-First Multi-Page Capture:**
   - **BẮT BUỘC:** Một bài nộp (`Submission`) có thể gồm `1..10` ảnh chụp các trang giấy thi của cùng 1 học sinh; Preprocessor xử lý từng trang (EXIF, perspective warp, khử bóng đổ, deskew, contrast) và gửi trọn bộ cho Agent 1 đọc liền mạch.

---

## 2. Luồng Hệ Thống v0.2.0

```text
[Mobile PWA App (/app) hoặc Desktop Split-Screen (/)]
       │ 1. Chụp 1..N trang giấy thi
       ├─► POST /api/v1/submissions/quality-check (kiểm tra mờ / thiếu sáng tức thì)
       ▼ 2. Gửi bài chấm
[FastAPI Backend + SQLite WAL]
       ▼
[Preprocessor] ──► EXIF → 4-pt Perspective Warp → Shadow Removal → Deskew → Contrast
       ▼
[MD5 Cache] (key theo hash gộp toàn bộ trang + chế độ live/mock)
       ▼
[Agent 1: Full-Page Vision OCR] (OpenAI / Gemini — multi-page, Header HS, strikethrough)
       ▼
[SymPy Math Verifier] ──► Kiểm tra phương trình, phép biến đổi, nghiệm (bỏ qua scratch)
       ▼
[Agent 2: Precision Grader] (Rule-based + OpenAI/Gemini Auto-Rubric + Error Propagation)
       ▼
[SQLite Persistence + HITL Review + Excel Export (Standard / VnEdu / SMAS)]
```

---

## 3. Vùng Cấm (Non-Goals trong v0.2.0)
- Không dựng Native App store build (Flutter/React Native) trong đợt này — PWA chạy trực tiếp trên trình duyệt điện thoại đủ để kiểm chứng UX chụp thật và dùng chung 100% API.
- Không thêm OMR/Tiếng Anh (giữ đúng phạm vi Tầng 2 sau khi lõi Toán + chụp ảnh thật ổn định).
