# SPEC-P02 — Desktop `/` (index.html)

> **Tier:** 🟠 · Mockup: `01-desktop-home.png`, `02-desktop-grading.png`

## 1. Header (cao 68px, nền surface)
- Trái: logo chữ thường `edumark` + 4 tia màu + chip `v0.7`; khối đề (`#exam-title-header`) + `#badge-confidence`.
- Giữa: pill học sinh (avatar viền màu + `#student-name-display` + nút đổi tên) + `#status-display` + `#cache-badge`.
- Phải: `#btn-api-key` (chấm + nhãn), icon: Lịch sử, Bảng điểm, QR, App chụp bài (`/app`), Sáng/Tối (`#dark-icon`); **1 nút chính** `#btn-new-grade` mở `#new-grade-menu` gồm: Tải bài thi (`openUploadModal`), Chấm cả folder (`openFolderModal`), Tạo đề & barem (`openRubricModal`).
- `#btn-override`, `#btn-approve` chuyển xuống thanh hành động (§5). CẤM xoá.

## 2. Panel trái
- Card trắng bo 18px, cách mép 20px. Toolbar giữ `#active-page-pill`, `#blur-warning`, `#loading-pill`, cụm zoom.
- `#image-placeholder` (trạng thái chưa có ảnh) = **home**: tiêu đề hai tầng ("chấm bài viết tay, **nhanh** mà vẫn **chuẩn**."), lưới 6 tile (Chụp bằng điện thoại→`openMobileQrModal`, Tải ảnh/PDF→`openUploadModal`, Chấm cả folder→`openFolderModal`, Tạo đề & barem→`openRubricModal`, Bảng điểm→`openGradebookModal`, Mở bài vừa chấm→`openHistoryModal`) + dải "Kéo thả ảnh vào đây". Tile có `pointer-events-auto`, placeholder vẫn `pointer-events-none` để kéo thả hoạt động.
- Viewport ảnh: nền `--em-soft`, ảnh có bóng lift. Footer phím tắt giữ.

## 3. Panel phải
- `#home-recent` (chỉ hiện khi chưa có `currentSubmission`): avatar lớp, `#home-unnamed-bar` cam (đếm bài có tên chứa "chưa rõ tên", bấm → `openHistoryModal`), danh sách ≤6 bài: avatar viền màu theo trạng thái, điểm mono, chip trạng thái; điểm > `total_points` ⇒ chữ magenta + chip "Điểm vượt barem". Bấm dòng ⇒ `renderGradingRecord(s)` như lịch sử.
- Thẻ điểm: eyebrow "Tổng điểm", tiêu đề `#question-id-title`, số `#display-total-score` cỡ 40 mono, vòng tiến độ, chip SymPy (`#sympy-banner` giữ logic), `#chip-time`, `#chip-cost`.
- `#attention-bar` (thanh cam nổi, nghiêng −1.2°; level `error` dùng nền magenta chữ trắng) theo 01-CONTRACTS §3; nút "Xem câu" ⇒ `switchRightTab('score')` + cuộn tới câu.
- `#telemetry-panel` và `#reassembly-banner` **chuyển vào tab "Ma trận & đo"** (giữ id & logic).
- Tab: segmented control nền soft; nút đang chọn nền surface chữ blue.
- `renderGradingRecord` BẮT BUỘC: ẩn `#home-recent`, gọi `renderAttentionBar(data)`, cập nhật `#chip-time`/`#chip-cost`.

## 4. Chuỗi class do JS sinh
- `switchRightTab`, `quickAdjustScore`, `renderGradingRecord` (điểm, trạng thái) đổi sang class mới; các chuỗi khác chỉ qua lớp biến (P01) + quy tắc `text-white` (P01 §3).

## 5. Thanh hành động (đáy panel phải)
`Sửa nhanh` −0.25/+0.25/+0.50 (giữ `quickAdjustScore`) · `#btn-override` (outline, phím O) · `#btn-approve` (green, phím A).

## 6. CẤM
- Đổi thứ tự/tên tham số của hàm hiện có; xoá id; thêm phụ thuộc JS mới ngoài CDN đã dùng.
