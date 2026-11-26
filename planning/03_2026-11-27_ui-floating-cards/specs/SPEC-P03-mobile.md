# SPEC-P03 — Mobile PWA `/app` (mobile.html)

> **Tier:** 🟠 · Mockup: `03-mobile.png`

## 1. Phạm vi
- Nạp `theme.css` + `theme.js` (01-CONTRACTS §1); bỏ `class="dark"` cứng; `theme-color` = `#DCE4EC` (Sáng).
- Header: logo `edumark` + tia màu + chip `v0.7`, chip trạng thái AI giữ id cũ; thêm nút Sáng/Tối (`toggleThemeMobile`).
- Segmented "1 học sinh / Cả lớp" (`#mob-mode-single`, `#mob-mode-class`): nền soft, nút chọn nền surface chữ blue — sửa chuỗi class trong `setCaptureMode`.
- Nút "Chụp bài ngay" (label `native-camera-input`): khối blue lớn bo 22px, icon tròn, vòng cung trang trí; "Chọn từ thư viện": card trắng.
- `#btn-grade-now`: nút primary blue cao 56px (giữ `disabled` style).
- Kết quả: thẻ điểm card trắng, số mono 44; tiêu chí dạng card có icon tròn trạng thái (qua lớp biến).
- Thanh điều hướng đáy: nền surface; mục đang chọn chữ blue — sửa chuỗi class trong `switchTab`.
- `text-white` theo quy tắc P01 §3.

## 2. CẤM
- Đổi luồng camera (`startLiveCamera`, `snapFromVideo`, input file), đổi API, xoá id (test V7-03).
- `.doc-frame` overlay camera giữ tối (camera luôn nền đen).
