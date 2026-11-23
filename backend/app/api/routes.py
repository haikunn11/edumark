from __future__ import annotations

import json
from datetime import datetime, timezone
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from backend.app.models.schemas import (
    ExamRubric,
    QuestionRubric,
    RubricCriterion,
    SubmissionGradingRecord,
    SubmissionStatus,
    TeacherReviewRequest,
    GradingResult,
    QuestionGradingResult,
)
from backend.app.services.pipeline import grading_pipeline

router = APIRouter()

# In-memory database phục vụ test flow và demo
EXAM_DB: dict[str, ExamRubric] = {}
SUBMISSION_DB: dict[str, SubmissionGradingRecord] = {}


def get_auto_rubric() -> ExamRubric:
    """Barem linh hoạt: AI tự động nhận diện đề bài trong ảnh và sinh tiêu chí chấm."""
    return ExamRubric(
        exam_id="AUTO",
        title="Tự động nhận diện Đề bài (AI Auto)",
        exam_title="Tự động nhận diện Đề bài (AI Auto)",
        total_points=2.0,
        rubric_version=1,
        questions=[
            QuestionRubric(
                question_id="AUTO",
                question_text="AUTO",
                max_score=2.0,
                total_points=2.0,
                allow_error_propagation=True,
                criteria=[],
            )
        ],
    )


def get_default_secondary_math_rubric() -> ExamRubric:
    """Tạo đề thi mẫu: Kiểm tra Toán 9 - Giải phương trình chứa ẩn ở mẫu."""
    return ExamRubric(
        exam_id="TOAN9_01",
        title="Kiểm tra 15p: Giải phương trình chứa ẩn ở mẫu",
        exam_title="Kiểm tra 15p: Giải phương trình chứa ẩn ở mẫu",
        total_points=2.0,
        rubric_version=1,
        questions=[
            QuestionRubric(
                question_id="Q1",
                question_text="Giải phương trình: x/(x-2) + 1 = 4/(x-2)",
                max_score=2.0,
                total_points=2.0,
                allow_error_propagation=True,
                criteria=[
                    RubricCriterion(
                        criterion_id="Q1_C1_DKXD",
                        name="Tìm điều kiện xác định",
                        description="Đặt điều kiện mẫu thức khác 0: x != 2",
                        max_score=0.5,
                        max_points=0.5,
                        required_logic="x \\neq 2",
                    ),
                    RubricCriterion(
                        criterion_id="Q1_C2_BIEN_DOI",
                        name="Quy đồng và giải phương trình",
                        description="Quy đồng khử mẫu ra 2x = 6 <=> x = 3",
                        max_score=1.0,
                        max_points=1.0,
                        required_logic="x + (x - 2) = 4 \\Leftrightarrow 2x = 6 \\Leftrightarrow x = 3",
                        depends_on=["Q1_C1_DKXD"],
                    ),
                    RubricCriterion(
                        criterion_id="Q1_C3_KET_LUAN",
                        name="Đối chiếu điều kiện và kết luận tập nghiệm",
                        description="Đối chiếu x = 3 thỏa mãn ĐKXĐ, S = {3}",
                        max_score=0.5,
                        max_points=0.5,
                        required_logic="x = 3 thỏa mãn ĐKXĐ, S = {3}",
                        depends_on=["Q1_C2_BIEN_DOI"],
                    ),
                ],
            )
        ],
    )


# Đăng ký sẵn đề thi mặc định
_auto_rubric = get_auto_rubric()
EXAM_DB[_auto_rubric.exam_id] = _auto_rubric
_default_rubric = get_default_secondary_math_rubric()
EXAM_DB[_default_rubric.exam_id] = _default_rubric


@router.get("/health")
def health_check() -> dict:
    return {"status": "ok", "service": "EduMark AI Backbone", "version": "0.1.0"}


@router.get("/config/key-status")
def get_key_status() -> dict:
    from backend.app.core.config import settings
    has_openai = bool(settings.OPENAI_API_KEY and not settings.OPENAI_API_KEY.startswith("mock_"))
    has_gemini = bool(settings.GEMINI_API_KEY and not settings.GEMINI_API_KEY.startswith("mock_"))
    has_key = has_openai or has_gemini
    provider = "openai" if has_openai else ("gemini" if has_gemini else "mock")
    model = settings.DEFAULT_OPENAI_MODEL if has_openai else settings.DEFAULT_VISION_MODEL

    return {
        "configured": has_key,
        "provider": provider,
        "model": model,
        "vision_model": model,
        "reasoning_model": model,
    }


@router.post("/config/key")
def update_gemini_key(req: dict) -> dict:
    """Lưu OpenAI hoặc Gemini API Key trực tiếp từ Web Dashboard vào .env và runtime."""
    import os
    from backend.app.core.config import settings
    from backend.app.services.agent1_ocr import vision_agent
    from backend.app.services.agent2_grader import reasoning_agent
    from backend.app.core.cache import cache

    new_key = req.get("api_key", "").strip()
    if not new_key:
        raise HTTPException(status_code=400, detail="API Key không được để trống")

    is_openai = new_key.startswith("sk-")

    # Kiểm tra xác thực key nếu không phải mock key test
    if not new_key.startswith("mock_"):
        if is_openai:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=new_key)
                client.chat.completions.create(
                    model=settings.DEFAULT_OPENAI_MODEL,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=5,
                )
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"OpenAI API Key không hợp lệ: {exc}")
        else:
            try:
                from google import genai
                client = genai.Client(api_key=new_key)
                client.models.generate_content(
                    model=settings.DEFAULT_VISION_MODEL,
                    contents="ping",
                )
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Gemini API Key không hợp lệ: {exc}")

    cache.clear()

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    env_path = os.path.join(project_root, ".env")

    if is_openai:
        settings.AI_PROVIDER = "openai"
        settings.OPENAI_API_KEY = new_key
        vision_agent.openai_api_key = new_key
        reasoning_agent.openai_api_key = new_key
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(f"AI_PROVIDER=openai\nOPENAI_API_KEY={new_key}\nDEFAULT_OPENAI_MODEL={settings.DEFAULT_OPENAI_MODEL}\nGEMINI_API_KEY={settings.GEMINI_API_KEY}\n")
        msg = f"Đã kích hoạt OpenAI ({settings.DEFAULT_OPENAI_MODEL}) thành công!"
    else:
        settings.AI_PROVIDER = "gemini"
        settings.GEMINI_API_KEY = new_key
        vision_agent.api_key = new_key
        reasoning_agent.api_key = new_key
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(f"AI_PROVIDER=gemini\nOPENAI_API_KEY={settings.OPENAI_API_KEY}\nGEMINI_API_KEY={new_key}\n")
        msg = "Đã kích hoạt Google Gemini thành công!"

    return {"status": "ok", "message": msg}


@router.get("/exams", response_model=list[ExamRubric])
def list_exams() -> list[ExamRubric]:
    """Danh sách tất cả các đề thi và barem trong hệ thống."""
    return list(EXAM_DB.values())


@router.get("/exams/{exam_id}", response_model=ExamRubric)
def get_exam(exam_id: str) -> ExamRubric:
    """Lấy chi tiết barem của 1 đề thi."""
    if exam_id not in EXAM_DB:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "E002", "message": f"Không tìm thấy đề thi với mã: {exam_id}"},
        )
    return EXAM_DB[exam_id]


@router.post("/exams", response_model=ExamRubric)
def create_exam(rubric: ExamRubric) -> ExamRubric:
    """Tạo hoặc cập nhật một barem đề thi mới."""
    EXAM_DB[rubric.exam_id] = rubric
    return rubric


@router.post("/submissions/grade", response_model=SubmissionGradingRecord)
async def grade_submission(
    file: UploadFile = File(...),
    exam_id: str = Form("AUTO"),
    student_name: str = Form("Học sinh ẩn danh"),
) -> SubmissionGradingRecord:
    """Tải lên ảnh bài thi và chạy qua toàn bộ Pipeline AI tự động (WP-12)."""
    # Validate định dạng file
    content_type = file.content_type or ""
    allowed_types = ["image/jpeg", "image/png", "image/jpg", "application/pdf"]
    if content_type.lower() not in allowed_types and not file.filename.lower().endswith((".jpg", ".jpeg", ".png", ".pdf")):
        raise HTTPException(
            status_code=400,
            detail={"error_code": "E001", "message": "Định dạng file không hỗ trợ. Chỉ chấp nhận JPEG, PNG, PDF."},
        )

    exam_id_norm = (exam_id or "").strip()
    if not exam_id_norm or exam_id_norm == "AUTO":
        rubric = get_auto_rubric()
    elif exam_id_norm in EXAM_DB:
        rubric = EXAM_DB[exam_id_norm]
    else:
        rubric = get_auto_rubric()
    image_bytes = await file.read()

    if len(image_bytes) == 0:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "E001", "message": "File tải lên bị rỗng (0 bytes)."},
        )

    if len(image_bytes) > 50 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail={"error_code": "E003", "message": "Dung lượng file vượt quá giới hạn 50MB."},
        )

    record = grading_pipeline.process_submission(
        image_bytes=image_bytes,
        rubric=rubric,
        student_name=student_name,
    )
    SUBMISSION_DB[record.submission_id] = record
    return record


@router.post("/submissions/review", response_model=SubmissionGradingRecord)
def review_submission(req: TeacherReviewRequest) -> SubmissionGradingRecord:
    """Giáo viên xem xét lại điểm số, ghi đè hoặc phê duyệt (Human-in-the-Loop - WP-13)."""
    if req.submission_id not in SUBMISSION_DB:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "E002", "message": "Không tìm thấy bài thi cần duyệt."},
        )

    record = SUBMISSION_DB[req.submission_id]

    # Ghi đè điểm nếu có yêu cầu (Teacher Override)
    if req.score_overrides:
        total = 0.0
        for q_res in record.question_results:
            if q_res.question_id in req.score_overrides:
                override_val = float(req.score_overrides[q_res.question_id])
                # Validate override không âm và không vượt quá max_points
                if override_val < 0.0 or override_val > q_res.max_score:
                    raise HTTPException(
                        status_code=400,
                        detail={"error_code": "E001", "message": f"Điểm ghi đè {override_val} phải nằm trong khoảng [0, {q_res.max_score}]."},
                    )
                q_res.suggested_score = override_val
                q_res.earned_points = override_val
            total += q_res.suggested_score
        record.final_score = round(total, 2)
        record.ai_total_score = round(total, 2)

    if req.teacher_note:
        record.teacher_note = req.teacher_note

    if req.approve:
        record.status = SubmissionStatus.APPROVED
        record.needs_teacher_review = False
    elif req.score_overrides:
        # Nếu chỉ override mà chưa approve
        record.needs_teacher_review = False

    SUBMISSION_DB[record.submission_id] = record
    return record


@router.get("/submissions/{submission_id}", response_model=SubmissionGradingRecord)
def get_submission(submission_id: str) -> SubmissionGradingRecord:
    """Lấy chi tiết kết quả chấm 1 bài thi."""
    if submission_id not in SUBMISSION_DB:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "E002", "message": "Không tìm thấy bài thi"},
        )
    return SUBMISSION_DB[submission_id]


@router.post("/submissions/batch")
async def batch_grade_submissions(
    file: UploadFile = File(...),
    exam_id: str = Form("TOAN9_01"),
) -> dict:
    """
    Tải lên tệp ZIP hoặc PDF nhiều trang để chấm hàng loạt (WP-14 & WP-15).
    """
    from backend.app.services.batch import (
        split_pdf_pages,
        extract_zip_images,
        batch_manager,
        BatchSizeLimitError,
        ZipExtractionError,
        PDFSplitError,
    )

    if exam_id not in EXAM_DB:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "E002", "message": f"Không tìm thấy Barem cho exam_id: {exam_id}"},
        )

    rubric = EXAM_DB[exam_id]
    file_bytes = await file.read()
    filename = file.filename.lower()

    try:
        if filename.endswith(".pdf") or "pdf" in (file.content_type or ""):
            pages = split_pdf_pages(file_bytes, max_pages=50)
            items = [(f"trang_{p_num:02d}", p_bytes) for p_num, p_bytes in pages]
        elif filename.endswith(".zip") or "zip" in (file.content_type or ""):
            items = extract_zip_images(file_bytes, max_files=50)
        else:
            raise HTTPException(
                status_code=400,
                detail={"error_code": "E001", "message": "Chỉ chấp nhận file ZIP hoặc PDF cho xử lý hàng loạt."},
            )

        job = batch_manager.create_job(exam_id=exam_id, total_count=len(items))
        # Chạy đồng bộ cho batch nhỏ
        completed_job = batch_manager.run_batch_sync(job.job_id, items, rubric)

        # Lưu các bản ghi vào SUBMISSION_DB
        for r in completed_job.results:
            SUBMISSION_DB[r.submission_id] = r

        return completed_job.to_dict()

    except (BatchSizeLimitError, ZipExtractionError, PDFSplitError) as err:
        raise HTTPException(status_code=400, detail={"error_code": "E001", "message": str(err)})


@router.get("/submissions/batch/{job_id}")
def get_batch_job_status(job_id: str) -> dict:
    """Tra cứu tiến độ và kết quả của tác vụ chấm hàng loạt (WP-17)."""
    from backend.app.services.batch import batch_manager

    job = batch_manager.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "E002", "message": f"Không tìm thấy batch job với ID: {job_id}"},
        )
    return job.to_dict()


@router.get("/exports/excel/{exam_id}")
def export_exam_results_excel(
    exam_id: str,
    format_type: str = "standard",
):
    """Xuất bảng điểm Excel chuẩn hóa (.xlsx) cho VnEdu, SMAS hoặc Standard (WP-16)."""
    from fastapi.responses import Response
    from backend.app.services.batch import export_to_excel, ExportNoDataError

    # Lấy các bài thi thuộc exam_id
    records = [rec for rec in SUBMISSION_DB.values() if rec.exam_id == exam_id]
    if not records:
        # Nếu chưa có bài nào trong DB, dùng sample demo để test
        records = list(SUBMISSION_DB.values())

    if not records:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "E001", "message": "Chưa có bài thi nào được chấm để xuất bảng điểm."},
        )

    fmt = format_type.lower()
    if fmt not in ("standard", "vnedu", "smas"):
        fmt = "standard"

    try:
        excel_bytes = export_to_excel(exam_id=exam_id, records=records, format_type=fmt)
        filename = f"Bang_Diem_{exam_id}_{fmt}.xlsx"
        return Response(
            content=excel_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except ExportNoDataError as e:
        raise HTTPException(status_code=400, detail={"error_code": "E001", "message": str(e)})
