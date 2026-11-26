# Plan: EduMark AI v0.7.1 — Hotfix điểm câu vượt barem & lệch tổng tiêu chí

| Metadata | Giá trị |
| :--- | :--- |
| **Plan ID** | `04_2026-11-27_grader-score-clamp` |
| **SemVer** | `v0.7.0` → `v0.7.1` |
| **Phân loại** | `PATCH` (hotfix ≤1 ngày công — chỉ `plan.md` theo AGENTS.md §3.2.5) |
| **Tạo lúc** | `2026-11-27T05:30:00+07:00` |
| **Tác giả** | AI Agent + Chủ dự án |
| **Trạng thái** | ✅ ĐÃ HOÀN THÀNH LOCAL |

---

## 🐞 Triệu chứng (dữ liệu thật SQLite, phát hiện qua thanh chú ý v0.7.0)
| Bài | Hiện tượng |
|---|---|
| `class_a50039a2_hs22` | Tổng 12.00 / 3.0đ — Câu 1c `10.0/1.0`, tiêu chí `5/5 + 5/5` |
| `class_a50039a2_hs27` | Tổng 21.75 / 4.0đ — Câu 1a, 1c `10.0/1.0` |
| `class_a50039a2_hs26` | Tổng 14.50 / 10đ — Câu 2b `7.5/3.0` |
| `class_a50039a2_hs24` | Câu 1d tiêu chí `0.8/1.0` nhưng câu ghi `1.0` ⇒ tổng 4.00 thay vì 3.80 |

## 🔍 Nguyên nhân gốc (đo bằng payload SQLite)
1. `_parse_auto_rubric_response` / `_parse_rubric_response` (`agent2_grader.py`) lấy `suggested_score` **tự khai của LLM** làm điểm câu; tổng tiêu chí chỉ là giá trị dự phòng ⇒ câu 1d = 1.0 dù tiêu chí 0.8.
2. 2-pass OCR (`student_clusterer.py`) chấm lại 1 câu bằng `grade_auto_rubric` (thang mặc định 10đ) rồi **chỉ gán đè** `max_score/total_points` về thang gốc (1.0đ) mà không quy đổi điểm ⇒ 10/1đ. `routes.py` (chấm lại 1 câu) có quy đổi điểm câu nhưng bỏ quên tiêu chí.

## 📊 Nhật ký quyết định
| # | Thời điểm | Quyết định | Lý do |
|---|---|---|---|
| 1 | 2026-11-27T05:30 | Thêm `fit_result_to_max(res, new_max)` ở `agent2_grader.py`: quy đổi tỉ lệ tiêu chí khi tổng tối đa tiêu chí > thang câu; **điểm câu = Σ tiêu chí**; kẹp `[0, max]` | Một hàm chung cho 4 đường chấm; tiêu chí là phần thầy cô kiểm được, LLM tự khai tổng thì không |
| 2 | 2026-11-27T05:30 | Áp dụng ở: 2 hàm parse, 2-pass OCR clusterer, chấm lại 1 câu ở routes | Đủ mọi đường sinh `QuestionGradingResult` từ LLM |
| 3 | 2026-11-27T05:30 | **CẤM** tự sửa bản ghi cũ trong SQLite | Đổi dữ liệu đã chấm cần chủ dự án quyết; thầy cô chấm lại bằng nút "Soi lại"/"Sửa chữ" hoặc chạy lại bài. Thanh chú ý v0.7.0 vẫn cảnh báo bản ghi cũ |
| 4 | 2026-11-27T05:30 | Không đụng `_grade_with_rules` | Rule engine đã kẹp `total_awarded ≤ max` và tự đồng bộ tiêu chí khi bảo lưu điểm |
| 5 | 2026-11-27T06:20 | ⏸ Chuẩn hoá điểm bài cũ: chủ dự án cho làm rồi **yêu cầu dừng** sau bước khảo sát — chưa ghi DB, chưa sửa `outputs/` | Khảo sát chỉ đọc: 40 bài, 19 câu lệch, 13 bài tổng vượt barem, 0 bài APPROVED, 0 bài thầy cô sửa tay; file đầu ra (`00_ket_qua.json`, `00_KET_QUA_CHAM.md`, `00_BANG_DIEM_CA_LOP.xlsx`, `00_TONG_HOP_PHAN_LOAI.md`) cũng mang điểm cũ. Làm tiếp: sao lưu → dry-run → `--apply` (chi tiết ở `brain4agent/roadmap.md`) |

### Vùng cấm
- Không nâng (upscale) tiêu chí khi tổng tối đa tiêu chí < thang câu — chỉ quy đổi xuống; điểm câu khi đó vẫn = Σ tiêu chí ≤ max.
- Không sửa prompt LLM trong hotfix này (giá trị mẫu `"suggested_score": 10.0` vẫn còn) — đã có lớp kẹp tất định phía sau.

## ✅ Checklist & nghiệm thu
- [x] Test viết trước `tests/test_v071_score_reconcile.py` (V71-01…07) — **đỏ trên code cũ**: `assert 1.0 == 0.8` (×2), so sánh thang 3.0 sai, thiếu `fit_result_to_max` (×4).
- [x] Sửa `agent2_grader.py`, `student_clusterer.py`, `api/routes.py` ⇒ 7/7 xanh.
- [x] Toàn bộ pytest qua `heavy.sh -n 4` (kết quả ghi ở `brain4agent/changelog.md` mục v0.7.1).
- [x] Version `0.7.1`: `config.py`, `README.md`, `state.json`, `changelog.md`.
