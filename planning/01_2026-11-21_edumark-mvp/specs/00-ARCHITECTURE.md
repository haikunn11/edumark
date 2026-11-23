# 00-ARCHITECTURE — Kiến Trúc Nền Tảng EduMark AI MVP

> **Mục đích:** Định nghĩa mục tiêu, non-goals, bất biến kiến trúc và thứ tự đọc spec.
> **Tier:** 🔴 (Strongest — quyết định kiến trúc nền tảng)

---

## 1. Mục Tiêu (Goals)

| # | Mục tiêu                                                                              | Đo bằng                                               |
|---|----------------------------------------------------------------------------------------|--------------------------------------------------------|
| G1 | Chấm tự động bài tự luận Toán THCS (Đại số lớp 8-9) với độ chính xác ≥ 85%          | Benchmark 20 bài vs teacher chấm tay                  |
| G2 | Chi phí API ≤ 120đ VND/bài (~\$0.003)                                                | Log chi phí thực tế trên 100 bài                       |
| G3 | Teacher review + approve/override trong ≤ 30 giây/bài                                 | UX timing test trên 10 teacher                         |
| G4 | Cache OCR giảm ≥ 50% số lần gọi API khi chấm lại                                     | Cache hit rate metric                                  |
| G5 | Hệ thống chạy được trên laptop GV (không cần GPU, 4GB RAM)                            | Test trên máy ảo 4GB RAM                               |

---

## 2. Non-Goals (Phạm Vi KHÔNG Làm Ở MVP)

| # | Điều KHÔNG làm                                              | Lý do                                              |
|---|-------------------------------------------------------------|-----------------------------------------------------|
| N1 | Chấm Hình học (vẽ hình, chứng minh hình)                   | Cần vision model chuyên biệt cho diagram            |
| N2 | Multi-tenant / nhiều trường cùng lúc                        | Phức tạp hóa DB, auth — để Phase 3                  |
| N3 | Mobile app native (iOS/Android)                             | PWA đủ cho MVP, native app ở Phase 2                 |
| N4 | Chấm Tiếng Anh                                             | Khác rubric hoàn toàn — Phase 2                      |
| N5 | Real-time collaborative grading (nhiều GV chấm cùng bài)   | Over-engineering cho MVP                             |
| N6 | Tự train/fine-tune model riêng                              | Dùng Gemini API + prompt engineering đủ cho MVP      |

---

## 3. Bất Biến Kiến Trúc (Architectural Invariants)

Các quy tắc dưới đây **BẮT BUỘC** tuân thủ xuyên suốt dự án. Vi phạm = phải có quyết định tường minh trong Decision Log.

### 3.1. Swarm 2-Agent (Tách biệt OCR vs Grading)

```
┌─────────────┐    LaTeX/JSON     ┌──────────────┐    Score/Feedback    ┌──────────────┐
│  Ảnh viết   │ ──────────────► │  Agent 1     │ ──────────────────► │  Agent 2     │
│  tay scan   │                 │  Vision OCR  │                     │  Grader      │
└─────────────┘                 └──────────────┘                     └──────────────┘
                                       ▲                                    │
                                       │ cache hit                          ▼
                                ┌──────────────┐                   ┌──────────────┐
                                │  MD5 Cache   │                   │  HITL Review │
                                └──────────────┘                   │  Dashboard   │
                                                                   └──────────────┘
```

**BẮT BUỘC:**
- Agent 1 và Agent 2 PHẢI là 2 service/module riêng biệt, giao tiếp qua data contract (không gọi nhau trực tiếp).
- Output của Agent 1 PHẢI là structured data (JSON/dict), không phải plain text.
- Agent 2 KHÔNG được gọi lại Vision API — chỉ nhận text/LaTeX input.

**CẤM:**
- CẤM merge Agent 1 + Agent 2 thành 1 prompt/1 API call duy nhất.
- CẤM Agent 2 truy cập trực tiếp ảnh gốc.

### 3.2. Stateless Backend + External State

**BẮT BUỘC:**
- FastAPI server PHẢI stateless (không lưu state trong memory giữa các request, ngoại trừ cache layer).
- Mọi state persist PHẢI qua Storage Engine (file system hoặc cloud) + Database.
- Cache layer là optional accelerator, hệ thống PHẢI hoạt động đúng khi cache bị xóa.

### 3.3. Human-in-the-Loop (HITL) Là Bắt Buộc

**BẮT BUỘC:**
- KHÔNG BAO GIỜ tự động finalize điểm mà không có teacher review (ít nhất là confirm).
- Mọi kết quả chấm PHẢI có trạng thái: `pending_review` → `approved` / `overridden`.
- Teacher PHẢI có khả năng override bất kỳ điểm thành phần nào.

**CẤM:**
- CẤM auto-approve (kể cả confidence 100%) ở MVP — teacher phải ít nhất click confirm.

### 3.4. Error Propagation Awareness

**BẮT BUỘC:**
- Agent 2 PHẢI phát hiện Error Propagation: sai tính toán ở bước N nhưng phương pháp đúng ở bước N+1 → giữ điểm phương pháp.
- Kết quả chấm PHẢI ghi rõ: `error_propagation_detected: true/false` và `affected_steps: [...]`.

### 3.5. Confidence-Based Flagging

| Confidence Score | Màu    | Hành vi                              |
|:---------------:|:------:|:-------------------------------------|
| ≥ 90%           | 🟢 Xanh | Gợi ý approve, teacher click confirm |
| 70% - 89%       | 🟡 Vàng | Yêu cầu review kỹ                    |
| < 70%           | 🔴 Đỏ   | Flag bắt buộc review                 |

---

## 4. Tech Stack Cố Định (MVP)

| Layer           | Công nghệ                        | Lý do chọn                                    |
|:----------------|:----------------------------------|:-----------------------------------------------|
| Backend         | Python 3.12, FastAPI, Uvicorn     | Ecosystem ML/AI tốt nhất, async native         |
| Validation      | Pydantic V2                       | Type safety, serialization, schema generation  |
| Image Processing| Pillow + OpenCV                   | EXIF, blur detection, contrast, deskew          |
| OCR (Agent 1)   | Gemini 1.5/2.0 Flash Vision API   | Giá rẻ, multi-modal, hỗ trợ tiếng Việt tốt   |
| Grading (Agent 2)| Rule-based + LLM fallback        | Deterministic cho Toán, LLM cho edge cases      |
| Cache           | In-Memory (dev) → Redis (prod)    | Simple first, scale later                       |
| Storage         | Local FS (dev) → Cloudflare R2    | Chi phí thấp, S3-compatible                     |
| Database        | SQLite (dev) → PostgreSQL (prod)  | Zero-config dev, robust prod                    |
| Frontend        | HTML5 + TailwindCSS (vanilla JS)  | Không cần build step, nhẹ, nhanh               |

---

## 5. Router — Thứ Tự Đọc SPEC

Đọc theo thứ tự sau để hiểu toàn bộ thiết kế:

1. **`00-ARCHITECTURE.md`** (file này) — Nắm bất biến trước.
2. **`01-CONTRACTS.md`** — Hiểu data shape đi qua hệ thống.
3. **`SPEC-P01-ocr-pipe.md`** — Agent 1 biến ảnh thành data.
4. **`SPEC-P02-grader.md`** — Agent 2 biến data thành điểm.
5. **`SPEC-P03-dashboard.md`** — UI cho teacher tương tác.
6. **`SPEC-P04-batch.md`** — Mở rộng sang batch + export.
7. **`OPERATIONS.md`** — Deploy và vận hành.
8. **`TESTING-ACCEPTANCE.md`** — Chốt: đủ tiêu chuẩn chưa.

---

## 6. Vùng Cấm — Điều Đã Cân Nhắc Và Quyết Định KHÔNG Làm

| # | Ý tưởng bị bác                                      | Lý do bác bỏ                                                        |
|---|------------------------------------------------------|----------------------------------------------------------------------|
| X1 | Dùng Tesseract OCR thay Gemini Vision               | Tesseract kém với chữ viết tay tiếng Việt, không hiểu ngữ cảnh Toán |
| X2 | Fine-tune riêng model OCR trên dataset VN            | Tốn tiền, tốn thời gian, Gemini Flash đủ tốt với prompt engineering  |
| X3 | Microservice architecture từ đầu                     | Over-engineering cho MVP 1-dev, monolith modular đủ                   |
| X4 | React/Vue/Next.js frontend                           | Thêm build step, thêm complexity, vanilla JS đủ cho dashboard đơn   |
| X5 | Dùng LangChain/LlamaIndex orchestration              | Abstraction thừa cho 2-agent simple pipeline                          |
| X6 | gRPC giữa Agent 1 ↔ Agent 2                         | HTTP REST + JSON đủ, gRPC phức tạp hóa debug                         |
