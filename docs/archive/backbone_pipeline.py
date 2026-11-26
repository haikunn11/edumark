from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


# =====================================================================
# 1. TRẠNG THÁI VÒNG ĐỜI & CẤU TRÚC DỮ LIỆU CỐT LÕI (DATA CONTRACTS)
# =====================================================================

class SubmissionStatus(str, Enum):
    UPLOADED = "UPLOADED"
    REJECTED_BLUR = "REJECTED_BLUR"
    OCR_COMPLETED = "OCR_COMPLETED"
    GRADED_DRAFT = "GRADED_DRAFT"
    FLAGGED_REVIEW = "FLAGGED_REVIEW"
    APPROVED = "APPROVED"


@dataclass
class RubricCriterion:
    criterion_id: str
    name: str
    max_score: float
    required_logic: str
    depends_on: list[str] = field(default_factory=list)


@dataclass
class QuestionRubric:
    question_id: str
    max_score: float
    allow_alternative_solutions: bool
    criteria: list[RubricCriterion]


@dataclass
class ImageQualityReport:
    is_blurry: bool
    blur_score: float
    applied_transforms: list[str] = field(default_factory=list)


@dataclass
class ExtractedLine:
    line_no: int
    raw_latex: str
    unclear: bool = False


@dataclass
class ExtractedQuestion:
    question_id: str
    confidence: float
    has_unclear: bool
    syntax_valid: bool
    lines: list[ExtractedLine]


@dataclass
class OcrResult:
    submission_id: str
    image_hash: str
    cache_hit: bool
    quality: ImageQualityReport
    questions: list[ExtractedQuestion]


@dataclass
class CriterionEvaluation:
    criterion_id: str
    score: float
    max_score: float
    status: str  # "passed" | "partial" | "failed"
    error_type: str | None  # None | "essential_error" | "follow_through_error" | "missing_step"
    feedback: str


@dataclass
class QuestionGradingResult:
    question_id: str
    suggested_score: float
    max_score: float
    self_critique_applied: bool
    needs_review: bool
    review_reasons: list[str]
    criteria_evaluation: list[CriterionEvaluation]
    teacher_summary_comment: str


@dataclass
class SubmissionGradingRecord:
    submission_id: str
    exam_id: str
    rubric_version: int
    status: SubmissionStatus
    ocr_result: OcrResult | None = None
    question_results: list[QuestionGradingResult] = field(default_factory=list)
    ai_total_score: float = 0.0
    final_score: float | None = None
    needs_teacher_review: bool = False


# =====================================================================
# 2. INTERFACE CHO CACHE, STORAGE, AGENT 1 & AGENT 2
# =====================================================================

class CacheStore(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl_seconds: int = 3600) -> None: ...


class ObjectStorage(Protocol):
    def save_bytes(self, key: str, data: bytes) -> str: ...
    def load_bytes(self, key: str) -> bytes: ...


class VisionOcrAgent(Protocol):
    def extract_latex(self, image_bytes: bytes, applied_transforms: list[str]) -> list[ExtractedQuestion]: ...


class ReasoningGradingAgent(Protocol):
    def grade_question(
        self,
        question: ExtractedQuestion,
        rubric: QuestionRubric,
        run_self_critique: bool,
    ) -> QuestionGradingResult: ...


# =====================================================================
# 3. CÁC HÀM BỔ TRỢ: HASH, VALIDATE LATEX & ĐIỀU KIỆN SELF-CRITIQUE
# =====================================================================

def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_latex_syntax(latex_text: str) -> bool:
    """Kiểm tra cơ bản độ cân bằng ngoặc {} [] và tránh chuỗi trống/gãy."""
    if not latex_text or not latex_text.strip():
        return False
    stack: list[str] = []
    pairs = {"{": "}", "[": "]"}
    for ch in latex_text:
        if ch in pairs:
            stack.append(pairs[ch])
        elif ch in pairs.values():
            if not stack or stack.pop() != ch:
                return False
    # Kiểm tra các lệnh phổ biến không bị cắt cụt đuôi
    if re.search(r"\\(frac|sqrt|text)\s*$", latex_text):
        return False
    return len(stack) == 0


def should_trigger_self_critique(
    question: ExtractedQuestion,
    rubric: QuestionRubric,
    initial_result: QuestionGradingResult,
) -> bool:
    """Chỉ bật vòng tự phản biện có chọn lọc để tối ưu chi phí & độ trễ."""
    has_follow_through = any(
        c.error_type == "follow_through_error" for c in initial_result.criteria_evaluation
    )
    high_value_question = rubric.max_score >= 2.0 or len(rubric.criteria) >= 3
    low_ocr_confidence = question.confidence < 0.85 or question.has_unclear
    near_boundary_score = 0 < initial_result.suggested_score < rubric.max_score

    return bool(
        has_follow_through
        or low_ocr_confidence
        or (high_value_question and near_boundary_score)
    )


# =====================================================================
# 4. PIPELINE ĐIỀU PHỐI TRUNG TÂM (BACKBONE ORCHESTRATOR)
# =====================================================================

class MathGradingPipeline:
    PREPROC_VERSION = "p1"

    def __init__(
        self,
        cache: CacheStore,
        storage: ObjectStorage,
        ocr_agent: VisionOcrAgent,
        grading_agent: ReasoningGradingAgent,
        blur_threshold: float = 100.0,
        ocr_confidence_threshold: float = 0.85,
    ) -> None:
        self.cache = cache
        self.storage = storage
        self.ocr_agent = ocr_agent
        self.grading_agent = grading_agent
        self.blur_threshold = blur_threshold
        self.ocr_confidence_threshold = ocr_confidence_threshold

    def _get_rubric_cached(
        self, exam_id: str, rubric_version: int, rubrics_by_question: dict[str, QuestionRubric]
    ) -> dict[str, QuestionRubric]:
        cache_key = f"cache:rubric:{exam_id}:v{rubric_version}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        # Thực tế sẽ truy vấn PostgreSQL tại đây nếu cache miss
        self.cache.set(cache_key, rubrics_by_question, ttl_seconds=86400)
        return rubrics_by_question

    def _assess_and_preprocess_image(self, image_bytes: bytes) -> ImageQualityReport:
        """
        Stage 1: Blur gate + Conditional preprocessing.
        Thực tế dùng OpenCV: tính Laplacian variance; chỉ deskew / chuẩn hóa sáng khi vượt ngưỡng.
        """
        # Giả lập chỉ số độ nét (Laplacian variance)
        blur_score = self._estimate_blur_score(image_bytes)
        if blur_score < self.blur_threshold:
            return ImageQualityReport(is_blurry=True, blur_score=blur_score)

        transforms: list[str] = []
        if self._needs_deskew(image_bytes):
            transforms.append("conditional_deskew")
        if self._needs_illumination_norm(image_bytes):
            transforms.append("conditional_illumination_norm")

        return ImageQualityReport(
            is_blurry=False,
            blur_score=blur_score,
            applied_transforms=transforms,
        )

    def _estimate_blur_score(self, image_bytes: bytes) -> float:
        # Placeholder: thay bằng cv2.Laplacian(gray, cv2.CV_64F).var()
        return 240.0 if len(image_bytes) > 0 else 0.0

    def _needs_deskew(self, image_bytes: bytes) -> bool:
        return False

    def _needs_illumination_norm(self, image_bytes: bytes) -> bool:
        return False

    def run(
        self,
        submission_id: str,
        exam_id: str,
        rubric_version: int,
        image_bytes: bytes,
        rubrics_by_question: dict[str, QuestionRubric],
    ) -> SubmissionGradingRecord:
        record = SubmissionGradingRecord(
            submission_id=submission_id,
            exam_id=exam_id,
            rubric_version=rubric_version,
            status=SubmissionStatus.UPLOADED,
        )
        self.cache.set(f"cache:progress:{submission_id}", {"stage": "started", "pct": 10}, ttl_seconds=3600)

        # --- BƯỚC 1: Lưu ảnh gốc & kiểm tra chất lượng sớm (Blur Gate) ---
        image_hash = compute_sha256(image_bytes)
        self.storage.save_bytes(f"submissions/{submission_id}/raw.jpg", image_bytes)

        quality = self._assess_and_preprocess_image(image_bytes)
        if quality.is_blurry:
            record.status = SubmissionStatus.REJECTED_BLUR
            record.needs_teacher_review = True
            self.cache.set(f"cache:progress:{submission_id}", {"stage": "rejected_blur", "pct": 100}, ttl_seconds=3600)
            return record

        self.cache.set(f"cache:progress:{submission_id}", {"stage": "ocr", "pct": 35}, ttl_seconds=3600)

        # --- BƯỚC 2: Agent 1 (OCR & Layout Parser) với Cache theo image_hash ---
        ocr_cache_key = f"cache:ocr:{image_hash}:{self.PREPROC_VERSION}"
        cached_questions: list[ExtractedQuestion] | None = self.cache.get(ocr_cache_key)
        cache_hit = cached_questions is not None

        if cached_questions is None:
            raw_questions = self.ocr_agent.extract_latex(image_bytes, quality.applied_transforms)
            validated_questions: list[ExtractedQuestion] = []
            for q in raw_questions:
                all_lines_valid = all(validate_latex_syntax(line.raw_latex) for line in q.lines)
                has_unclear = q.has_unclear or any(line.unclear for line in q.lines)
                validated_questions.append(
                    ExtractedQuestion(
                        question_id=q.question_id,
                        confidence=q.confidence,
                        has_unclear=has_unclear,
                        syntax_valid=all_lines_valid,
                        lines=q.lines,
                    )
                )
            cached_questions = validated_questions
            self.cache.set(ocr_cache_key, cached_questions, ttl_seconds=7 * 86400)

        record.ocr_result = OcrResult(
            submission_id=submission_id,
            image_hash=image_hash,
            cache_hit=cache_hit,
            quality=quality,
            questions=cached_questions,
        )
        record.status = SubmissionStatus.OCR_COMPLETED
        self.cache.set(f"cache:progress:{submission_id}", {"stage": "grading", "pct": 70}, ttl_seconds=3600)

        # --- BƯỚC 3: Lấy Rubric từ Cache & Chạy Agent 2 (Step-by-Step Grading) ---
        rubrics = self._get_rubric_cached(exam_id, rubric_version, rubrics_by_question)
        total_score = 0.0
        any_review_needed = False

        for q in cached_questions:
            rubric = rubrics.get(q.question_id)
            if not rubric:
                continue

            # Chấm nháp lần đầu
            q_result = self.grading_agent.grade_question(q, rubric, run_self_critique=False)

            # Kích hoạt self-critique có chọn lọc nếu thỏa điều kiện
            if should_trigger_self_critique(q, rubric, q_result):
                q_result = self.grading_agent.grade_question(q, rubric, run_self_critique=True)
                q_result.self_critique_applied = True

            # Tổng hợp cờ cần giáo viên kiểm duyệt (HITL)
            reasons = list(q_result.review_reasons)
            if q.has_unclear:
                reasons.append("ocr_unclear_flag")
            if not q.syntax_valid:
                reasons.append("latex_syntax_warning")
            if q.confidence < self.ocr_confidence_threshold:
                reasons.append("low_ocr_confidence")

            q_result.review_reasons = sorted(set(reasons))
            q_result.needs_review = q_result.needs_review or len(q_result.review_reasons) > 0

            if q_result.needs_review:
                any_review_needed = True

            total_score += q_result.suggested_score
            record.question_results.append(q_result)

        # --- BƯỚC 4: Chốt trạng thái nháp / chờ duyệt (HITL) ---
        record.ai_total_score = round(total_score, 2)
        record.needs_teacher_review = any_review_needed
        record.status = (
            SubmissionStatus.FLAGGED_REVIEW if any_review_needed else SubmissionStatus.GRADED_DRAFT
        )
        self.cache.set(f"cache:progress:{submission_id}", {"stage": "completed", "pct": 100}, ttl_seconds=3600)
        return record

# =====================================================================
# 5. BỘ GIẢ LẬP (MOCK ADAPTERS) ĐỂ CHẠY TEST FLOW LOCAL
# =====================================================================

class InMemoryCache:
    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self._store.get(key)

    def set(self, key: str, value: Any, ttl_seconds: int = 3600) -> None:
        self._store[key] = value


class LocalObjectStorage:
    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}

    def save_bytes(self, key: str, data: bytes) -> str:
        self._files[key] = data
        return f"memory://{key}"

    def load_bytes(self, key: str) -> bytes:
        return self._files[key]


class MockVisionOcrAgent:
    """Giả lập Agent 1: trích xuất LaTeX theo từng dòng của câu hỏi."""

    def extract_latex(self, image_bytes: bytes, applied_transforms: list[str]) -> list[ExtractedQuestion]:
        # Trường hợp test có dòng thiếu ĐKXĐ / có nét hơi mờ ở bước kết luận
        return [
            ExtractedQuestion(
                question_id="Q1",
                confidence=0.88,
                has_unclear=False,
                syntax_valid=True,
                lines=[
                    ExtractedLine(line_no=1, raw_latex="\\frac{x}{x-2} + 1 = \\frac{4}{x-2}", unclear=False),
                    ExtractedLine(line_no=2, raw_latex="x + (x - 2) = 4", unclear=False),
                    ExtractedLine(line_no=3, raw_latex="2x = 6 \\Rightarrow x = 3", unclear=False),
                    ExtractedLine(line_no=4, raw_latex="\\text{Vậy nghiệm } x = 3", unclear=False),
                ],
            )
        ]


class MockReasoningGradingAgent:
    """Giả lập Agent 2: chấm theo tiêu chí Rubric & hỗ trợ self-critique."""

    def grade_question(
        self,
        question: ExtractedQuestion,
        rubric: QuestionRubric,
        run_self_critique: bool,
    ) -> QuestionGradingResult:
        # Giả lập học sinh quên nêu ĐKXĐ (0đ/0.5đ), làm đúng biến đổi (1.0đ/1.0đ),
        # kết luận x = 3 nhưng chưa đối chiếu điều kiện (0.25đ/0.5đ hoặc 0đ)
        c1 = CriterionEvaluation(
            criterion_id="Q1_C1",
            score=0.0,
            max_score=0.5,
            status="failed",
            error_type="missing_step",
            feedback="Thiếu bước đặt điều kiện xác định (x != 2).",
        )
        c2 = CriterionEvaluation(
            criterion_id="Q1_C2",
            score=1.0,
            max_score=1.0,
            status="passed",
            error_type=None,
            feedback="Quy đồng và giải phương trình đúng (x = 3).",
        )
        c3_score = 0.0 if run_self_critique else 0.25
        c3 = CriterionEvaluation(
            criterion_id="Q1_C3",
            score=c3_score,
            max_score=0.5,
            status="failed" if c3_score == 0.0 else "partial",
            error_type="missing_step",
            feedback="Chưa đối chiếu nghiệm x = 3 với điều kiện xác định trước khi kết luận.",
        )

        evals = [c1, c2, c3]
        total = round(sum(item.score for item in evals), 2)
        comment = (
            "Đã rà soát lại tiêu chí: trừ trọn điểm bước kết luận do thiếu đối chiếu ĐKXĐ."
            if run_self_critique
            else "Biến đổi đúng, cần lưu ý ĐKXĐ và đối chiếu nghiệm."
        )

        return QuestionGradingResult(
            question_id=question.question_id,
            suggested_score=total,
            max_score=rubric.max_score,
            self_critique_applied=run_self_critique,
            needs_review=False,
            review_reasons=[],
            criteria_evaluation=evals,
            teacher_summary_comment=comment,
        )


# =====================================================================
# 6. KỊCH BẢN CHẠY THỬ END-TO-END FLOW
# =====================================================================

if __name__ == "__main__":
    cache = InMemoryCache()
    storage = LocalObjectStorage()
    pipeline = MathGradingPipeline(
        cache=cache,
        storage=storage,
        ocr_agent=MockVisionOcrAgent(),
        grading_agent=MockReasoningGradingAgent(),
    )

    rubric_q1 = QuestionRubric(
        question_id="Q1",
        max_score=2.0,
        allow_alternative_solutions=True,
        criteria=[
            RubricCriterion("Q1_C1", "Điều kiện xác định", 0.5, "Đặt đúng ĐKXĐ"),
            RubricCriterion("Q1_C2", "Biến đổi & giải", 1.0, "Biến đổi đúng và tìm ẩn", depends_on=["Q1_C1"]),
            RubricCriterion("Q1_C3", "Đối chiếu & kết luận", 0.5, "So sánh ĐKXĐ và kết luận", depends_on=["Q1_C1", "Q1_C2"]),
        ],
    )
    rubrics = {"Q1": rubric_q1}

    sample_image = b"fake-jpeg-bytes-for-math-submission-001"

    # Chạy lần 1: cache miss -> chạy full pipeline
    res_1 = pipeline.run(
        submission_id="sub_001",
        exam_id="exam_toan8_c1",
        rubric_version=1,
        image_bytes=sample_image,
        rubrics_by_question=rubrics,
    )
    print("=== LẦN CHẠY 1 (EXPECT cache_hit = False) ===")
    print("Status:", res_1.status)
    print("OCR cache_hit:", res_1.ocr_result.cache_hit if res_1.ocr_result else None)
    print("AI Total Score:", res_1.ai_total_score)
    print("Needs Teacher Review:", res_1.needs_teacher_review)
    for qr in res_1.question_results:
        print(f"  - {qr.question_id}: {qr.suggested_score}/{qr.max_score} | self_critique={qr.self_critique_applied} | reasons={qr.review_reasons}")
        for ce in qr.criteria_evaluation:
            print(f"      [{ce.criterion_id}] {ce.score}/{ce.max_score} ({ce.status}): {ce.feedback}")

    # Chạy lần 2 với cùng ảnh: expect ocr cache_hit = True
    res_2 = pipeline.run(
        submission_id="sub_002",
        exam_id="exam_toan8_c1",
        rubric_version=1,
        image_bytes=sample_image,
        rubrics_by_question=rubrics,
    )
    print("\n=== LẦN CHẠY 2 CÙNG ẢNH (EXPECT ocr cache_hit = True) ===")
    print("Status:", res_2.status)
    print("OCR cache_hit:", res_2.ocr_result.cache_hit if res_2.ocr_result else None)