# SPEC-P02 — Agent 2: Step-by-Step Rubric Grading Engine

> **Phạm vi:** Nhận `OCRResult` + `ExamRubric` → trả `GradingResult` với điểm từng tiêu chí.
> **Tier:** 🟠 (Standard — logic tính năng chính) | Mục Error Propagation: 🔴
> **Modules:** `services/agent2_grader.py`

---

## 1. Luồng Chấm Điểm

```
OCRResult + ExamRubric
    │
    ▼
[Question Mapper]
    ├── Map OCRResult.questions → ExamRubric.questions theo question_id
    └── Unmatched questions → warning "câu không có trong barem"
    │
    ▼
[Step Matcher] (cho từng question)
    ├── Với mỗi RubricCriterion:
    │   ├── Search lines cho keywords match (regex)
    │   ├── Classify: matched / partial / missing
    │   └── Assign points
    │
    ▼
[Error Propagation Detector]
    ├── Phát hiện: sai tính toán ở step N, phương pháp đúng ở step N+1
    ├── Flag: error_propagation_detected = True
    └── Adjust: giữ điểm phương pháp, trừ điểm tính toán
    │
    ▼
[Confidence Scorer]
    ├── Tính confidence dựa trên: OCR confidence × match quality
    └── Classify: green / yellow / red
    │
    ▼
[Output Builder]
    └── Assemble GradingResult
```

---

## 2. Question Mapper

```python
def map_questions(
    ocr: OCRResult,
    rubric: ExamRubric
) -> dict[str, tuple[ExtractedQuestion, QuestionRubric]]:
    """
    Map OCR questions to rubric questions by question_id.
    Returns: { question_id: (extracted, rubric_question) }
    Unmapped OCR questions → log warning.
    Missing rubric questions (no OCR match) → score 0 with feedback "Không tìm thấy bài làm".
    """
```

**BẮT BUỘC:**
- Matching case-insensitive trên question_id.
- Nếu OCR có question nhưng rubric không có → bỏ qua, log warning.
- Nếu rubric có question nhưng OCR không có → earned_points = 0, feedback = "Không tìm thấy bài làm cho câu này".

---

## 3. Step Matcher — Thuật Toán Chấm Từng Bước

### 3.1. Matching Strategy

Với mỗi `RubricCriterion`, tìm trong `ExtractedQuestion.lines`:

```python
def match_criterion(
    criterion: RubricCriterion,
    lines: list[ExtractedLine]
) -> CriterionEvaluation:
    """
    1. Keyword matching: regex search criterion.keywords trong line.raw_text + line.latex
    2. Line type matching: criterion "dkxd" → tìm line_type == "condition"
    3. Scoring:
       - Full match (keyword + content correct) → max_points
       - Partial match (keyword found, content unclear) → max_points * 0.5
       - No match → 0
    4. Error pattern detection: check criterion.error_patterns
    """
```

### 3.2. Keyword Mapping Mặc Định (Toán THCS)

| criterion_id      | Keywords (regex)                                           | line_type expected |
|:-------------------|:----------------------------------------------------------|:-------------------|
| `dkxd`            | `ĐKXĐ\|điều kiện\|đk\|xác định\|mẫu.*khác.*0`           | `condition`        |
| `bien_doi`        | `biến đổi\|rút gọn\|⇔\|=>\|chuyển vế\|nhân\|chia`      | `equation`         |
| `giai_pt`         | `giải\|nghiệm\|x\s*=\|⇒`                                | `equation`         |
| `ket_luan`        | `kết luận\|vậy\|đáp số\|KL\|kết quả\|S\s*=`             | `conclusion`       |
| `doi_chieu`       | `đối chiếu\|thỏa\|thay.*vào\|kiểm tra\|loại`            | `text`             |

### 3.3. Partial Credit Rules

```python
PARTIAL_CREDIT_RULES = {
    "method_correct_calc_wrong": 0.5,     # Phương pháp đúng, tính sai → 50%
    "incomplete_step": 0.3,                # Có ý tưởng nhưng chưa xong → 30%
    "correct_but_no_explanation": 0.7,     # Đáp số đúng nhưng thiếu giải thích → 70%
}
```

**BẮT BUỘC:**
- Partial credit PHẢI dựa trên `partial_credit_rules` trong RubricCriterion (nếu có), fallback sang defaults.
- Mọi `earned_points` PHẢI ≤ `max_points`.

---

## 4. Error Propagation Detection

### 4.1. Định Nghĩa

> **Error Propagation** xảy ra khi học sinh mắc lỗi tính toán ở bước N, nhưng áp dụng **đúng phương pháp** ở bước N+1 trên kết quả sai đó.

### 4.2. Thuật Toán

```python
def detect_error_propagation(
    evaluations: list[CriterionEvaluation],
    lines: list[ExtractedLine]
) -> tuple[bool, list[str]]:
    """
    1. Tìm step đầu tiên bị lỗi calculation (error_type == "calculation")
    2. Kiểm tra các step SAU:
       - Nếu step sau dùng đúng method nhưng trên giá trị sai → error_propagation
       - affected_steps = [criterion_ids của các step bị ảnh hưởng]
    3. Điều chỉnh điểm:
       - Step lỗi gốc: trừ điểm tính toán (earned = max * 0.5)
       - Steps bị propagation: giữ điểm phương pháp (earned = max * partial_credit)
    
    Returns: (detected: bool, affected_step_ids: list[str])
    """
```

**BẮT BUỘC:**
- Error Propagation CHỈ áp dụng khi `QuestionRubric.allow_error_propagation == True`.
- PHẢI ghi rõ trong feedback: "Lỗi lan truyền từ bước [X]: phương pháp đúng, tính toán sai do nhận giá trị sai từ bước trước."
- affected_steps PHẢI liệt kê đầy đủ.

**CẤM:**
- CẤM cho 0 điểm toàn bộ khi có Error Propagation — PHẢI giữ điểm phương pháp.
- CẤM detect Error Propagation khi `allow_error_propagation == False`.

---

## 5. Confidence Scoring

```python
def compute_confidence(
    ocr_confidence: float,
    match_quality: float,  # 0-1, tỷ lệ criteria matched / total criteria
    has_ambiguity: bool    # True nếu có partial matches
) -> tuple[float, Literal["green", "yellow", "red"]]:
    """
    confidence = ocr_confidence * 0.4 + match_quality * 0.5 + (0.1 if not has_ambiguity else 0)
    
    Thresholds:
      >= 0.9 → "green"
      >= 0.7 → "yellow"  
      <  0.7 → "red"
    """
```

**BẮT BUỘC:**
- Confidence PHẢI phản ánh cả OCR quality lẫn grading certainty.
- `"red"` submissions PHẢI hiện cảnh báo nổi bật trên Dashboard.

---

## 6. Common Error Patterns (Toán THCS Hardcoded)

| Pattern ID          | Mô tả                                           | Detection Rule                                |
|:--------------------|:-------------------------------------------------|:----------------------------------------------|
| `missing_dkxd`      | Thiếu ĐKXĐ trong bài phương trình chứa ẩn ở mẫu | Rubric có criterion `dkxd` nhưng không match   |
| `sign_error`        | Sai dấu khi khai triển ngoặc                     | Regex: dấu khác giữa step N và expected       |
| `forgot_verify`     | Quên đối chiếu nghiệm với ĐKXĐ                   | Rubric có `doi_chieu` nhưng không match        |
| `wrong_conclusion`  | Kết luận sai (khác đáp án)                        | Conclusion line ≠ expected answer              |
| `incomplete_factor` | Phân tích nhân tử thiếu trường hợp               | Số factors < expected                          |

---

## 7. Bảng Lỗi Module

| Lỗi                        | Nguyên nhân                        | Hành vi bắt buộc                                 |
|:----------------------------|:-----------------------------------|:-------------------------------------------------|
| `RubricMismatchError`       | question_id không khớp giữa OCR và rubric | Log warning, chấm 0 cho câu thiếu, tiếp tục |
| `GradingLogicError`         | Bug trong step matching            | Log full context, trả confidence "red"            |
| `ErrorPropagationConflict`  | Conflict trong chain detection     | Fallback: không áp dụng EP, chấm thường          |
