# TESTING-ACCEPTANCE — Ma Trận Test & Nghiệm Thu

> **Mục đích:** Định nghĩa tất cả test cases, benchmark, và Exit Gates cho MVP.
> **Tier:** 🔴 (Strongest — cổng nghiệm thu)

---

## 1. Phân Tầng Test

| Tầng           | Mô tả                                 | Công cụ         | Tần suất       |
|:---------------|:---------------------------------------|:-----------------|:----------------|
| Unit Tests     | Test từng function riêng lẻ            | pytest           | Mỗi commit      |
| Integration    | Test luồng giữa 2+ modules            | pytest + fixtures| Mỗi PR          |
| E2E            | Test toàn bộ pipeline end-to-end       | run_test_flow.py | Mỗi release     |
| Benchmark      | So sánh AI vs teacher trên dataset thật | custom script   | Mỗi milestone   |
| Manual/UX      | Teacher thử nghiệm thực tế             | Người dùng      | Trước production |

---

## 2. Unit Test Cases

### 2.1. Preprocessor (`services/preprocessor.py`)

| Test ID | Mô tả                                    | Input                     | Expected Output              |
|:--------|:------------------------------------------|:--------------------------|:-----------------------------|
| UT-01   | EXIF auto-rotate ảnh xoay 90°            | Ảnh JPEG rotation=6       | Ảnh đứng, orientation=1       |
| UT-02   | Resize ảnh > 4096px                       | Ảnh 6000x4000             | Ảnh 4096x2731 (giữ ratio)    |
| UT-03   | Resize ảnh < 4096px → không đổi           | Ảnh 1920x1080             | Ảnh 1920x1080 (unchanged)    |
| UT-04   | Blur detection → blurry                   | Ảnh Gaussian blur σ=10    | is_blurry=True, variance<100 |
| UT-05   | Blur detection → sharp                    | Ảnh rõ nét                | is_blurry=False, variance>100|
| UT-06   | Contrast enhancement không crash          | Ảnh grayscale 100x100     | Ảnh enhanced, no exception    |
| UT-07   | File format invalid → error               | File .gif                 | PreprocessorError             |

### 2.2. Cache (`core/cache.py`)

| Test ID | Mô tả                                    | Input                     | Expected Output              |
|:--------|:------------------------------------------|:--------------------------|:-----------------------------|
| UT-08   | Cache set + get                           | key="abc", value=OCRResult| get("abc") == value          |
| UT-09   | Cache miss                                | key="xyz" (not set)       | get("xyz") == None           |
| UT-10   | Cache TTL expiry                          | TTL=1s, wait 2s           | get() == None                |
| UT-11   | Cache clear                               | 3 items cached            | len == 0 after clear         |
| UT-12   | MD5 hash consistency                      | Same bytes twice          | Same hash                    |
| UT-13   | MD5 hash different for different images   | Different bytes           | Different hashes             |

### 2.3. Schemas (`models/schemas.py`)

| Test ID | Mô tả                                    | Input                     | Expected Output              |
|:--------|:------------------------------------------|:--------------------------|:-----------------------------|
| UT-14   | ExamRubric total_points validation        | sum ≠ total               | ValidationError              |
| UT-15   | ExamRubric valid                          | sum == total              | No error                     |
| UT-16   | CriterionEvaluation earned > max          | earned=5, max=3           | ValidationError              |
| UT-17   | Confidence out of range                   | confidence=1.5            | ValidationError              |
| UT-18   | GradingResult status enum                 | status="invalid"          | ValidationError              |

### 2.4. Agent 1 OCR (`services/agent1_ocr.py`)

| Test ID | Mô tả                                    | Input                     | Expected Output              |
|:--------|:------------------------------------------|:--------------------------|:-----------------------------|
| UT-19   | Mock OCR returns valid OCRResult          | Test image                | Valid OCRResult              |
| UT-20   | OCR low confidence → OCRLowConfidenceError| Blank image               | Error raised                 |
| UT-21   | Cache hit → no API call                   | Previously cached image   | cached=True, 0 API calls     |
| UT-22   | Gemini API error → retry                  | Mock 503 then 200         | Success after retry          |

### 2.5. Agent 2 Grader (`services/agent2_grader.py`)

| Test ID | Mô tả                                    | Input                     | Expected Output              |
|:--------|:------------------------------------------|:--------------------------|:-----------------------------|
| UT-23   | Perfect answer → full marks              | OCR với ĐKXĐ+biến đổi+KL | earned == total              |
| UT-24   | Missing ĐKXĐ → partial marks             | OCR thiếu condition       | dkxd earned == 0             |
| UT-25   | Error Propagation detected                | Sai step 2, đúng method 3 | EP=True, partial credit      |
| UT-26   | EP disabled → no EP detection             | allow_EP=False            | EP=False, normal scoring     |
| UT-27   | All wrong → 0 marks                       | Blank/gibberish OCR       | earned == 0, confidence red  |
| UT-28   | Confidence green threshold                | High match quality        | confidence_level=="green"    |
| UT-29   | Confidence red threshold                  | Low match quality         | confidence_level=="red"      |

---

## 3. Integration Test Cases

| Test ID | Mô tả                                           | Luồng                              | Expected                        |
|:--------|:-------------------------------------------------|:------------------------------------|:--------------------------------|
| IT-01   | Pipeline E2E: ảnh → OCR → grade                 | preprocessor→agent1→agent2          | Valid GradingResult             |
| IT-02   | Pipeline với cache hit                            | Grade → grade same image           | 2nd call cached=True            |
| IT-03   | API /submissions/grade                            | POST multipart → response          | 200 + GradingResult JSON        |
| IT-04   | API /submissions/review approve                   | POST review approve                | status="approved"               |
| IT-05   | API /submissions/review override                  | POST with overrides                | Updated points + status         |
| IT-06   | API /exams CRUD                                   | Create→Read→Update→Delete          | All 2xx                         |
| IT-07   | Batch upload ZIP                                  | POST ZIP 5 images                  | job created, 5 results          |
| IT-08   | Excel export                                      | GET /exports/excel/{id}            | Valid .xlsx file                 |

---

## 4. E2E Test Script (`run_test_flow.py`)

Kịch bản hiện tại (đã pass):

```
Step 1: Load rubric (ExamRubric Toán HK1 lớp 9)
Step 2: Create test image (synthetic handwriting)
Step 3: Pipeline run 1 → GradingResult (cached=False)
Step 4: Pipeline run 2 → GradingResult (cached=True, faster)
Step 5: Teacher review → approve → status="approved"
```

**Mở rộng cần thêm:**
```
Step 6: Batch upload 5 ảnh → 5 GradingResults
Step 7: Export Excel → valid .xlsx
Step 8: Upload ảnh blur → warning returned
Step 9: Upload file invalid → 400 error
Step 10: Error Propagation case → EP detected
```

---

## 5. Benchmark Dataset

### 5.1. Thu Thập

| # | Nguồn               | Số lượng | Loại                                       |
|---|:---------------------|:---------|:--------------------------------------------|
| 1 | Bài mẫu tự tạo      | 10 bài   | Viết tay rõ, font đều                        |
| 2 | Bài thật teacher     | 10 bài   | Scan/chụp bài thật từ teacher tình nguyện     |
| 3 | Edge cases           | 10 bài   | Mờ, nghiêng, nhiều gạch xóa, 2 cột           |
| **Tổng**              | **30 bài** |                                             |

### 5.2. Benchmark Metrics

| Metric                  | Target MVP | Đo bằng                                                |
|:------------------------|:-----------|:--------------------------------------------------------|
| **OCR Accuracy**        | ≥ 80%      | % dòng OCR khớp với ground truth (manual annotation)     |
| **Grading Accuracy**    | ≥ 85%      | % bài có total_earned ± 0.5 so với teacher chấm tay      |
| **EP Detection**        | ≥ 90%      | % Error Propagation cases detected correctly              |
| **API Latency (p50)**   | ≤ 5s       | Thời gian từ upload đến kết quả                           |
| **API Latency (p95)**   | ≤ 15s      | Thời gian worst case                                      |
| **Cost per paper**      | ≤ 120đ VND | Gemini API cost tracking                                  |
| **Cache Hit Rate**      | ≥ 50%      | Khi chấm lại cùng batch                                   |

### 5.3. Benchmark Script

```python
# benchmark.py (tạo mới)
def run_benchmark(dataset_dir: str, rubric: ExamRubric) -> BenchmarkReport:
    """
    1. Load ground truth (teacher scores) from dataset_dir/ground_truth.json
    2. Run pipeline on each image
    3. Compare AI scores vs teacher scores
    4. Compute metrics: accuracy, EP detection, latency, cost
    5. Generate report
    """
```

---

## 6. Exit Gates

### 6.1. Phase 1.1 (OCR Pipeline)

| Gate | Điều kiện                                        | ✅ local | ⬜ staging |
|:-----|:-------------------------------------------------|:--------:|:---------:|
| G-01 | UT-01 → UT-07 all pass                          | ✅       | ⬜        |
| G-02 | UT-19 → UT-22 all pass                          | ✅       | ⬜        |
| G-03 | OCR accuracy ≥ 80% trên 10-20 ảnh mẫu            | ✅       | ⬜        |
| G-04 | Cache hit/miss hoạt động đúng                     | ✅       | ⬜        |

### 6.2. Phase 1.2 (Grading Engine)

| Gate | Điều kiện                                        | ✅ local | ⬜ staging |
|:-----|:-------------------------------------------------|:--------:|:---------:|
| G-05 | UT-23 → UT-29 all pass                          | ✅       | ⬜        |
| G-06 | Grading accuracy ≥ 85% trên 20 bài benchmark     | ✅       | ⬜        |
| G-07 | Error Propagation detected ≥ 90%                  | ✅       | ⬜        |
| G-08 | Confidence thresholds hoạt động đúng              | ✅       | ⬜        |

### 6.3. Phase 1.3 (Dashboard)

| Gate | Điều kiện                                        | ✅ local | ⬜ staging |
|:-----|:-------------------------------------------------|:--------:|:---------:|
| G-09 | Upload ảnh → xem kết quả Split-Screen             | ✅       | ⬜        |
| G-10 | Approve/Override flow hoạt động                    | ✅       | ⬜        |
| G-11 | Keyboard shortcuts hoạt động                       | ✅       | ⬜        |
| G-12 | Dark/Light mode hoạt động                          | ✅       | ⬜        |

### 6.4. Phase 1.4 (Batch & Export)

| Gate | Điều kiện                                        | ✅ local | ⬜ staging |
|:-----|:-------------------------------------------------|:--------:|:---------:|
| G-13 | Batch upload ZIP 10 ảnh → 10 kết quả              | ✅       | ⬜        |
| G-14 | PDF split + grade hoạt động                        | ✅       | ⬜        |
| G-15 | Excel export standard format valid                 | ✅       | ⬜        |
| G-16 | Excel export VnEdu format valid                    | ✅       | ⬜        |

### 6.5. MVP Final Gate

| Gate | Điều kiện                                        | ✅ local | ⬜ staging | ⬜ prod |
|:-----|:-------------------------------------------------|:--------:|:---------:|:------:|
| G-17 | ALL G-01 → G-16 pass                             | ✅       | ⬜        | ⬜     |
| G-18 | `run_test_flow.py` exit 0 (10 steps)              | ✅       | ⬜        | ⬜     |
| G-19 | Benchmark 30 bài: accuracy ≥ 85%                   | ✅       | ⬜        | —      |
| G-20 | 3 teacher test thực tế, feedback positive           | —        | —         | ⬜     |
| G-21 | Cost per paper ≤ 120đ VND (measured)                | ✅       | ⬜        | ⬜     |
| G-22 | No critical/high severity bugs open                 | ✅       | ⬜        | ⬜     |

---

## 7. Quy Tắc Nghiệm Thu

**BẮT BUỘC:**
- Kế hoạch CHỈ được đánh dấu "✅ ĐÃ HOÀN THÀNH" khi tất cả Exit Gates của môi trường tương ứng chuyển ✅.
- MVP được ship khi G-17 → G-22 đều ✅ ở ít nhất `local`.
- Production deploy yêu cầu G-20 (teacher test) ✅.
- Benchmark dataset PHẢI có ground truth annotation từ teacher thật.

**CẤM:**
- CẤM đánh ✅ Exit Gate mà không có evidence (log output, screenshot, benchmark report).
- CẤM ship production khi có bug severity `critical` hoặc `high` chưa fix.
- CẤM thay đổi benchmark target sau khi bắt đầu (phải tạo Gate mới, không sửa target cũ).
