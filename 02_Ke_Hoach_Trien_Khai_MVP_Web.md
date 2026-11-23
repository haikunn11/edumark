# KẾ HOẠCH TRIỂN KHAI & ĐẶC TẢ KỸ THUẬT MVP (WEB DASHBOARD)
> **Dự án:** EduMark AI  
> **Phạm vi trọng tâm:** Môn Toán & Tiếng Anh (Cấp THCS & THPT)  
> **Kịch bản ứng dụng:** Chấm bài kiểm tra tập trung trên lớp (In-Class Exam) qua Web Dashboard

---

## 1. QUY TRÌNH NGHIỆP VỤ 5 BƯỚC TRÊN WEB DASHBOARD

```
[BƯỚC 1: Tạo Đề & Barem]
  └─ Giáo viên chọn môn (Toán / Tiếng Anh)
  └─ Nhập hoặc tải file Barem điểm chuẩn (Checkpoints từng 0.25đ)

[BƯỚC 2: Tải Bài Scan Hàng Loạt (Batch Upload)]
  └─ Kéo thả 1 file PDF scan tổng (chứa 40–50 bài thi của cả lớp)
  └─ Hoặc chọn nhiều file ảnh chụp bài làm (JPEG/PNG)

[BƯỚC 3: AI Xử Lý Tự Động Ngầm (Background Processing)]
  └─ Tách trang, nắn thẳng góc, khử bóng mờ bằng OpenCV
  └─ Nhận diện Họ tên / Số báo danh học sinh ở phần đầu trang
  └─ AI đọc chữ viết tay, so khớp barem từng bước giải, chấm điểm sơ khảo

[BƯỚC 4: Giao Diện Chấm Đối Soát 2 Màn Hình (Split-Screen)]
  └─ Bên trái: Ảnh bài thi học sinh (AI khoanh xanh chỗ đúng, đỏ chỗ sai)
  └─ Bên phải: Điểm thành phần từng câu + Lời phê đề xuất
  └─ Thao tác cực nhanh bằng phím tắt: [Space] = Đồng ý, [+/-] = Sửa điểm

[BƯỚC 5: Xuất Báo Cáo & Đồng Bộ Sổ Điểm]
  └─ Xuất file Excel chuẩn mẫu VnEdu, SMAS (1-click tải về)
  └─ Xem biểu đồ nhiệt: Những câu hỏi/dạng bài cả lớp đang bị hổng kiến thức
```

---

## 2. CƠ CHẾ AI XỬ LÝ CHUYÊN BIỆT CHO TOÁN & TIẾNG ANH

### 2.1. Môn Toán học (Chấm Tự luận theo Bước - Step-by-Step)
* **Phân rã logic bước giải:** AI không chỉ nhìn vào đáp số cuối cùng mà đối chiếu từng bước suy luận:
  * *Bước 1:* Đặt điều kiện xác định / Gọi ẩn số (0.25đ).
  * *Bước 2:* Biến đổi tương đương, áp dụng công thức, định lý (0.50đ).
  * *Bước 3:* Tính toán ra nghiệm và kết luận điều kiện (0.25đ).
* **Bảo lưu điểm suy luận (Error Propagation):** Nếu học sinh tính nhầm một phép cộng/trừ ở bước trên nhưng các bước biến đổi logic phía dưới vẫn đúng, hệ thống hỗ trợ trừ điểm tính toán nhưng vẫn bảo lưu điểm phương pháp theo cấu hình của giáo viên.
* **Định vị lỗi trực quan:** AI trả về tọa độ (Bounding Box) khoanh vùng chính xác vị trí bị sai dấu, sai công thức để giáo viên và học sinh nhìn thấy ngay.

### 2.2. Môn Tiếng Anh (Chấm Tích hợp Đa Thể Thức)
* **Trắc nghiệm & Điền từ:** Nhận diện ký tự khoanh tròn hoặc chữ viết tay. Hỗ trợ thiết lập danh sách từ đồng nghĩa được chấp nhận (Acceptable Synonyms).
* **Viết lại câu (Sentence Transformation):** AI phân tích cú pháp, thì của động từ, chính tả và kiểm tra việc giữ nguyên ngữ nghĩa so với câu gốc.
* **Viết đoạn văn ngắn (Writing 80–120 từ):** AI chấm theo 4 tiêu chí chuẩn quốc tế:
  1. *Task Completion* (Đạt yêu cầu đề bài): 25%
  2. *Coherence & Cohesion* (Tính mạch lạc, liên kết câu): 25%
  3. *Lexical Resource* (Vốn từ vựng): 25%
  4. *Grammar & Accuracy* (Đúng ngữ pháp, chính tả): 25%

---

## 3. THIẾT KẾ GIAO DIỆN CHẤM ĐỐI SOÁT (SPLIT-SCREEN DASHBOARD)

Giao diện được thiết kế để giáo viên xử lý một bài thi chỉ trong **15 đến 30 giây**:

* **Màn hình bên trái (60%):** Trình xem ảnh bài làm chất lượng cao. Cho phép thu phóng, xoay ảnh, có các khung viền đánh dấu nhận diện của AI (Xanh lá = Đúng; Đỏ = Lỗi sai; Vàng = Cần giáo viên chú ý).
* **Màn hình bên phải (40%):** Bảng barem chi tiết từng câu. Hiển thị điểm số AI đề xuất, nút tăng/giảm điểm nhanh (`+0.25`, `-0.25`) và khung nhận xét tự động.
* **Hệ thống phím tắt chuyên dụng:**
  * Phím `Space`: Xác nhận bài và chuyển ngay sang bài tiếp theo.
  * Phím `A` / `D`: Lùi lại bài trước / Tiến sang bài sau.
  * Phím `1`, `2`, `3...`: Nhảy nhanh đến các câu hỏi tương ứng.
  * Phím `E`: Chỉnh sửa lời phê học sinh.

---

## 4. KIẾN TRÚC HỆ THỐNG & CÔNG NGHỆ (TECH STACK)

```
[Trình duyệt Giáo viên]
       │ (Tải file PDF scan cả lớp)
       ▼
[Web Frontend: Next.js 14 + TailwindCSS + Shadcn/ui]
       │ (REST API / WebSocket)
       ▼
[Backend Server: Python FastAPI]
       │
   ┌───┴───────────────────────────────────────┐
   ▼                                           ▼
[Hàng đợi tác vụ: Redis + Celery]    [Cơ sở dữ liệu: PostgreSQL]
   │                                           ▲
   ▼                                           │
[Hệ thống AI Workers]                          │
   ├─ Xử lý ảnh: OpenCV (Nắn góc, tách trang)  │
   ├─ HTR / OCR: ViDoctr / VietOCR             │
   └─ Lõi chấm: Gemini Flash Vision ───────────┘
   │
   ▼
[Lưu trữ ảnh: Cloudflare R2 (Miễn phí băng thông tải về)]
```

---

## 5. DỰ TOÁN CHI PHÍ VẬN HÀNH AI (SIÊU TIẾT KIỆM)

Một trong những ưu thế lớn nhất của phương án kỹ thuật này là chi phí vận hành cực thấp:

* **Tầng xử lý ảnh (OpenCV):** Chạy trên máy chủ thông thường (CPU), chi phí = **0đ tiền API**.
* **Tầng chấm tự luận (Gemini 2.0 / 1.5 Flash Vision):** Giá thành token xử lý hình ảnh rất rẻ (~$0.001 - $0.003 mỗi trang bài làm).
* **Ước tính thực tế:**
  * Chi phí AI cho 1 bài thi (gồm 2 trang): **~80đ – 120đ VND / bài**.
  * Chấm trọn vẹn 1 lớp học 45 học sinh chỉ tốn: **~4.000đ – 5.500đ VND**.
  * Lưu trữ đám mây Cloudflare R2: Miễn phí lưu trữ 10GB đầu tiên và **miễn phí 100% cước băng thông tải về (Egress fee)**.

👉 *Biên lợi nhuận cực kỳ cao khi thu phí giáo viên 99.000đ/tháng hoặc gói trường học.*

---

## 6. KẾ HOẠCH TRIỂN KHAI 4 SPRINT (7 TUẦN ĐẾN BẢN CHẠY THẬT)

* **Sprint 1 (Tuần 1 - 2): Khung Web & Quản lý Đề kiểm tra**
  * Xây dựng giao diện Web Next.js (Dashboard, Quản lý lớp, Quản lý học sinh).
  * Module tải file PDF scan hàng loạt và tự động cắt trang lên Cloudflare R2.
* **Sprint 2 (Tuần 3 - 4): Xây dựng AI Pipeline Chấm bài**
  * Tích hợp nhận diện thông tin học sinh (Header: Tên, Lớp, SBD).
  * Xây dựng logic Prompting chấm theo Barem cho môn Toán (bước giải) và Tiếng Anh (trắc nghiệm, viết lại câu).
* **Sprint 3 (Tuần 5 - 6): Hoàn thiện Giao diện Chấm đối soát & Xuất Excel**
  * Xây dựng màn hình Split-Screen Viewer tương tác thời gian thực.
  * Tích hợp phím tắt chấm bài tốc độ cao.
  * Xuất bảng điểm tương thích định dạng mẫu của phần mềm quản lý trường học (VnEdu, SMAS).
* **Sprint 4 (Tuần 7): Thử nghiệm thực tế (Pilot Test)**
  * Chạy thử nghiệm thực tế với 3–5 giáo viên thân thiết (chấm thử 150–200 bài kiểm tra thật).
  * Thu thập phản hồi, tinh chỉnh độ nhạy của AI và tối ưu giao diện.

---
*Tài liệu đặc tả kỹ thuật MVP EduMark AI - 2026*
