# SPEC-P04 — Batch Upload, PDF Split & Excel Export

> **Phạm vi:** Upload nhiều bài cùng lúc (ZIP/PDF), chấm hàng loạt, export kết quả.
> **Tier:** 🟠 (Standard — logic mở rộng)
> **Modules:** `services/batch.py` (tạo mới), `api/routes.py`

---

## 1. Batch Upload

### 1.1. ZIP Upload

```python
async def process_zip_upload(
    file: UploadFile,
    exam_id: str
) -> BatchJob:
    """
    1. Validate: file.content_type == "application/zip", size ≤ 50MB
    2. Extract ZIP → temp directory
    3. Filter: chỉ lấy .jpg, .jpeg, .png (bỏ __MACOSX, .DS_Store)
    4. Sort theo tên file (tự nhiên: 1.jpg, 2.jpg, ..., 10.jpg)
    5. Tạo BatchJob, enqueue từng ảnh vào pipeline
    """
```

### 1.2. PDF Multi-Page Upload

```python
async def process_pdf_upload(
    file: UploadFile,
    exam_id: str
) -> BatchJob:
    """
    1. Validate: file.content_type == "application/pdf", size ≤ 50MB
    2. Split PDF → 1 ảnh/trang (dùng pdf2image hoặc PyMuPDF)
    3. Resolution: 300 DPI
    4. Mỗi trang = 1 submission
    5. Tạo BatchJob, enqueue
    """
```

**BẮT BUỘC:**
- Giới hạn: tối đa 50 bài/batch (50 ảnh hoặc 50 trang PDF).
- File ZIP/PDF ≤ 50MB.
- Mỗi ảnh đơn lẻ ≤ 10MB.
- Đặt tên submission tự động: `{exam_id}_batch_{index:03d}`.

**CẤM:**
- CẤM chấm quá 50 bài/batch (rate limit protection).
- CẤM lưu ảnh temp quá 1 giờ (cleanup job).

---

## 2. Batch Job Queue

### 2.1. Schema

```python
class BatchJob(BaseModel):
    job_id: str                          # UUID
    exam_id: str
    status: Literal["queued", "processing", "completed", "failed"]
    total_submissions: int
    completed_submissions: int = 0
    failed_submissions: int = 0
    results: list[GradingResult] = []
    created_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None
```

### 2.2. Processing

```python
async def process_batch_job(job: BatchJob) -> BatchJob:
    """
    For each submission in job:
      1. Run pipeline.grade(image, rubric)
      2. Update job.completed_submissions
      3. On error: job.failed_submissions += 1, log, continue (don't stop batch)
    After all:
      4. job.status = "completed"
      5. job.completed_at = now()
    """
```

**BẮT BUỘC:**
- Chạy tuần tự (không parallel) ở MVP để tránh rate limit Gemini API.
- Nếu 1 bài fail → log, tiếp tục bài tiếp (không dừng cả batch).
- Cập nhật progress real-time (SSE hoặc polling mỗi 2s).
- Giữ job result trong memory 24h (cleanup sau đó).

---

## 3. Excel Export

### 3.1. Export Format — Standard

```
| STT | Học sinh | Câu 1a | Câu 1b | Câu 2 | Câu 3 | Tổng | Phần trăm | Confidence | Status    |
|-----|----------|--------|--------|-------|-------|------|-----------|------------|-----------|
| 1   | HS_001   | 2.5    | 1.0    | 3.0   | 1.5   | 8.0  | 80%       | 🟢 92%     | Approved  |
| 2   | HS_002   | 1.0    | 0.5    | 2.0   | 0.0   | 3.5  | 35%       | 🔴 61%     | Override  |
```

### 3.2. VnEdu Format

```
| STT | Họ và tên    | Điểm miệng | Điểm 15p | Điểm 1 tiết | Điểm HK |
|-----|--------------|-------------|----------|-------------|---------|
| 1   | (blank)      |             | 8.0      |             |         |
```

### 3.3. SMAS Format

```
| MaHS    | HoTen | DiemKT | GhiChu |
|---------|-------|--------|--------|
| HS_001  |       | 8.0    |        |
```

### 3.4. Implementation

```python
def export_to_excel(
    exam_id: str,
    results: list[GradingResult],
    format: Literal["standard", "vnedu", "smas"] = "standard"
) -> bytes:
    """
    Dùng openpyxl để tạo .xlsx file.
    Returns: bytes content of xlsx file.
    """
```

**BẮT BUỘC:**
- CHỈ export submissions có status `"approved"` hoặc `"overridden"`.
- File name format: `{exam_title}_{date}_results.xlsx`.
- Sheet 1: Bảng điểm. Sheet 2: Chi tiết chấm (feedback từng câu).
- Header row bold, có filter.

**CẤM:**
- CẤM export submissions `"pending_review"` — teacher phải review hết trước.
- CẤM dùng `csv` module — PHẢI dùng `openpyxl` cho .xlsx.

---

## 4. Progress UI

### 4.1. Batch Progress Bar

```html
<div class="batch-progress">
  <div class="progress-bar" style="width: 30%">3/10</div>
  <div class="stats">
    ✅ 2 passed | ⚠️ 1 flagged | ❌ 0 failed
  </div>
  <div class="eta">ETA: ~2 phút còn lại</div>
</div>
```

### 4.2. Real-time Updates

```
Option A (MVP): Polling GET /api/submissions/batch/{job_id} mỗi 2 giây
Option B (Future): Server-Sent Events (SSE) cho real-time push
```

**MVP dùng Option A** (polling). SSE cho Phase 2.

---

## 5. Dependencies Mới (cần pip install)

```
openpyxl>=3.1.0      # Excel export
PyMuPDF>=1.23.0      # PDF splitting (fitz)
# hoặc pdf2image + poppler
```

---

## 6. Bảng Lỗi Module

| Lỗi                  | Nguyên nhân                   | Hành vi bắt buộc                                    |
|:----------------------|:------------------------------|:-----------------------------------------------------|
| `ZipExtractionError`  | ZIP corrupt / encrypted        | Trả E001, "File ZIP không đọc được"                  |
| `PDFSplitError`       | PDF corrupt / password-locked  | Trả E001, "File PDF không đọc được"                  |
| `BatchSizeLimitError` | > 50 bài trong 1 batch         | Trả 400, "Tối đa 50 bài mỗi lần upload"             |
| `FileTooLargeError`   | > 50MB                         | Trả E003                                              |
| `ExportNoDataError`   | Không có bài approved để export | Trả 400, "Chưa có bài nào được duyệt"                |
