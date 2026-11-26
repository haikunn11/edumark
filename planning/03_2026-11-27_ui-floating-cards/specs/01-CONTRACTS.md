# 01-CONTRACTS — Floating Cards UI (v0.7.0)

> **Tier:** 🔴

## 1. File mới
| File | Vai trò | Nạp ở đâu |
|---|---|---|
| `backend/app/static/theme.css` | Biến màu sáng/tối, token `--em-*`, component `.em-*`, font | `<link rel="stylesheet" href="/static/theme.css">` trong `<head>` của cả 2 trang |
| `backend/app/static/theme.js` | Đặt `tailwind.config` (màu qua biến, font) + áp theme từ `localStorage` | `<script src="/static/theme.js"></script>` **ngay sau** script Tailwind CDN, trước mọi script khác |
| `scripts/gen_theme_palette.py` | Sinh khối biến màu trong `theme.css` (tái lập được) | Chạy tay khi đổi bảng màu |

## 2. Biến & lưu trữ
- Biến màu: `--tw-<family>-<shade>: R G B;` với family ∈ {slate, indigo, sky, cyan, teal, emerald, amber, rose, violet, purple}, shade ∈ {50,100,200,…,900,950}.
- Tailwind dùng `rgb(var(--tw-f-s) / <alpha-value>)` ⇒ mọi class `bg-x-500/20` vẫn đúng alpha.
- `localStorage['theme']` ∈ {`'light'`, `'dark'`}; thiếu/không đọc được ⇒ `'light'`. Đọc/ghi luôn bọc `try/catch`.
- `<html>` **không** còn `class="dark"` cứng; `theme.js` tự thêm khi `theme === 'dark'`.

## 3. API JS mới / đổi
```js
// index.html
toggleDarkMode(): void            // giữ tên; đổi class 'dark' trên <html>, lưu localStorage, đổi icon #dark-icon
computeAttention(data): Attention[]
  // Attention = { level: 'error' | 'warn', qid: string|null, title: string, detail: string }
  // Thứ tự ưu tiên & điều kiện (dung sai EPS = 0.01):
  //  1. error : tổng (final_score ?? ai_total_score) > total_points + EPS        → title "Tổng điểm vượt barem"
  //  2. error : earned_points câu > max_score câu + EPS                           → "Câu X vượt điểm tối đa"
  //  3. warn  : |earned_points câu − Σ criteria.earned_points| > EPS (khi có tiêu chí) → "Câu X: tiêu chí a đ nhưng câu ghi b đ"
  //  4. warn  : needs_review === true                                             → "Câu X cần thầy cô xem" + review_reasons[0]
renderAttentionBar(data): void    // ẩn #attention-bar nếu rỗng; hiện mục ưu tiên cao nhất + "+N mục khác"
loadHomeRecent(): Promise<void>   // GET /api/v1/submissions?limit=25, vẽ #home-recent-list (tối đa 6), đếm bài "chưa rõ tên"
switchRightTab(tab)               // giữ tên; chỉ đổi class trạng thái nút
toggleThemeMobile(): void         // mobile.html, cùng hợp đồng localStorage
```
- Lỗi mạng ở `loadHomeRecent` ⇒ hiện dòng "Không tải được lịch sử" trong danh sách, **không** `alert`, không ném lỗi ra ngoài.

## 4. DOM id bất biến
Toàn bộ id của v0.6 (trích từ commit `3db70cb`) được liệt kê trong `tests/test_v07_ui_theme.py::V06_IDS_INDEX` và `V06_IDS_MOBILE`. Test V7-03 thất bại nếu thiếu bất kỳ id nào.

## 5. Id mới
| Trang | Id | Vai trò |
|---|---|---|
| index | `attention-bar`, `attention-title`, `attention-detail`, `attention-more`, `attention-btn` | Thanh chú ý |
| index | `home-recent`, `home-recent-list`, `home-unnamed-bar`, `home-unnamed-count` | Bài vừa chấm ở panel phải khi chưa mở bài |
| index | `btn-new-grade`, `new-grade-menu` | Nút chính + menu |
| index | `chip-time`, `chip-cost` | Chip nhỏ trong thẻ điểm |
| index | `score-card`, `review-workspace`, `action-bar` | Vùng chấm — ẩn ở trang chủ, `renderGradingRecord` bỏ `hidden` |
| index | `student-avatar` | Avatar viền màu theo trạng thái ở header |
| index | `score-ring-arc`, `score-ring-pct` | Vòng tiến độ tổng điểm (`setTotalScoreDisplay`) |
| index | `right-scroll` | Vùng cuộn cột phải (`position: relative`; `focusAttention` cuộn bằng `scrollTo`) |
| mobile | `mob-dark-icon` | Icon nút Sáng/Tối |
