# ĐỀ XUẤT DỰ ÁN: PHẦN MỀM CHẤM BÀI TỰ ĐỘNG THÔNG MINH (EDUMARK AI)
> **Tài liệu tổng quan dự án, khảo sát thị trường và đề xuất giải pháp**  
> *Dành cho trao đổi nội bộ, đối tác và định hướng phát triển sản phẩm*

---

## 1. TỔNG QUAN & TẦM NHÌN SẢN PHẨM

**EduMark AI** là giải pháp phần mềm ứng dụng Trí tuệ nhân tạo (AI Multimodal & OCR tiên tiến) hỗ trợ giáo viên chấm bài kiểm tra tự luận và trắc nghiệm một cách tự động, chính xác theo thang điểm (Barem), đồng thời cung cấp phản hồi chi tiết tức thì cho học sinh.

* **Sứ mệnh:** Giảm **80% thời gian chấm bài thủ công** cho giáo viên, chuyển đổi phản hồi học tập từ "chậm trễ - chỉ có điểm số" thành "tức thì - giải thích cặn kẽ từng lỗi sai".
* **Định vị:** Trợ lý chấm bài thông minh (AI Co-pilot) đồng hành cùng giáo viên, không thay thế giáo viên mà tối ưu hóa năng suất dạy học.

---

## 2. KHẢO SÁT NHU CẦU THỰC TẾ & "NỖI ĐAU" TẠI VIỆT NAM

Chương trình giáo dục phổ thông (GDPT 2018) và định dạng đề thi mới yêu cầu đánh giá năng lực tư duy, tăng tỷ trọng tự luận và câu hỏi trắc nghiệm suy luận. Điều này tạo áp lực khổng lồ lên cả thầy và trò:

### 2.1. Đối với Giáo viên (Nỗi đau lớn nhất)
1. **Quá tải số lượng bài chấm:** Mỗi giáo viên THCS/THPT phụ trách từ 3–5 lớp (120–220 học sinh). Mỗi đợt kiểm tra 15 phút, 1 tiết hoặc thi học kỳ tiêu tốn 15–30 giờ chấm bài ngoài giờ lên lớp.
2. **Khó khăn khi chấm tự luận viết tay:** Chữ viết học sinh xấu, bài làm dài, nhiều bước biến đổi. Việc đọc và sửa từng dòng trên giấy vừa mỏi mắt vừa dễ sót lỗi.
3. **Áp lực công bằng theo Barem:** Chấm đến bài thứ 40–50 giáo viên thường mệt mỏi, dẫn đến chấm lướt, thiếu đồng nhất giữa bài đầu và bài cuối.
4. **Nhập liệu sổ điểm thủ công:** Sau khi chấm xong mất thêm nhiều giờ nhập điểm vào hệ thống VnEdu, SMAS...
5. **Nạn chép bài nhau:** Khó phát hiện các bài làm giống hệt nhau khi phải chấm xấp bài dày đặc.

### 2.2. Đối với Học sinh
1. **Độ trễ nhận kết quả quá lâu:** Sau 1–2 tuần mới được trả bài kiểm tra. Lúc này học sinh đã quên mất cách mình tư duy hay lý do mình chọn đáp án đó.
2. **Chỉ nhận điểm số, không biết tại sao sai:** Bài trả về thường chỉ có con điểm và vài nét gạch đỏ, thiếu lời giải thích chi tiết rằng sai từ bước nào để sửa chữa.
3. **Không có bài tập củng cố tức thì:** Học sinh không biết tìm dạng bài tương tự ở đâu để bù đắp ngay lỗ hổng kiến thức vừa phát hiện.

---

## 3. PHÂN TÍCH THỊ TRƯỜNG & KHOẢNG TRỐNG CẠNH TRANH

| Ứng dụng | Điểm mạnh | Hạn chế / Điểm nghẽn |
| :--- | :--- | :--- |
| **Azota / Shub Classroom** | Phổ biến tại VN, giao bài tập tốt, chấm trắc nghiệm bằng phiếu scan OMR nhanh. | **Chấm tự luận rất thủ công:** Giáo viên vẫn phải zoom ảnh rồi dùng ngón tay/bút cảm ứng gạch chấm từng bài, chưa có AI phân tích barem. |
| **TNMaker / ZipGrade** | Quét phiếu trắc nghiệm trên điện thoại tiện lợi. | Bắt buộc in phiếu mẫu OMR tốn kém; hoàn toàn **không chấm được tự luận**. |
| **Photomath / Qanda** | Giải toán thông minh cho học sinh. | Ứng dụng 1 chiều cho học sinh tra lời giải; **không phải công cụ quản lý - chấm bài theo barem cho giáo viên**. |
| **Gradescope (Mỹ)** | AI gom nhóm bài chấm theo tiêu chí rất tốt. | Giá thành rất đắt (USD), không tối ưu cho chữ viết tay tiếng Việt và chương trình thi của Bộ GD&ĐT Việt Nam. |

👉 **Khoảng trống thị trường của EduMark AI:**  
Một phần mềm thuần Việt, chi phí rẻ, tập trung giải quyết xuất sắc bài toán: **"Chấm bài tự luận chữ viết tay theo Barem từng bước + Quét trắc nghiệm linh hoạt trên giấy vở thông thường"**.

---

## 4. ĐỀ XUẤT GIẢI PHÁP: 3 TRỤ CỘT CỐT LÕI CỦA EDUMARK AI

### Trụ cột 1: Động cơ Chấm thông minh theo Barem (Rubric Grading Engine)
* **Môn Toán:** Nhận diện chữ viết tay và công thức toán học. So khớp bài làm với Barem theo từng bước (Step-by-step): nhận diện bước đặt điều kiện, bước biến đổi đại số, bước kết luận. Sai bước nào trừ điểm bước đó, khoanh đỏ vị trí nhầm lẫn.
* **Môn Tiếng Anh:** Tự động chấm trắc nghiệm ngữ pháp, câu điền từ (chấp nhận từ đồng nghĩa), viết lại câu, và đánh giá đoạn văn ngắn theo tiêu chí CEFR (từ vựng, ngữ pháp, độ mạch lạc).
* **Trắc nghiệm linh hoạt:** Học sinh chỉ cần ghi đáp án vào giấy thi thông thường (ví dụ: `1.A, 2.B...`), AI tự đọc đáp án mà không cần in phiếu OMR chuyên dụng tốn kém.

### Trụ cột 2: Cơ chế "Giáo viên làm chủ" (Human-in-the-Loop)
* AI đóng vai trò **"Trợ lý chấm sơ khảo"**.
* Giao diện đối soát chia đôi màn hình: Bên trái là ảnh bài làm học sinh (có highlight xanh/đỏ của AI), bên phải là barem điểm.
* Giáo viên chỉ cần duyệt nhanh bằng phím tắt (`Space` = Đồng ý, hoặc nhấp chuột để điều chỉnh điểm nếu muốn). Tốc độ chấm tăng gấp **5 đến 10 lần** (chỉ còn 15-30 giây/bài).

### Trụ cột 3: Lời phê Sư phạm & Báo cáo Lỗ hổng Kiến thức
* **Lời phê cá nhân hóa:** AI tự sinh nhận xét mang tính động viên và chỉ rõ lỗi sai cụ thể của từng học sinh.
* **Bản đồ nhiệt cả lớp (Class Heatmap):** Báo cáo ngay cho giáo viên: Dạng bài nào có 60-70% học sinh làm sai để giáo viên dành thời gian sửa bài trên lớp.
* **Phát hiện chép bài:** Tự động gom nhóm các bài có lời giải hoặc lỗi sai giống hệt nhau bất thường.
* **Xuất điểm 1-click:** Xuất file Excel chuẩn mẫu để đồng bộ ngay vào hệ thống VnEdu, SMAS.

---

## 5. MÔ HÌNH KINH DOANH KHẢ THI (BUSINESS MODEL)

1. **Gói Giáo viên Cá nhân (B2C - Freemium):**
   * *Bản Miễn phí:* Chấm trắc nghiệm không giới hạn + tặng 50 bài tự luận/tháng.
   * *Bản Pro (99.000đ – 149.000đ/tháng):* Chấm tự luận không giới hạn, phân tích phổ điểm, xuất file VnEdu/SMAS, lưu trữ bài thi cả năm học.
2. **Gói Trường học & Trung tâm Luyện thi (B2B SaaS):**
   * Bán bản quyền theo trường hoặc theo đầu học sinh (30.000đ – 50.000đ / học sinh / năm).
   * Cung cấp ngân hàng đề thi chuẩn hóa, quản lý phân quyền giáo viên bộ môn, thống kê chất lượng giảng dạy toàn trường.

---

## 6. LỘ TRÌNH PHÁT TRIỂN (ROADMAP)

* **Giai đoạn 1 (MVP - 2 tháng):** Hoàn thiện Web Dashboard cho giáo viên; tập trung chấm bài kiểm tra trên lớp môn **Toán & Tiếng Anh**; tính năng tải file scan hàng loạt; giao diện đối soát 2 màn hình; xuất điểm Excel.
* **Giai đoạn 2 (Tăng trưởng - 3-5 tháng):** Mở rộng sang các môn Tự nhiên khác (Lý, Hóa) và môn Văn; bổ sung Mobile App quét bài bằng camera; thuật toán phát hiện gian lận/chép bài.
* **Giai đoạn 3 (Hệ sinh thái - 6+ tháng):** Tính năng gợi ý bài tập khắc phục điểm yếu cho học sinh (Adaptive Learning); cổng kết nối API với các nền tảng giáo dục lớn.

---
*Tài liệu soạn thảo phục vụ dự án EduMark AI - 2026*
