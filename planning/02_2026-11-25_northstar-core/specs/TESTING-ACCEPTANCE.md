# TESTING-ACCEPTANCE — Ma Trận Test & Exit Gates (v0.2.0)

> **Tier:** 🔴

## 1. Test Cases Mới (`tests/test_v02_northstar.py`)

| Test ID | Module | Nội dung kiểm thử | Kỳ vọng |
| :--- | :--- | :--- | :--- |
| `V2-01` | `preprocessor.py` | `assess_capture_quality` phân biệt ảnh mờ, ảnh quá tối, ảnh đạt chuẩn | Trả đúng `is_acceptable`, `is_too_dark`, `warnings` |
| `V2-02` | `preprocessor.py` | `detect_and_warp_document` + `remove_shadows` trên ảnh chụp tờ giấy nghiêng có bóng | Trả ảnh hợp lệ không crash, ghi nhận transform |
| `V2-03` | `agent1_ocr.py` | Không còn regex phá biểu thức hợp lệ `\frac{x+4}{4}` và parse đúng `is_strikethrough`, `student_name_detected` | Giữ nguyên `\frac{x+4}{4}`, gắn đúng cờ gạch xóa |
| `V2-04` | `agent1_ocr.py` | Khi `is_live=True` mà API ném lỗi → KHÔNG silent fallback sang mock | Raise `OCRApiError` (route trả `503 E008`) |
| `V2-05` | `math_verifier.py` | SymPy kiểm tra bước biến đổi đúng (`2x - 2 = 4` → `2x = 6` → `x = 3`) và bắt bước tính sai (`2x = 6` → `x = 4`) | `invalid_transitions == 0` cho bài đúng; `>= 1` cho bài sai |
| `V2-06` | `agent2_grader.py` | Bỏ qua dòng `is_strikethrough=True` / `LineType.SCRATCH` khi chấm | Không chấm nhầm phần học sinh đã gạch bỏ |
| `V2-07` | `core/database.py` | Lưu & đọc lại `ExamRubric`, `SubmissionGradingRecord`, `BatchJob` qua SQLite sau khi khởi tạo lại repository | Dữ liệu còn nguyên sau reload |
| `V2-08` | `routes.py` | CRUD exams (`PUT`/`DELETE`), `/health`, `/submissions` list, `/submissions/quality-check`, `/submissions/grade-multipage` (2 ảnh) | Tất cả `200 OK`, `page_count == 2`, export không trộn exam khác |
| `V2-09` | `routes.py` | `POST /submissions/batch` với `async_mode=true` | Chạy qua BackgroundTasks, tra cứu `/batch/{job_id}` trả `completed` |
| `V2-10` | `main.py` | PWA routes: `/app`, `/static/manifest.json`, `/static/sw.js` | Trả `200 OK` đúng content-type |

## 2. Exit Gates (`v0.2.0`)

| Gate | Điều kiện | Local | Staging |
| :--- | :--- | :---: | :---: |
| `G2-01` | Toàn bộ 42 test `v0.1.0` vẫn PASS 100% (0 regression) | ✅ | ⬜ |
| `G2-02` | Toàn bộ 10 nhóm test mới `V2-01` → `V2-10` PASS 100% | ✅ | ⬜ |
| `G2-03` | `run_test_flow.py` chạy exit 0 | ✅ | ⬜ |
| `G2-04` | `init_brain.js --check` báo `NÃO ĐÃ OK` | ✅ | ⬜ |
