from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field


class SubmissionStatus(str, Enum):
    UPLOADED = "UPLOADED"
    PREPROCESSED = "PREPROCESSED"
    OCR_COMPLETED = "OCR_COMPLETED"
    GRADED_DRAFT = "GRADED_DRAFT"
    FLAGGED_REVIEW = "FLAGGED_REVIEW"
    APPROVED = "APPROVED"


class SubjectType(str, Enum):
    MATH_SECONDARY = "MATH_SECONDARY"  # Toán cấp 2 (THCS)
    MATH_HIGH = "MATH_HIGH"            # Toán cấp 3 (THPT)
    ENGLISH = "ENGLISH"                # Tiếng Anh


class RubricCriterion(BaseModel):
    criterion_id: str = Field(..., description="Mã tiêu chí, ví dụ: Q1_C1")
    name: str = Field(default="", description="Tên bước giải (ví dụ: Tìm ĐKXĐ)")
    description: str = Field(default="", description="Mô tả tiêu chí chi tiết")
    max_score: float = Field(default=0.0, ge=0.0)
    max_points: float = Field(default=0.0, ge=0.0)
    required_logic: str = Field(default="", description="Mô tả logic hoặc công thức chuẩn cần đạt")
    keywords: list[str] = Field(default_factory=list, description="Từ khóa hoặc regex nhận diện bước giải")
    error_patterns: list[str] = Field(default_factory=list, description="Mẫu lỗi thường gặp")
    partial_credit_rules: dict[str, float] = Field(default_factory=dict, description="Quy tắc tính điểm thành phần")
    depends_on: list[str] = Field(default_factory=list, description="Các bước tiên quyết")

    def model_post_init(self, __context):
        if not self.name and self.description:
            self.name = self.description
        elif not self.description and self.name:
            self.description = self.name
        if self.max_score and not self.max_points:
            self.max_points = self.max_score
        elif self.max_points and not self.max_score:
            self.max_score = self.max_points


class QuestionRubric(BaseModel):
    question_id: str = Field(..., description="Mã câu hỏi, ví dụ: Q1")
    question_text: str = Field(default="", description="Nội dung đề bài câu hỏi")
    max_score: float = Field(default=0.0, ge=0.0)
    total_points: float = Field(default=0.0, ge=0.0)
    allow_alternative_solutions: bool = True
    allow_error_propagation: bool = True
    criteria: list[RubricCriterion] = Field(default_factory=list)

    def model_post_init(self, __context):
        if self.max_score and not self.total_points:
            self.total_points = self.max_score
        elif self.total_points and not self.max_score:
            self.max_score = self.total_points
        if not self.total_points and self.criteria:
            self.total_points = round(sum(c.max_points for c in self.criteria), 2)
            self.max_score = self.total_points


class ExamRubric(BaseModel):
    exam_id: str
    title: str = ""
    exam_title: str = ""
    subject: SubjectType = SubjectType.MATH_SECONDARY
    grade: int = 9
    total_points: float = 0.0
    rubric_version: int = 1
    questions: list[QuestionRubric] = Field(default_factory=list)

    def model_post_init(self, __context):
        if not self.title and self.exam_title:
            self.title = self.exam_title
        elif not self.exam_title and self.title:
            self.exam_title = self.title
        if not self.total_points and self.questions:
            self.total_points = round(sum(q.total_points for q in self.questions), 2)


class ImageQualityReport(BaseModel):
    is_blurry: bool = False
    blur_score: float = 100.0
    brightness: float = 128.0
    is_too_dark: bool = False
    is_overexposed: bool = False
    perspective_corrected: bool = False
    shadow_removed: bool = False
    is_acceptable: bool = True
    warnings: list[str] = Field(default_factory=list)
    recommendation: str = ""
    applied_transforms: list[str] = Field(default_factory=list)


class LineType(str, Enum):
    EQUATION = "equation"
    TEXT = "text"
    CONDITION = "condition"
    CONCLUSION = "conclusion"
    SCRATCH = "scratch"
    UNKNOWN = "unknown"


class ExtractedLine(BaseModel):
    line_index: int = 0
    line_no: int = 1
    page_no: int = 1
    raw_text: str = ""
    latex: str = ""
    raw_latex: str = ""
    confidence: float = 1.0
    line_type: LineType = LineType.EQUATION
    unclear: bool = False
    is_strikethrough: bool = False

    def model_post_init(self, __context):
        if not self.latex and self.raw_latex:
            self.latex = self.raw_latex
        elif not self.raw_latex and self.latex:
            self.raw_latex = self.latex
        if self.line_no and not self.line_index:
            self.line_index = max(0, self.line_no - 1)
        elif self.line_index and not self.line_no:
            self.line_no = self.line_index + 1
        if self.line_type == LineType.SCRATCH:
            self.is_strikethrough = True


class ExtractedQuestion(BaseModel):
    question_id: str = "Q1"
    question_text: str = ""
    grade_level: str = ""
    student_name_detected: str = ""
    student_class_detected: str = ""
    confidence: float = 1.0
    overall_confidence: float = 1.0
    has_unclear: bool = False
    syntax_valid: bool = True
    ocr_warnings: list[str] = Field(default_factory=list)
    lines: list[ExtractedLine] = Field(default_factory=list)

    def model_post_init(self, __context):
        if self.lines:
            active_lines = [l for l in self.lines if not l.is_strikethrough] or self.lines
            mean_conf = sum(l.confidence for l in active_lines) / len(active_lines)
            self.overall_confidence = round(mean_conf, 2)
            self.confidence = self.overall_confidence
            if any(l.unclear and not l.is_strikethrough for l in self.lines):
                self.has_unclear = True


class OCRResult(BaseModel):
    submission_id: str = ""
    image_hash: str = ""
    questions: list[ExtractedQuestion] = Field(default_factory=list)
    processing_time_ms: int = 0
    model_used: str = "gemini-2.0-flash"
    cached: bool = False
    ocr_warnings: list[str] = Field(default_factory=list)


class MathVerificationSummary(BaseModel):
    equations_checked: int = 0
    valid_transitions: int = 0
    invalid_transitions: int = 0
    detected_roots: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    summary: str = ""


class CriterionEvaluation(BaseModel):
    criterion_id: str
    name: str = ""
    description: str = ""
    score: float = 0.0
    earned_points: float = 0.0
    max_score: float = 0.0
    max_points: float = 0.0
    matched: bool = True
    status: str = "passed"  # "passed" | "partial" | "failed"
    error_type: str | None = "none"
    feedback: str = ""

    def model_post_init(self, __context):
        if not self.name and self.description:
            self.name = self.description
        elif not self.description and self.name:
            self.description = self.name
        if self.score and not self.earned_points:
            self.earned_points = self.score
        elif self.earned_points and not self.score:
            self.score = self.earned_points
        if self.max_score and not self.max_points:
            self.max_points = self.max_score
        elif self.max_points and not self.max_score:
            self.max_score = self.max_points


class QuestionGradingResult(BaseModel):
    question_id: str
    suggested_score: float = 0.0
    earned_points: float = 0.0
    max_score: float = 0.0
    total_points: float = 0.0
    self_critique_applied: bool = True
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    criteria_evaluation: list[CriterionEvaluation] = Field(default_factory=list)
    criteria_evaluations: list[CriterionEvaluation] = Field(default_factory=list)
    error_propagation_detected: bool = False
    affected_steps: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    confidence_level: str = "green"  # "green" | "yellow" | "red"
    teacher_summary_comment: str = ""

    def model_post_init(self, __context):
        if self.suggested_score and not self.earned_points:
            self.earned_points = self.suggested_score
        elif self.earned_points and not self.suggested_score:
            self.suggested_score = self.earned_points
        if self.max_score and not self.total_points:
            self.total_points = self.max_score
        elif self.total_points and not self.max_score:
            self.max_score = self.total_points
        if self.criteria_evaluation and not self.criteria_evaluations:
            self.criteria_evaluations = self.criteria_evaluation
        elif self.criteria_evaluations and not self.criteria_evaluation:
            self.criteria_evaluation = self.criteria_evaluations


class GradingResult(BaseModel):
    submission_id: str
    exam_id: str
    student_id: str = ""
    student_name: str = "Học sinh ẩn danh"
    total_earned: float = 0.0
    total_possible: float = 10.0
    percentage: float = 0.0
    questions: list[QuestionGradingResult] = Field(default_factory=list)
    overall_confidence: float = 1.0
    overall_confidence_level: str = "green"
    status: str = "pending_review"
    error_propagation_detected: bool = False
    graded_at: str = ""
    reviewed_at: str | None = None
    reviewed_by: str | None = None
    teacher_notes: str = ""

    def model_post_init(self, __context):
        if self.questions:
            self.total_earned = round(sum(q.earned_points for q in self.questions), 2)
            self.total_possible = round(sum(q.total_points for q in self.questions), 2)
            if self.total_possible > 0:
                self.percentage = round((self.total_earned / self.total_possible) * 100.0, 1)
            mean_conf = sum(q.confidence for q in self.questions) / len(self.questions)
            self.overall_confidence = round(mean_conf, 2)
            if self.overall_confidence >= 0.90:
                self.overall_confidence_level = "green"
            elif self.overall_confidence >= 0.70:
                self.overall_confidence_level = "yellow"
            else:
                self.overall_confidence_level = "red"
            self.error_propagation_detected = any(q.error_propagation_detected for q in self.questions)


class SubmissionGradingRecord(BaseModel):
    submission_id: str
    exam_id: str
    exam_title: str = ""
    student_name: str = "Học sinh ẩn danh"
    detected_student_name: str = ""
    page_count: int = 1
    rubric_version: int = 1
    status: SubmissionStatus = SubmissionStatus.UPLOADED
    image_hash: str = ""
    cache_hit: bool = False
    is_mock: bool = False
    quality: ImageQualityReport = Field(default_factory=ImageQualityReport)
    extracted_questions: list[ExtractedQuestion] = Field(default_factory=list)
    math_verification: MathVerificationSummary | None = None
    question_results: list[QuestionGradingResult] = Field(default_factory=list)
    ai_total_score: float = 0.0
    total_points: float = 10.0
    final_score: float | None = None
    needs_teacher_review: bool = False
    teacher_note: str | None = None
    created_at: str = ""


class TeacherReviewRequest(BaseModel):
    submission_id: str
    score_overrides: dict[str, float] = Field(default_factory=dict, description="question_id -> điểm mới")
    teacher_note: str | None = None
    approve: bool = True
