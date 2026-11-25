from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from backend.app.models.schemas import (
    ExamRubric,
    SubmissionGradingRecord,
    SubmissionStatus,
    ExtractedQuestion,
    ImageQualityReport,
    QuestionGradingResult,
)
from backend.app.core.cache import cache, compute_image_hash
from backend.app.core.storage import storage
from backend.app.services.preprocessor import preprocessor
from backend.app.services.agent1_ocr import vision_agent
from backend.app.services.agent2_grader import reasoning_agent
from backend.app.services.math_verifier import verify_question_math


class AutomatedGradingPipeline:
    """Orchestrator kết nối toàn bộ luồng: Tiền xử lý (1..N trang) -> Cache -> Agent 1 -> SymPy -> Agent 2 -> HITL."""

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
        return self.process_multipage_submission(
            pages_bytes=[image_bytes],
            rubric=rubric,
            student_name=student_name,
            submission_id=submission_id,
        )

    def process_multipage_submission(
        self,
        pages_bytes: list[bytes],
        rubric: ExamRubric,
        student_name: str = "Học sinh ẩn danh",
        submission_id: str | None = None,
    ) -> SubmissionGradingRecord:
        if not pages_bytes:
            raise ValueError("Danh sách trang ảnh bài thi không được rỗng.")

        sub_id = submission_id or f"sub_{uuid.uuid4().hex[:8]}"
        if len(pages_bytes) == 1:
            img_hash = compute_image_hash(pages_bytes[0])
        else:
            h = hashlib.md5()
            for p in pages_bytes:
                h.update(p)
            img_hash = h.hexdigest()

        record = SubmissionGradingRecord(
            submission_id=sub_id,
            exam_id=rubric.exam_id,
            exam_title=rubric.title,
            student_name=student_name,
            page_count=len(pages_bytes),
            rubric_version=rubric.rubric_version,
            status=SubmissionStatus.UPLOADED,
            image_hash=img_hash,
            cache_hit=False,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # --- BƯỚC 1: Tiền xử lý toàn bộ trang ảnh ---
        processed_pages: list[bytes] = []
        reports: list[ImageQualityReport] = []
        for idx, page_bytes in enumerate(pages_bytes, start=1):
            proc_bytes, q_report = self.preprocessor.process_image(page_bytes)
            processed_pages.append(proc_bytes)
            reports.append(q_report)
            filename = f"{sub_id}.jpg" if idx == 1 else f"{sub_id}_p{idx}.jpg"
            self.storage.save_file(filename, proc_bytes, subfolder=rubric.exam_id)

        # Gộp báo cáo chất lượng ảnh (nếu nhiều trang, lấy ngưỡng kém nhất để cảnh báo an toàn)
        primary_report = reports[0]
        all_transforms: list[str] = []
        all_warnings: list[str] = []
        for idx, r in enumerate(reports, start=1):
            prefix = f"p{idx}:" if len(reports) > 1 else ""
            all_transforms.extend(f"{prefix}{t}" for t in r.applied_transforms)
            all_warnings.extend(f"{prefix}{w}" for w in r.warnings)

        merged_quality = ImageQualityReport(
            is_blurry=any(r.is_blurry for r in reports),
            blur_score=min(r.blur_score for r in reports),
            brightness=round(sum(r.brightness for r in reports) / len(reports), 1),
            is_too_dark=any(r.is_too_dark for r in reports),
            is_overexposed=any(r.is_overexposed for r in reports),
            perspective_corrected=any(r.perspective_corrected for r in reports),
            shadow_removed=any(r.shadow_removed for r in reports),
            is_acceptable=all(r.is_acceptable for r in reports),
            warnings=all_warnings,
            recommendation=primary_report.recommendation,
            applied_transforms=all_transforms,
        )
        record.quality = merged_quality
        record.status = SubmissionStatus.PREPROCESSED

        if merged_quality.is_blurry:
            record.status = SubmissionStatus.FLAGGED_REVIEW
            record.needs_teacher_review = True

        # --- BƯỚC 2: Agent 1 Vision OCR (với MD5 Caching) ---
        is_live_key = self.vision_agent.is_live
        record.is_mock = not is_live_key
        cache_key_ocr = f"cache:ocr:{'live' if is_live_key else 'mock'}:{img_hash}"

        cached_extracted = self.cache.get(cache_key_ocr)
        if cached_extracted:
            extracted_questions = [ExtractedQuestion(**q) for q in cached_extracted]
            record.cache_hit = True
        else:
            if len(processed_pages) == 1:
                extracted_questions = self.vision_agent.extract_latex(
                    processed_pages[0],
                    applied_transforms=merged_quality.applied_transforms,
                )
            else:
                extracted_questions = self.vision_agent.extract_latex_multipage(
                    processed_pages,
                    applied_transforms=merged_quality.applied_transforms,
                )
            self.cache.set(
                cache_key_ocr,
                [q.model_dump() for q in extracted_questions],
                ttl_seconds=86400,
            )

        record.extracted_questions = extracted_questions
        record.status = SubmissionStatus.OCR_COMPLETED

        # Tự điền tên học sinh nếu OCR đọc được từ phần đầu trang giấy thi
        detected_name = next((q.student_name_detected.strip() for q in extracted_questions if q.student_name_detected.strip()), "")
        if detected_name:
            record.detected_student_name = detected_name
            if not student_name or student_name.strip() in ("", "Học sinh ẩn danh"):
                record.student_name = detected_name

        # --- BƯỚC 3: Kiểm chứng toán học SymPy ---
        primary_mv = verify_question_math(extracted_questions[0]) if extracted_questions else None
        record.math_verification = primary_mv

        # --- BƯỚC 4: Agent 2 Precision Grader ---
        if rubric.exam_id == "AUTO" or not rubric.questions:
            if extracted_questions:
                exam_title, q_result = self.reasoning_agent.grade_auto_rubric(
                    extracted_questions,
                    math_verification=primary_mv,
                )
                rubric.title = exam_title
                record.exam_title = exam_title
                record.question_results.append(q_result)
                total_ai_score = q_result.suggested_score
                any_review_needed = q_result.needs_review
                record.total_points = q_result.total_points
            else:
                total_ai_score = 0.0
                any_review_needed = True
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
                    norm_rid = normalize_qid(q_rubric.question_id)
                    for n_k, n_v in norm_q_map.items():
                        if n_k == norm_rid or norm_rid.endswith(n_k) or n_k.endswith(norm_rid):
                            extracted_q = n_v
                            break

                if not extracted_q and len(rubric.questions) == 1 and len(extracted_questions) == 1:
                    extracted_q = extracted_questions[0]

                if not extracted_q:
                    q_result = QuestionGradingResult(
                        question_id=q_rubric.question_id,
                        suggested_score=0.0,
                        max_score=q_rubric.max_score,
                        self_critique_applied=True,
                        needs_review=False,
                        teacher_summary_comment="Học sinh không làm câu này.",
                    )
                else:
                    q_mv = verify_question_math(extracted_q)
                    q_result = self.reasoning_agent.grade_question(
                        question=extracted_q,
                        rubric=q_rubric,
                        run_self_critique=True,
                        math_verification=q_mv,
                    )

                total_ai_score += q_result.suggested_score
                if q_result.needs_review:
                    any_review_needed = True
                record.question_results.append(q_result)

        # --- BƯỚC 5: Hoàn tất chấm sơ khảo (HITL Tagging) ---
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
