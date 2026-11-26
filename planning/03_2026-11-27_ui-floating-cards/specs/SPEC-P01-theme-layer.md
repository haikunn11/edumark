# SPEC-P01 — Lớp Theme (biến màu, sáng/tối, font, component)

> **Tier:** 🟠 · File: `scripts/gen_theme_palette.py`, `backend/app/static/theme.css`, `backend/app/static/theme.js`

## 1. Bảng màu chế độ Sáng
| Họ Tailwind | Nghĩa mới | Gốc (500) | Nút (600) |
|---|---|---|---|
| `slate` | Trung tính (đảo ramp: 950 = nền app, 900 = bề mặt card, 100 = chữ đậm) | — | — |
| `indigo` | blue — hành động chính | `#3382BF` | `#3382BF` |
| `sky` | blue-2 — thông tin, link phụ | `#4798D0` | `#3F8DC6` |
| `cyan` | cyan — AI/OCR, bảng điểm | `#54C2D8` | `#2C9DB5` |
| `teal`, `violet`, `purple` | purple — icon tính năng (đề & barem) | `#8E6BD8` | `#7B57CB` |
| `emerald` | green — đúng/duyệt | `#6FB043` | `#5C9B33` |
| `amber` | orange — cần xem | `#F6AF2F` | `#C98210` |
| `rose` | magenta — lỗi | `#CF2967` | `#C02060` |

Ramp slate sáng: 50 `#1D2129` · 100 `#262A33` · 200 `#3A4250` · 300 `#5B6575` · 400 `#6E7A8A` · 500 `#94A0B0` · 600 `#CBD5E1` · 700 `#E3E9F0` · 800 `#F4F7FA` · 900 `#FFFFFF` · 950 `#DCE4EC`.

Ramp họ màu sáng (B = gốc): 50/100 = pha đen 55%/50% · 200/300/400 = pha đen 45%/38%/25% (chữ trên nền sáng — UI v0.6 chỉ dùng 50–400 làm màu chữ) · 500 = B · 600 = màu nút · 700 = pha đen 15% · 800/900/950 = pha trắng 72%/86%/92% (viền & nền nhạt).
Lý do đảo: UI v0.6 dùng 200–400 làm **chữ sáng trên nền tối** và 800–950 làm **nền tối**; đảo giữ đúng vai trò khi nền sáng.

## 2. Chế độ Tối
`html.dark` gán lại toàn bộ biến = giá trị Tailwind v3.4.17 gốc (`tailwindcss/lib/public/colors.js`) ⇒ tái hiện v0.6.

## 3. `text-white` theo ngữ cảnh
- BẮT BUỘC: `text-white` trên nền **trung tính** (tiêu đề modal, tên trong danh sách…) đổi thành `text-slate-100` (Sáng = chữ đậm, Tối ≈ trắng).
- GIỮ `text-white` khi cùng dòng có nền màu (`bg-<họ màu>-(500|600|700)`, `bg-gradient`, `from-`).
- CẤM remap màu `white` toàn cục (nút màu sẽ mất chữ).

## 4. Token & component (`theme.css`)
- Token: `--em-bg, --em-surface, --em-soft, --em-line, --em-ink, --em-ink-2, --em-ink-3, --em-blue, --em-green, --em-orange, --em-magenta, --em-cyan, --em-purple`, `--em-r-card: 18px`, `--em-r-ctl: 12px`, `--em-shadow-card`, `--em-shadow-lift`, `--em-shadow-orange`.
- Component: `.em-card`, `.em-btn(-primary|-success|-outline|-ghost)`, `.em-chip(-…)`, `.em-ico(.sm|.lg)` + `.em-i-<màu>`, `.em-avatar` (`--ring`), `.em-highlight`, `.em-seg`, `.em-dots`, `.em-kbd`, `.em-logo`.
- Font: `<link>` Google Fonts `Be Vietnam Pro` 400–800, `JetBrains Mono` 500/700; Tailwind `fontFamily.sans/mono` trỏ vào 2 font này.

## 5. Bảng lỗi
| Lỗi | Hành vi bắt buộc |
|---|---|
| CDN Google Fonts không tải | Rơi về `system-ui`, giao diện vẫn dùng được |
| `localStorage` ném lỗi (private mode) | Dùng Sáng, không crash |
| `theme.js` nạp trước Tailwind CDN | CẤM — `tailwind` chưa tồn tại; thứ tự ở 01-CONTRACTS §1 |
