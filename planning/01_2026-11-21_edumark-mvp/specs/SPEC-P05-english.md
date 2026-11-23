# SPEC-P05 — Module Tiếng Anh (Trắc Nghiệm + Writing CEFR)

> **Phạm vi:** Chấm bài Tiếng Anh: trắc nghiệm (OMR) + tự luận Writing (CEFR rubric).
> **Tier:** 🟠 (Standard) — **CHƯA TRIỂN KHAI Ở MVP (Tầng 2, Phase 2.1)**
> **Modules:** `services/agent_english_omr.py`, `services/agent_english_writing.py` (tạo mới)

---

## ⚠️ LƯU Ý: File này là thiết kế SƠ BỘ cho Tầng 2. Chỉ bắt đầu triển khai sau khi MVP Toán (Tầng 1) hoàn thành và nghiệm thu.

---

## 1. Trắc Nghiệm — OMR Bubble Detection

### 1.1. Input

- Ảnh phiếu trả lời trắc nghiệm (bubble sheet)
- Format: 4 lựa chọn A/B/C/D per câu, 20-50 câu/phiếu

### 1.2. Pipeline

```
Ảnh phiếu trắc nghiệm
    │
    ▼
[Preprocessor] (reuse SPEC-P01)
    │
    ▼
[OMR Detection]
    ├── Detect grid/bubbles (OpenCV contour detection)
    ├── Map bubble position → câu hỏi + lựa chọn
    └── Multi-mark detection (tô 2 đáp án)
    │
    ▼
[Answer Matching]
    ├── So sánh với đáp án đúng
    └── Tính điểm: đúng = +1, sai = 0, multi-mark = 0
    │
    ▼
[Output: OMRResult]
```

### 1.3. Schema Dự Kiến

```python
class OMRAnswer(BaseModel):
    question_number: int
    selected: list[Literal["A", "B", "C", "D"]]  # list vì có thể multi-mark
    correct_answer: str
    is_correct: bool
    is_multi_mark: bool = False   # True nếu tô > 1 đáp án

class OMRResult(BaseModel):
    submission_id: str
    total_questions: int
    correct_count: int
    score: float                  # correct_count / total_questions * max_points
    answers: list[OMRAnswer]
    confidence: float
```

**BẮT BUỘC:**
- Multi-mark (tô 2+ đáp án) → score 0 cho câu đó + flag.
- Confidence OMR dựa trên contrast ratio của bubble fill.

---

## 2. Writing — CEFR Rubric Grading

### 2.1. CEFR Levels Supported

| Level | Mô tả               | Dạng bài thường gặp              |
|:------|:---------------------|:----------------------------------|
| A1    | Beginner             | Fill-in, short answer              |
| A2    | Elementary           | Email, postcard (40-60 words)      |
| B1    | Intermediate         | Informal letter, story (100-150w)  |
| B2    | Upper-Intermediate   | Essay, formal letter (150-200w)    |

### 2.2. Rubric Criteria

```python
class WritingRubricCriterion(BaseModel):
    criterion_id: Literal[
        "task_achievement",    # Hoàn thành yêu cầu đề bài
        "coherence",           # Mạch lạc, liên kết
        "vocabulary",          # Từ vựng
        "grammar",             # Ngữ pháp
        "spelling_punctuation" # Chính tả, dấu câu
    ]
    max_points: float
    band_descriptors: dict[str, str]  # e.g. {"5": "Excellent...", "4": "Good..."}
```

### 2.3. Pipeline

```
Ảnh bài viết Tiếng Anh
    │
    ▼
[Agent 1 OCR] (reuse, prompt adjusted for English)
    │
    ▼
[Writing Grader Agent]
    ├── Gemini LLM: đánh giá theo 5 tiêu chí CEFR
    ├── Grammar checker: highlight lỗi ngữ pháp
    ├── Vocabulary assessment: range & accuracy
    └── Band score + feedback per criterion
    │
    ▼
[Output: WritingGradingResult]
```

**BẮT BUỘC:**
- Writing grading PHẢI dùng LLM (không rule-based) vì ngôn ngữ tự nhiên.
- Feedback PHẢI bằng Tiếng Việt (cho teacher Việt Nam).
- Highlight lỗi trực tiếp trong text (red underline).

---

## 3. Rubric Templates Mặc Định

### 3.1. Email (A2-B1)

| Tiêu chí          | Điểm | Mô tả band 5                              |
|:-------------------|:-----:|:-------------------------------------------|
| Task Achievement   | 2.0   | Trả lời đầy đủ 3/3 nội dung yêu cầu       |
| Coherence          | 2.0   | Mở-thân-kết rõ ràng, dùng linkers tốt      |
| Vocabulary         | 2.0   | Từ vựng phong phú, đúng ngữ cảnh           |
| Grammar            | 2.0   | Ít lỗi, dùng đa dạng cấu trúc             |
| Spelling           | 2.0   | Không lỗi chính tả, dấu câu đúng          |

### 3.2. Essay (B1-B2)

| Tiêu chí          | Điểm | Mô tả band 5                              |
|:-------------------|:-----:|:-------------------------------------------|
| Task Achievement   | 2.5   | Luận điểm rõ, ví dụ thuyết phục            |
| Coherence          | 2.5   | Đoạn văn logic, transition mượt            |
| Vocabulary         | 2.0   | Academic vocabulary, collocations           |
| Grammar            | 2.0   | Complex sentences, ít lỗi                  |
| Spelling           | 1.0   | Chính tả chuẩn                             |

---

## 4. Timeline Dự Kiến

| Tuần   | Deliverable                           |
|:-------|:--------------------------------------|
| T2.1   | OMR detection prototype               |
| T2.2   | OMR → answer matching + scoring        |
| T2.3   | Writing OCR + CEFR prompt engineering  |
| T2.4   | Writing grading + feedback generation  |
| T2.5   | Integration test + dashboard update    |
| T2.6   | Benchmark: 50 bài Anh vs teacher       |
