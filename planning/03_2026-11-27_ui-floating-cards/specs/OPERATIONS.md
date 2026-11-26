# OPERATIONS — Floating Cards UI (v0.7.0)

> **Tier:** 🟢

## 1. Triển khai
1. Không cần migrate DB, không đổi `.env`.
2. `sw.js`: `CACHE_NAME = 'edumark-pwa-v0.7.0'`, `APP_SHELL` thêm `/static/theme.css`, `/static/theme.js` ⇒ điện thoại đã cài PWA nhận bản mới ở lần mở kế tiếp (SW cũ bị xoá ở `activate`).
3. Khởi động như cũ: `python -m uvicorn backend.app.main:app --port 8000`.

## 2. Sinh lại bảng màu
`python scripts/gen_theme_palette.py` ghi đè khối giữa 2 mốc `/* @palette:start */` … `/* @palette:end */` trong `theme.css`. CẤM sửa tay khối này.

## 3. Rollback
- `git revert <sha v0.7.0>` (chỉ đụng `static/`, `scripts/gen_theme_palette.py`, `tests/`, tài liệu). Không có dữ liệu cần khôi phục.
- Người dùng muốn giao diện cũ ngay mà không rollback: bấm nút Sáng/Tối (Tối = bảng màu v0.6).
