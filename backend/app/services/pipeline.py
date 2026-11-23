from __future__ import annotations

import re
import uuid
from backend.app.models.schemas import (
    ExamRubric,
    SubmissionGradingRecord,
    SubmissionStatus,
    ExtractedQuestion,
    QuestionGradingResult,
)
from backend.app.core.cache import cache, compute_image_hash
from backend.app.core.storage import storage
from backend.app.services.preprocessor import preprocessor
from backend.app.services.agent1_ocr import vision_agent
from backend.app.services.agent2_grader import reasoning_agent


class AutomatedGradingPipeline:
    """Orchestrator kết nối toàn bộ luồng: Tiền xử lý -> Cache -> Agent 1 -> Agent 2 -> Human-in-the-Loop."""

    def __init__(self) -> None:
        self.preprocessor = preprocessor
        self.vision_agent = vision_agent
        self.reasoning_agent = reasoning_agent
        self.cache = cache
        self.storage = storage

    def process_submission(
        self,
        image_bytes: bytes,
        rubric: ExamRubric,
        student_name: str = "Học sinh ẩn danh",
        submission_id: str | None = None,
    ) -> SubmissionGradingRecord:
        sub_id = submission_id or f"sub_{uuid.uuid4().hex[:8]}"
        img_hash = compute_image_hash(image_bytes)
        cache_key_ocr = f"cache:ocr:{img_hash}"

        # Khởi tạo bản ghi kết quả
        record = SubmissionGradingRecord(
            submission_id=sub_id,
            exam_id=rubric.exam_id,
            exam_title=rubric.title,
            student_name=student_name,
            rubric_version=rubric.rubric_version,
            status=SubmissionStatus.UPLOADED,
            image_hash=img_hash,
            cache_hit=False,
        )

        # --- BƯỚC 1: Tiền xử lý ảnh ---
        proc_bytes, quality_report = self.preprocessor.process_image(image_bytes)
        record.quality = quality_report
        record.status = SubmissionStatus.PREPROCESSED

        if quality_report.is_blurry:
            record.status = SubmissionStatus.FLAGGED_REVIEW
            record.needs_teacher_review = True
            # Vẫn cho chạy tiếp hoặc đánh dấu cần xem thủ công

        # Lưu ảnh vào storage
        storage_path = self.storage.save_file(f"{sub_id}.jpg", proc_bytes, subfolder=rubric.exam_id)

        # --- BƯỚC 2: OCR Agent (với Caching) ---
        is_live_key = self.vision_agent.is_live
        record.is_mock = not is_live_key
        cache_key_ocr = f"cache:ocr:{'live' if is_live_key else 'mock'}:{img_hash}"

        cached_extracted = self.cache.get(cache_key_ocr)
        if cached_extracted:
            extracted_questions = [ExtractedQuestion(**q) for q in cached_extracted]
            record.cache_hit = True
        else:
            extracted_questions = self.vision_agent.extract_latex(
                proc_bytes, quality_report.applied_transforms
            )
            # Lưu vào cache tương ứng theo chế độ live hoặc mock
            self.cache.set(
                cache_key_ocr,
                [q.model_dump() for q in extracted_questions],
                ttl_seconds=86400,
            )

        record.extracted_questions = extracted_questions
        record.status = SubmissionStatus.OCR_COMPLETED

        # --- BƯỚC 3: Grading Agent (Step-by-Step Barem) ---
        if rubric.exam_id == "AUTO" or not rubric.questions:
            if extracted_questions:
                exam_title, q_result = self.reasoning_agent.grade_auto_rubric(extracted_questions)
                rubric.title = exam_title
                record.exam_title = exam_title
                record.question_results.append(q_result)
                total_ai_score = q_result.suggested_score
                any_review_needed = q_result.needs_review
                record.total_points = q_result.total_points
            else:
                total_ai_score = 0.0
                record.total_points = 10.0
        else:
            def normalize_qid(s: str) -> str:
                return re.sub(r"[^0-9a-zA-Z]", "", str(s)).lower()

            q_map = {q.question_id: q for q in extracted_questions}
            norm_q_map = {normalize_qid(q.question_id): q for q in extracted_questions}
            total_ai_score = 0.0
            any_review_needed = False

            for q_rubric in rubric.questions:
                extracted_q = q_map.get(q_rubric.question_id)
                if not extracted_q:
                    # Tìm kiếm linh hoạt (vd: Q1 <-> Câu 1, 1, Bài 1)
                    norm_rid = normalize_qid(q_rubric.question_id)
                    for n_k, n_v in norm_q_map.items():
                        if n_k == norm_rid or norm_rid.endswith(n_k) or n_k.endswith(norm_rid):
                            extracted_q = n_v
                            break

                # Nếu bài thi 1 câu và kết quả OCR chỉ có 1 câu, tự động ánh xạ
                if not extracted_q and len(rubric.questions) == 1 and len(extracted_questions) == 1:
                    extracted_q = extracted_questions[0]

                if not extracted_q:
                    # Học sinh bỏ trống câu này
                    q_result = QuestionGradingResult(
                        question_id=q_rubric.question_id,
                        suggested_score=0.0,
                        max_score=q_rubric.max_score,
                        self_critique_applied=True,
                        needs_review=False,
                        teacher_summary_comment="Học sinh không làm câu này.",
                    )
                else:
                    q_result = self.reasoning_agent.grade_question(
                        question=extracted_q,
                        rubric=q_rubric,
                        run_self_critique=True,
                    )

                total_ai_score += q_result.suggested_score
                if q_result.needs_review:
                    any_review_needed = True
                record.question_results.append(q_result)

        # --- BƯỚC 4: Hoàn tất chấm sơ khảo (HITL Tagging) ---
        record.ai_total_score = round(total_ai_score, 2)
        record.final_score = record.ai_total_score
        if not getattr(record, "total_points", None):
            record.total_points = round(sum(q.total_points for q in record.question_results), 2) or 10.0
        record.needs_teacher_review = any_review_needed or record.quality.is_blurry
        record.status = (
            SubmissionStatus.FLAGGED_REVIEW
            if record.needs_teacher_review
            else SubmissionStatus.GRADED_DRAFT
        )

        return record


grading_pipeline = AutomatedGradingPipeline()
