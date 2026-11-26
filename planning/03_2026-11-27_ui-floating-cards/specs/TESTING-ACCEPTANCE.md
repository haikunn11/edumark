# TESTING-ACCEPTANCE — Floating Cards UI (v0.7.0)

> **Tier:** 🔴

## 1. Test tự động (`tests/test_v07_ui_theme.py`)
| ID | Nội dung | Kỳ vọng |
|---|---|---|
| `V7-01` | `GET /` và `/app` | 200, chứa `v0.7`, link `/static/theme.css`, script `/static/theme.js` đứng sau Tailwind CDN |
| `V7-02` | `GET /static/theme.css`, `/static/theme.js`, `/static/sw.js` | 200; css có đủ 10 họ × 11 sắc cho cả `:root` và `html.dark`; sw chứa `v0.7.0` + 2 file theme |
| `V7-03` | Mọi id v0.6 còn trong HTML v0.7 | 0 id thiếu (index & mobile) |
| `V7-04` | `<html>` không còn `class="dark"` cứng | Đúng cho cả 2 trang |
| `V7-05` | Hàm mới tồn tại: `computeAttention`, `renderAttentionBar`, `loadHomeRecent`, `toggleThemeMobile` | Có trong HTML |
| `V7-06` | `computeAttention` chạy trên Node với dữ liệu thật B.Nam (câu 1d 0.8 ≠ 1.0) và bài vượt barem | warn câu 1d; error "vượt barem"; bài sạch ⇒ `[]` (bỏ qua nếu máy không có `node`) |
| Hồi quy | Toàn bộ pytest cũ | 0 fail, số test không giảm |

## 2. Bằng chứng thị giác (Playwright, 1440×900 & 390×844)
Lưu tại `d:\dự án ma\runtime\edumark-redesign\after\` (ngoài repo):
`desktop-home`, `desktop-loaded-{verify,score,matrix}`, `desktop-dark`, các modal (history, upload, folder, rubric, gradebook, api-key, override, qr), `mobile-{capture,result,history}`, `mobile-dark`.
Điều kiện: **0 lỗi `pageerror`/console error** do mã trang (lỗi mạng tới CDN bên ngoài ghi riêng).

## 3. Exit Gates
| Gate | Điều kiện | Local | Server |
|---|---|:---:|:---:|
| `G7-01` | V7-01…V7-06 PASS | ✅ | ⬜ |
| `G7-02` | Toàn bộ pytest cũ PASS, 0 skip mới | ✅ | ⬜ |
| `G7-03` | Ảnh nghiệm thu đủ danh sách §2, 0 JS error | ✅ | ⬜ |
| `G7-04` | Chủ dự án xem ảnh & đồng ý | ⬜ | ⬜ |
| `G7-05` | `init_brain.js --check` báo `NÃO ĐÃ OK` | ✅ | ⬜ |

## 4. Bằng chứng (2026-11-27, local)
- `pytest tests/ -n 4` qua `heavy.sh`: `87 passed in 28.82s`, exit 0.
- Đột biến: xoá `id="cache-badge"` + vô hiệu nhánh lệch tiêu chí + đặt lại `class="dark"` ⇒ `3 failed, 5 passed` (V7-03, V7-04[/], V7-06); khôi phục (sha256 khớp) ⇒ `8 passed`.
- Playwright `accept.py all`: 27 PNG + `js-errors-all.json` = `[]`.
- `init_brain.js --check`: NÃO ĐÃ OK.
