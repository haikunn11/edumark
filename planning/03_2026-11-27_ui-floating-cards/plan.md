# Plan: EduMark AI v0.7.0 — Giao diện "Floating Cards" (Desktop + Mobile PWA)

| Metadata | Giá trị |
| :--- | :--- |
| **Plan ID** | `03_2026-11-27_ui-floating-cards` |
| **SemVer** | `v0.6.0` → `v0.7.0` |
| **Phân loại** | `MINOR` (đổi giao diện, giữ tương thích ngược toàn bộ API & DOM id) |
| **Tạo lúc** | `2026-11-27T02:40:00+07:00` |
| **Tác giả** | AI Agent + Chủ dự án |
| **Trạng thái** | ✅ ĐÃ HOÀN THÀNH LOCAL (`2026-11-27T05:10:00+07:00`) — chờ G7-04 (chủ dự án xem ảnh) & gate server |

---

## 🎯 Mục tiêu
Áp design system "Floating Cards" (phân tích từ video explainer Keka, mockup đã được chủ dự án duyệt ngày 2026-11-27) vào `/` (Desktop) và `/app` (Mobile PWA): nền sáng xám-xanh, card trắng nổi, mỗi màu một nghĩa, thanh cam chỉ cho chỗ thầy cô cần quyết. Không đổi backend, không đổi API.

Mockup tham chiếu (ngoài repo, chỉ để đối chiếu thị giác): `d:\dự án ma\runtime\edumark-redesign\mockup\*.png`.

---

## 📑 Mục lục SPEC

| File | Mô tả | Tier |
| :--- | :--- | :---: |
| `specs/00-ARCHITECTURE.md` | Mục tiêu, non-goals, bất biến, vùng cấm, thứ tự đọc | 🔴 |
| `specs/01-CONTRACTS.md` | File mới, biến CSS, API JS (`toggleDarkMode`, `computeAttention`…), danh sách DOM id bất biến | 🔴 |
| `specs/SPEC-P01-theme-layer.md` | Lớp theme: biến màu Tailwind, sáng/tối, font, token, component CSS | 🟠 |
| `specs/SPEC-P02-desktop-layout.md` | Dựng lại header, trang trống (home), thẻ điểm, thanh chú ý, tab, thanh hành động | 🟠 |
| `specs/SPEC-P03-mobile.md` | Áp theme + chỉnh header, khu chụp, thanh điều hướng của `/app` | 🟠 |
| `specs/OPERATIONS.md` | Cache PWA, thứ tự triển khai, rollback | 🟢 |
| `specs/TESTING-ACCEPTANCE.md` | Ma trận test, bằng chứng ảnh, Exit Gates | 🔴 |

---

## 📊 Nhật ký quyết định

### Quyết định đang hiệu lực
| # | Thời điểm | Quyết định | Lý do |
|---|-----------|-----------|-------|
| 1 | 2026-11-27T02:05 | Chủ dự án duyệt mockup Floating Cards ("ok đó em. áp vào cho a đi") | Ảnh mẫu 00–04 đã xem trước khi code |
| 2 | 2026-11-27T02:40 | Đổi màu bằng **lớp biến CSS cho bảng màu Tailwind** thay vì viết lại toàn bộ class | ~400 class màu nằm trong HTML lẫn chuỗi JS; viết lại từng chỗ rủi ro gãy JS cao, lớp biến phủ cả modal & HTML do JS sinh |
| 3 | 2026-11-27T02:40 | Sáng = mặc định (giao diện mới); Tối = bảng màu Tailwind gốc v0.6 qua cùng lớp biến | Nút Sáng/Tối trước đây không có tác dụng; giữ lựa chọn tối cho người quen giao diện cũ |
| 4 | 2026-11-27T02:40 | Font Be Vietnam Pro (chữ) + JetBrains Mono (số) | Poppins (gần video) thiếu dấu tiếng Việt |
| 5 | 2026-11-27T02:40 | Thanh chú ý (cam/magenta) tính từ dữ liệu thật: tổng vượt barem, điểm câu ≠ tổng tiêu chí, `needs_review` | Dữ liệu thật có lỗi 12.00/3.0đ và câu 1d 0.8 ≠ 1.0; UI phải làm lộ ra, không che |
| 6 | 2026-11-27T02:40 | Khung vùng câu trên ảnh (mockup 02) **hoãn** | OCR không trả toạ độ (bbox); vẽ giả là vi phạm trung thực |
| 7 | 2026-11-27T04:20 | Ramp Sáng sắc 50/100 = chữ đậm (không phải nền nhạt) | Đo: 3/3 lần dùng 50/100 trong UI là `text-*`; nút "Soi lại/Sửa chữ" mất chữ khi để nền nhạt |
| 8 | 2026-11-27T04:50 | `focusAttention` cuộn bằng `#right-scroll.scrollTo()` + `#right-scroll` có `relative` | `scrollIntoView` cuộn cả `<main>` (đo `main.scrollTop=23`, `scrollHeight 2086>832`); sau sửa `832/832`, `scrollTop=0` |

### Quyết định bị thay thế
| # | Thời điểm | Quyết định cũ | Thay bằng | Lý do |
|---|-----------|--------------|-----------|-------|
| 1 | v0.2.0–v0.6.0 | Giao diện tối cố định `class="dark"`, 12 nút màu trên header | Sáng mặc định + 1 nút chính "Chấm bài mới" + icon | Thiết kế mới đã duyệt |

---

## 👷 Work Packages
| WP | Nội dung | Tier | Phụ thuộc |
|---|---|:---:|---|
| WP1 | `scripts/gen_theme_palette.py` + `static/theme.css` + `static/theme.js` | 🟠 | — |
| WP2 | Desktop `index.html`: nạp theme, thay `text-white` ngữ cảnh trung tính, dựng lại vùng chính | 🟠 | WP1 |
| WP3 | Mobile `mobile.html` | 🟠 | WP1 |
| WP4 | Test `tests/test_v07_ui_theme.py` + chụp ảnh nghiệm thu | 🟠 | WP2, WP3 |
| WP5 | Đồng bộ phiên bản & não bộ (Ma trận 6 điểm) | 🟢 | WP4 |

## ✅ Checklist thực thi
- [x] WP1 theme layer
- [x] WP2 desktop
- [x] WP3 mobile
- [x] WP4 test + ảnh nghiệm thu
- [x] WP5 version `0.7.0` + brain sync
