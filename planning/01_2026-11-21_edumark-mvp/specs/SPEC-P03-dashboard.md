# SPEC-P03 — Split-Screen Web Dashboard & Teacher HITL

> **Phạm vi:** Giao diện web cho teacher: upload ảnh, xem kết quả chấm, approve/override.
> **Tier:** 🟠 (Standard — logic giao diện)
> **Files:** `static/index.html`, `api/routes.py`

---

## 1. Layout — Split-Screen Design

```
┌──────────────────────────────────────────────────────────────┐
│  🎓 EduMark AI — Chấm Bài Tự Động               [Dark/Light]│
├─────────────────────────────┬────────────────────────────────┤
│                             │                                │
│      ẢNH BÀI VIẾT TAY      │       KẾT QUẢ CHẤM           │
│      (Zoomable, Pan)        │                                │
│                             │  Câu 1a: 2.5/3.0  🟢          │
│                             │  ├─ ĐKXĐ: 0.5/0.5 ✅          │
│                             │  ├─ Biến đổi: 1.5/1.5 ✅      │
│                             │  └─ Kết luận: 0.5/1.0 ⚠️      │
│                             │     "Thiếu đối chiếu ĐKXĐ"    │
│      [← Prev] [Next →]     │                                │
│                             │  Câu 2: 0/2.0  🔴              │
│                             │  └─ "Không tìm thấy bài làm"  │
│                             │                                │
│                             │  ──────────────────            │
│                             │  TỔNG: 6.5/10  (65%)  🟡      │
│                             │                                │
│                             │  [✅ Approve] [✏️ Override]    │
│                             │  [❌ Reject]  [⏭ Skip]        │
│                             │                                │
│                             │  Teacher notes: [___________]  │
├─────────────────────────────┴────────────────────────────────┤
│  Progress: ████████░░ 8/10 bài  │  ⌨ Space=Next A=Approve   │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. Upload Zone

### 2.1. Single Upload

```
Drag & Drop Zone
┌─────────────────────────┐
│   📷 Kéo thả ảnh vào   │
│   hoặc Click để chọn    │
│                         │
│   JPEG, PNG, PDF        │
│   Tối đa 10MB          │
└─────────────────────────┘
  [Chọn Barem ▼]  [Chấm Bài]
```

**BẮT BUỘC:**
- Hiện preview ảnh trước khi gửi chấm.
- Validate client-side: file type (JPEG/PNG/PDF), size ≤ 10MB.
- Loading spinner khi đang chấm (gọi API).
- Hiện thời gian xử lý sau khi xong.

### 2.2. Batch Upload

```
[📦 Upload Nhiều Bài]
  → Chọn ZIP hoặc PDF nhiều trang
  → Progress bar: 3/10 bài đã chấm...
  → Hoàn tất: [Xem Kết Quả] [Export Excel]
```

---

## 3. Kết Quả Chấm — Chi Tiết Hiển Thị

### 3.1. Mã Màu Confidence

| Confidence | Màu                      | Icon | Hành vi UI                        |
|:-----------|:--------------------------|:-----|:----------------------------------|
| ≥ 90%      | `bg-green-100 text-green-800` | 🟢  | Gợi ý "Approve" tự động highlight |
| 70-89%     | `bg-yellow-100 text-yellow-800` | 🟡 | Yêu cầu review                    |
| < 70%      | `bg-red-100 text-red-800`    | 🔴  | Cảnh báo đỏ, bắt buộc review      |

### 3.2. Error Propagation Display

Khi `error_propagation_detected == True`:

```html
<div class="bg-purple-50 border-l-4 border-purple-500 p-3">
  ⚡ Lỗi lan truyền phát hiện!
  Bước "biến đổi" sai tính toán → ảnh hưởng "kết luận"
  Điểm phương pháp được giữ: +0.5
</div>
```

### 3.3. Teacher Override Form

```
Câu 1a: AI chấm 2.5/3.0
  [Sửa điểm: ___] / 3.0
  [Ghi chú: ________________________________]

Câu 2: AI chấm 0/2.0
  [Sửa điểm: ___] / 2.0
  [Ghi chú: ________________________________]
```

**BẮT BUỘC:**
- Override PHẢI validate: 0 ≤ new_points ≤ max_points.
- Mọi override PHẢI có ghi chú (teacher_notes required khi override).
- Hiện so sánh: "AI: 2.5 → Teacher: 3.0 (+0.5)".
- Lưu lịch sử override (ai chấm bao nhiêu, teacher sửa bao nhiêu).

**CẤM:**
- CẤM cho phép override mà không ghi chú lý do.
- CẤM xóa lịch sử override.

---

## 4. Keyboard Shortcuts

| Phím        | Hành vi                              |
|:------------|:-------------------------------------|
| `Space`     | Next submission (bài tiếp theo)       |
| `A`         | Approve bài hiện tại                  |
| `D`         | Reject (Decline) bài hiện tại        |
| `+` / `=`   | Zoom in ảnh                           |
| `-`         | Zoom out ảnh                          |
| `R`         | Reset zoom                            |
| `O`         | Mở Override form                      |
| `Esc`       | Đóng Override form / Cancel           |
| `Ctrl+E`   | Export Excel                           |

**BẮT BUỘC:**
- Shortcuts CHỈ hoạt động khi không focus vào input/textarea.
- Hiện tooltip shortcuts ở footer bar.
- `A` (Approve) yêu cầu confirm nếu confidence < 70% (red).

---

## 5. Dark/Light Mode

**BẮT BUỘC:**
- Mặc định: Dark mode (teacher dùng buổi tối nhiều).
- Toggle button góc trên phải.
- Lưu preference vào `localStorage`.
- Mã màu confidence PHẢI tương phản đủ ở cả 2 mode.

---

## 6. Responsive & Mobile

| Breakpoint    | Layout                                           |
|:--------------|:-------------------------------------------------|
| ≥ 1024px      | Split-screen ngang (ảnh trái, kết quả phải)      |
| 768-1023px    | Split-screen ngang, panel nhỏ hơn                |
| < 768px       | Stack dọc (ảnh trên, kết quả dưới), swipe nav    |

---

## 7. Bảng Lỗi UI

| Lỗi                   | Nguyên nhân               | Hành vi bắt buộc                                      |
|:-----------------------|:--------------------------|:-------------------------------------------------------|
| Upload failed          | File type/size invalid     | Toast đỏ "File không hợp lệ: chỉ JPEG/PNG/PDF ≤ 10MB" |
| API timeout            | Server chậm/die            | Spinner + "Đang xử lý... thử lại?" sau 30s             |
| Override validation    | Điểm ngoài range           | Inline error "Điểm phải từ 0 đến {max}"                |
| Shortcut conflict      | Focus trong input           | Disable shortcuts khi focus input                       |
