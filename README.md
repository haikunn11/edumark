# EduMark AI - Khung Backbone Dự án Chấm bài Tự động

Khung kiến trúc xương sống (Backbone Architecture) của dự án **EduMark AI** - Hệ thống chấm bài tự luận viết tay thông minh cho giáo viên và học sinh cấp 2 (THCS), ứng dụng mô hình **Swarm 2-Agent (Vision OCR + Reasoning Grader) + Caching + Human-in-the-Loop**.

---

## 📁 Cấu trúc Thư mục Dự án

```
d:\duanvoiemy\
├── backend\
│   └── app\
│       ├── main.py                  # Điểm khởi chạy FastAPI Server & Web App
│       ├── core\
│       │   ├── config.py            # Cấu hình hệ thống, ngưỡng chất lượng ảnh, API keys
│       │   ├── cache.py             # Bộ nhớ đệm InMemory & Redis (MD5 Image Hashing)
│       │   └── storage.py           # Quản lý lưu trữ ảnh bài thi (Local / Cloudflare R2)
│       ├── models\
│       │   └── schemas.py           # Pydantic Schemas (Barem, Submission, Score, Rubric)
│       ├── services\
│       │   ├── preprocessor.py      # Tiền xử lý ảnh (Độ nét, tăng tương phản, nắn thẳng)
│       │   ├── agent1_ocr.py        # Agent 1: Nhận diện chữ viết tay & xuất cấu trúc LaTeX
│       │   ├── agent2_grader.py     # Agent 2: Chấm theo Barem từng bước & bắt lỗi lan truyền
│       │   └── pipeline.py          # Orchestrator điều phối toàn bộ luồng chấm bài
│       ├── api\
│       │   └── routes.py            # REST API endpoints (/exams, /grade, /review)
│       └── static\
│           └── index.html           # Giao diện Web Split-Screen Dashboard tương tác
├── run_test_flow.py                 # Kịch bản chạy Test Flow tự động 5 bước
├── 00_Gemini_Imported_Chat.md       # Lịch sử 24 lượt hội thoại kỹ thuật đã đồng bộ từ Gemini
├── 01_De_Xuat_Du_An_EduMark_AI.md   # Bản đề xuất dự án tổng quan (gửi đối tác/sếp)
└── 02_Ke_Hoach_Trien_Khai_MVP_Web.md# Kế hoạch chi tiết MVP Web Dashboard
```

---

## 🚀 Hướng Dẫn Chạy Test Flow

### 1. Chạy Kịch bản Kiểm thử Tự động (Console Test Flow)
Chạy lệnh sau trong PowerShell hoặc Terminal:
```powershell
python run_test_flow.py
```
**Quy trình kiểm thử bao gồm:**
1. Khởi tạo Barem đề thi Toán 9: Giải phương trình chứa ẩn ở mẫu (2.0 điểm).
2. Tạo ảnh bài làm viết tay của học sinh (ngẫu nhiên hóa nét chữ và nội dung).
3. **Chạy Pipeline lần 1 (Chưa có Cache):** Tiền xử lý ảnh -> Agent 1 bóc tách LaTeX -> Agent 2 đối chiếu barem -> Bắt lỗi thiếu ĐKXĐ (0đ/0.5đ), biến đổi đúng (+1.0đ), quên đối chiếu (0đ/0.5đ) -> Đạt 1.0/2.0 điểm -> Tự sinh lời phê sư phạm.
4. **Chạy Pipeline lần 2 (Kiểm tra Cache):** Nhận diện ảnh trùng khớp MD5 -> Tái sử dụng kết quả trong Cache (0đ tiền API, phản hồi tức thì).
5. **Cơ chế Human-in-the-Loop:** Giáo viên xem xét, cộng 0.25đ khuyến khích và bấm Duyệt (`APPROVED`).

---

### 2. Khởi chạy Giao diện Web Dashboard (Split-Screen)
Để trải nghiệm giao diện người dùng thực tế:
```powershell
python -m uvicorn backend.app.main:app --reload --port 8000
```
Sau đó mở trình duyệt truy cập:
* **Giao diện Chấm đối soát Split-Screen:** [http://127.0.0.1:8000](http://127.0.0.1:8000)
* **Tài liệu API tương tác (Swagger UI):** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
