# 00-ARCHITECTURE — Floating Cards UI (v0.7.0)

> **Tier:** 🔴 · Thứ tự đọc: 00 → 01 → SPEC-P01 → SPEC-P02 → SPEC-P03 → OPERATIONS → TESTING-ACCEPTANCE

## 1. Mục tiêu
1. `/` và `/app` hiển thị theo design system Floating Cards ở chế độ Sáng (mặc định).
2. Nút Sáng/Tối hoạt động thật; Tối tái hiện bảng màu v0.6.
3. Thầy cô thấy ngay **chỗ cần quyết** (thanh chú ý) thay vì phải đọc hết panel.
4. Header desktop từ 12 nút màu còn **1 nút chính + nhóm icon**.

## 2. Non-goals
- Không đổi backend, endpoint, schema, SQLite.
- Không vẽ khung vùng câu trên ảnh (thiếu bbox — xem plan QĐ #6).
- Không đổi luồng nghiệp vụ, phím tắt, nội dung modal.

## 3. Bất biến (BẮT BUỘC)
- **B1. DOM id bất biến:** mọi `id` tồn tại ở v0.6 của `index.html` và `mobile.html` phải còn ở v0.7 (danh sách ở 01-CONTRACTS §4; test V7-03 kiểm).
- **B2. Hàm JS bất biến:** mọi hàm global được gọi qua `onclick` ở v0.6 giữ nguyên tên & chữ ký.
- **B3. Màu qua biến:** 10 họ màu Tailwind đang dùng (`slate, indigo, sky, cyan, teal, emerald, amber, rose, violet, purple`) được định nghĩa qua biến CSS `--tw-<họ>-<sắc>` dạng `R G B`; chế độ Sáng trên `:root`, Tối trên `html.dark`.
- **B4. Mỗi màu một nghĩa** (chế độ Sáng): blue = hành động chính; green = đúng/duyệt; orange = cần thầy cô xem; magenta = sai/lỗi/vượt barem; cyan = AI/OCR; purple = chỉ icon tính năng.
- **B5. Thanh chú ý trung thực:** chỉ hiện khi dữ liệu có vấn đề thật (01-CONTRACTS §3); không có vấn đề ⇒ ẩn.

## 4. Vùng cấm (đã cân nhắc, quyết định KHÔNG làm)
| Cấm | Lý do |
|---|---|
| Viết lại toàn bộ class màu từng phần tử | Gãy chuỗi className trong JS; lớp biến đạt cùng kết quả |
| Chuyển sang build Tailwind (npm/PostCSS) | Đổi kiến trúc frontend, ngoài phạm vi MINOR |
| Tự "sửa" điểm khi phát hiện vượt barem/lệch tổng | UI chỉ cảnh báo; sửa logic chấm là việc của Agent 2 (Idea Vault) |
| Vẽ khung câu ước lượng trên ảnh | Không có toạ độ thật ⇒ gây hiểu nhầm |
| Họa tiết (vòng cung, lưới chấm) đè lên ảnh bài làm | Che chữ viết tay — trái North Star "nhìn trọn vẹn 100%" |
