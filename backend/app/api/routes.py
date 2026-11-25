from __future__ import annotations

import os
from fastapi import APIRouter, BackgroundTasks, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from backend.app.core.config import settings
from backend.app.core.database import db
from backend.app.models.schemas import (
    ExamRubric,
    ImageQualityReport,
    QuestionRubric,
    RubricCriterion,
    SmartFolderBatchResult,
    SubmissionGradingRecord,
    SubmissionStatus,
    TeacherReviewRequest,
)
from backend.app.services.agent1_ocr import OCRApiError, OCRLowConfidenceError
from backend.app.services.folder_reassembler import (
    ALLOWED_IMAGE_EXTS,
    build_sorted_output_folder,
    grade_student_folder,
    load_images_from_local_folder,
    zip_sorted_output_folder,
)
from backend.app.services.student_clusterer import (
    grade_smart_folder_multistudent,
    zip_directory_recursive,
)
from backend.app.services.pipeline import grading_pipeline
from backend.app.services.preprocessor import assess_capture_quality


class FolderPathGradeRequest(BaseModel):
    folder_path: str
    exam_id: str = "AUTO"
    student_name: str = "Học sinh ẩn danh"

router = APIRouter()

# Giữ tham chiếu dict để tương thích ngược nếu code ngoài có import trực tiếp
EXAM_DB: dict[str, ExamRubric] = {}
SUBMISSION_DB: dict[str, SubmissionGradingRecord] = {}


def get_auto_rubric() -> ExamRubric:
    """Barem linh hoạt: AI tự động nhận diện đề bài trong ảnh và sinh tiêu chí chấm."""
    return ExamRubric(
        exam_id="AUTO",
        title="Tự động nhận diện Đề bài (AI Auto)",
        exam_title="Tự động nhận diện Đề bài (AI Auto)",
        total_points=10.0,
        rubric_version=1,
        questions=[
            QuestionRubric(
                question_id="AUTO",
                question_text="AUTO",
                max_score=10.0,
                total_points=10.0,
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


def _seed_default_exams() -> None:
    for rubric in (get_auto_rubric(), get_default_secondary_math_rubric()):
        if not db.get_exam(rubric.exam_id):
            db.save_exam(rubric)
        EXAM_DB[rubric.exam_id] = rubric


_seed_default_exams()


def _validate_upload_image_or_pdf(file: UploadFile, content: bytes) -> None:
    content_type = (file.content_type or "").lower()
    filename = (file.filename or "").lower()
    allowed_types = ("image/jpeg", "image/png", "image/jpg", "image/webp", "application/pdf")
    if content_type not in allowed_types and not filename.endswith((".jpg", ".jpeg", ".png", ".webp", ".pdf")):
        raise HTTPException(
            status_code=400,
            detail={"error_code": "E001", "message": "Định dạng file không hỗ trợ. Chỉ chấp nhận JPEG, PNG, WEBP, PDF."},
        )
    if len(content) == 0:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "E001", "message": "File tải lên bị rỗng (0 bytes)."},
        )
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail={"error_code": "E003", "message": "Dung lượng file vượt quá giới hạn 50MB."},
        )


@router.get("/health")
def health_check() -> dict:
    return {
        "status": "ok",
        "service": "EduMark AI Backbone",
        "version": settings.VERSION,
        "db_ok": db.ping(),
    }


@router.get("/config/key-status")
def get_key_status() -> dict:
    has_openai = bool(settings.OPENAI_API_KEY and not settings.OPENAI_API_KEY.startswith("mock_"))
    has_gemini = bool(settings.GEMINI_API_KEY and not settings.GEMINI_API_KEY.startswith("mock_"))
    has_key = has_openai or has_gemini
    pref = (settings.AI_PROVIDER or "openai").lower()
    if pref == "gemini" and has_gemini:
        provider, model = "gemini", settings.DEFAULT_VISION_MODEL
    elif has_openai:
        provider, model = "openai", settings.DEFAULT_OPENAI_MODEL
    elif has_gemini:
        provider, model = "gemini", settings.DEFAULT_VISION_MODEL
    else:
        provider, model = "mock", settings.DEFAULT_VISION_MODEL

    return {
        "configured": has_key,
        "provider": provider,
        "model": model,
        "vision_model": model,
        "reasoning_model": model,
    }


@router.post("/config/key")
def update_api_key(
    req: dict,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict:
    """Lưu OpenAI hoặc Gemini API Key vào runtime & .env (có bảo vệ ADMIN_API_TOKEN ở staging/prod)."""
    if settings.ADMIN_API_TOKEN or settings.ENVIRONMENT != "local":
        expected = settings.ADMIN_API_TOKEN
        if not expected or x_admin_token != expected:
            raise HTTPException(
                status_code=403,
                detail={"error_code": "E007", "message": "Không có quyền thay đổi cấu hình API Key trên môi trường này."},
            )

    from backend.app.services.agent1_ocr import vision_agent
    from backend.app.services.agent2_grader import reasoning_agent
    from backend.app.core.cache import cache

    new_key = str(req.get("api_key", "")).strip()
    if not new_key:
        raise HTTPException(status_code=400, detail="API Key không được để trống")

    is_openai = new_key.startswith("sk-")

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
            f.write(
                f"AI_PROVIDER=openai\nOPENAI_API_KEY={new_key}\n"
                f"DEFAULT_OPENAI_MODEL={settings.DEFAULT_OPENAI_MODEL}\n"
                f"GEMINI_API_KEY={settings.GEMINI_API_KEY}\n"
            )
        msg = f"Đã kích hoạt OpenAI ({settings.DEFAULT_OPENAI_MODEL}) thành công!"
    else:
        settings.AI_PROVIDER = "gemini"
        settings.GEMINI_API_KEY = new_key
        vision_agent.api_key = new_key
        reasoning_agent.api_key = new_key
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(
                f"AI_PROVIDER=gemini\nOPENAI_API_KEY={settings.OPENAI_API_KEY}\n"
                f"GEMINI_API_KEY={new_key}\n"
                f"DEFAULT_VISION_MODEL={settings.DEFAULT_VISION_MODEL}\n"
            )
        msg = f"Đã kích hoạt Google Gemini ({settings.DEFAULT_VISION_MODEL}) thành công!"

    return {"status": "ok", "message": msg}


# --- Exam CRUD ---
@router.get("/exams", response_model=list[ExamRubric])
def list_exams() -> list[ExamRubric]:
    return db.list_exams()


@router.get("/exams/{exam_id}", response_model=ExamRubric)
def get_exam(exam_id: str) -> ExamRubric:
    exam = db.get_exam(exam_id)
    if not exam:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "E002", "message": f"Không tìm thấy đề thi với mã: {exam_id}"},
        )
    return exam


@router.post("/exams", response_model=ExamRubric)
def create_exam(rubric: ExamRubric) -> ExamRubric:
    db.save_exam(rubric)
    EXAM_DB[rubric.exam_id] = rubric
    return rubric


@router.put("/exams/{exam_id}", response_model=ExamRubric)
def update_exam(exam_id: str, rubric: ExamRubric) -> ExamRubric:
    if not db.get_exam(exam_id):
        raise HTTPException(
            status_code=404,
            detail={"error_code": "E002", "message": f"Không tìm thấy đề thi với mã: {exam_id}"},
        )
    rubric.exam_id = exam_id
    db.save_exam(rubric)
    EXAM_DB[exam_id] = rubric
    return rubric


@router.delete("/exams/{exam_id}")
def delete_exam(exam_id: str) -> dict:
    if exam_id == "AUTO":
        raise HTTPException(
            status_code=400,
            detail={"error_code": "E001", "message": "Không thể xóa barem hệ thống AUTO."},
        )
    deleted = db.delete_exam(exam_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "E002", "message": f"Không tìm thấy đề thi với mã: {exam_id}"},
        )
    EXAM_DB.pop(exam_id, None)
    return {"status": "deleted", "exam_id": exam_id}


# --- Mobile Pre-Capture Quality Check ---
@router.post("/submissions/quality-check", response_model=ImageQualityReport)
async def check_capture_quality(file: UploadFile = File(...)) -> ImageQualityReport:
    """Đánh giá nhanh độ nét & ánh sáng ảnh chụp điện thoại trước khi gửi chấm AI."""
    image_bytes = await file.read()
    _validate_upload_image_or_pdf(file, image_bytes)
    return assess_capture_quality(image_bytes)


# --- Submissions Listing & Grading ---
@router.get("/submissions", response_model=list[SubmissionGradingRecord])
def list_submissions(
    exam_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[SubmissionGradingRecord]:
    return db.list_submissions(exam_id=exam_id, status=status, limit=limit)


def _resolve_rubric(exam_id: str) -> ExamRubric:
    exam_id_norm = (exam_id or "").strip()
    if not exam_id_norm or exam_id_norm == "AUTO":
        return get_auto_rubric()
    existing = db.get_exam(exam_id_norm)
    if existing:
        return existing
    return get_auto_rubric()


@router.post("/submissions/grade", response_model=SubmissionGradingRecord)
async def grade_submission(
    file: UploadFile = File(...),
    exam_id: str = Form("AUTO"),
    student_name: str = Form("Học sinh ẩn danh"),
) -> SubmissionGradingRecord:
    """Tải lên 1 ảnh bài thi và chạy qua Pipeline AI tự động."""
    image_bytes = await file.read()
    _validate_upload_image_or_pdf(file, image_bytes)
    rubric = _resolve_rubric(exam_id)

    try:
        record = grading_pipeline.process_submission(
            image_bytes=image_bytes,
            rubric=rubric,
            student_name=student_name,
        )
    except OCRLowConfidenceError as exc:
        raise HTTPException(status_code=422, detail={"error_code": "E004", "message": str(exc)})
    except OCRApiError as exc:
        print(f"[Grade OCRApiError] {exc}")
        http_status = 429 if exc.error_code == "E006" else 503
        raise HTTPException(status_code=http_status, detail={"error_code": exc.error_code, "message": str(exc)})

    db.save_submission(record)
    SUBMISSION_DB[record.submission_id] = record
    return record


@router.post("/submissions/grade-multipage", response_model=SubmissionGradingRecord)
async def grade_multipage_submission(
    files: list[UploadFile] = File(...),
    exam_id: str = Form("AUTO"),
    student_name: str = Form("Học sinh ẩn danh"),
) -> SubmissionGradingRecord:
    """Chấm 1 bài làm viết tay gồm nhiều trang chụp từ App (1..10 ảnh)."""
    if not files:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "E001", "message": "Vui lòng chụp hoặc chọn ít nhất 1 trang bài làm."},
        )
    if len(files) > 10:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "E001", "message": "Một bài nộp hỗ trợ tối đa 10 trang ảnh."},
        )

    pages_bytes: list[bytes] = []
    for f in files:
        data = await f.read()
        _validate_upload_image_or_pdf(f, data)
        pages_bytes.append(data)

    rubric = _resolve_rubric(exam_id)

    try:
        record = grading_pipeline.process_multipage_submission(
            pages_bytes=pages_bytes,
            rubric=rubric,
            student_name=student_name,
        )
    except OCRLowConfidenceError as exc:
        raise HTTPException(status_code=422, detail={"error_code": "E004", "message": str(exc)})
    except OCRApiError as exc:
        print(f"[Grade OCRApiError] {exc}")
        http_status = 429 if exc.error_code == "E006" else 503
        raise HTTPException(status_code=http_status, detail={"error_code": exc.error_code, "message": str(exc)})

    db.save_submission(record)
    SUBMISSION_DB[record.submission_id] = record
    return record


@router.post("/submissions/grade-folder-upload", response_model=SubmissionGradingRecord)
async def grade_folder_upload(
    files: list[UploadFile] = File(...),
    exam_id: str = Form("AUTO"),
    student_name: str = Form("Học sinh ẩn danh"),
) -> SubmissionGradingRecord:
    """
    Chế độ A (Upload từ Web UI): Nhận cả Folder ảnh bài làm lộn xộn của 1 học sinh,
    OCR song song, tự sắp xếp lại Câu 1 -> 2 -> 3, ghép mảnh cùng câu và chấm trọn bộ.
    """
    files_data: list[tuple[str, bytes]] = []
    for f in files:
        fname = os.path.basename(f.filename or "image.jpg")
        if not fname.lower().endswith(ALLOWED_IMAGE_EXTS):
            continue
        raw = await f.read()
        if len(raw) == 0:
            continue
        if len(raw) > 50 * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail={"error_code": "E003", "message": f"File {fname} vượt quá giới hạn 50MB."},
            )
        files_data.append((fname, raw))

    if not files_data:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "E001", "message": "Folder tải lên không chứa file ảnh hợp lệ (.jpg, .png, .webp)."},
        )
    if len(files_data) > 30:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "E001", "message": "Một folder học sinh hỗ trợ tối đa 30 ảnh."},
        )

    rubric = _resolve_rubric(exam_id)
    try:
        record = grade_student_folder(files_data=files_data, rubric=rubric, student_name=student_name)
    except OCRLowConfidenceError as exc:
        raise HTTPException(status_code=422, detail={"error_code": "E004", "message": str(exc)})
    except OCRApiError as exc:
        print(f"[Folder Grade OCRApiError] {exc}")
        http_status = 429 if exc.error_code == "E006" else 503
        raise HTTPException(status_code=http_status, detail={"error_code": exc.error_code, "message": str(exc)})

    db.save_submission(record)
    SUBMISSION_DB[record.submission_id] = record
    return record


@router.post("/submissions/grade-folder-path", response_model=SubmissionGradingRecord)
def grade_folder_path(req: FolderPathGradeRequest) -> SubmissionGradingRecord:
    """
    Chế độ A (Đường dẫn Folder local trên máy): Quét trực tiếp thư mục ảnh local,
    OCR song song, tự sắp xếp lại câu lộn xộn và chấm trọn bộ.
    """
    try:
        files_data = load_images_from_local_folder(req.folder_path, max_files=30)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "E002", "message": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error_code": "E001", "message": str(exc)})

    rubric = _resolve_rubric(req.exam_id)
    try:
        record = grade_student_folder(files_data=files_data, rubric=rubric, student_name=req.student_name)
    except OCRLowConfidenceError as exc:
        raise HTTPException(status_code=422, detail={"error_code": "E004", "message": str(exc)})
    except OCRApiError as exc:
        print(f"[Folder Path Grade OCRApiError] {exc}")
        http_status = 429 if exc.error_code == "E006" else 503
        raise HTTPException(status_code=http_status, detail={"error_code": exc.error_code, "message": str(exc)})

    db.save_submission(record)
    SUBMISSION_DB[record.submission_id] = record
    return record


@router.post("/submissions/grade-folder-smart-upload", response_model=SmartFolderBatchResult)
async def grade_folder_smart_upload(
    files: list[UploadFile] = File(...),
    exam_id: str = Form("AUTO"),
) -> SmartFolderBatchResult:
    """
    Chế độ Thông Minh (v0.3.0 — Upload Folder):
    Quét toàn bộ ảnh trong folder, tự xoay ảnh ngang 90°/180°/270°, tự động tách
    nhiều học sinh thành từng thư mục con HS_01/, HS_02/... + xuất 00_BANG_DIEM_CA_LOP.xlsx.
    """
    files_data: list[tuple[str, bytes]] = []
    for f in files:
        fname = os.path.basename(f.filename or "image.jpg")
        if not fname.lower().endswith(ALLOWED_IMAGE_EXTS):
            continue
        raw = await f.read()
        if len(raw) == 0:
            continue
        if len(raw) > 50 * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail={"error_code": "E003", "message": f"File {fname} vượt quá giới hạn 50MB."},
            )
        files_data.append((fname, raw))

    if not files_data:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "E001", "message": "Folder tải lên không chứa file ảnh hợp lệ (.jpg, .png, .webp)."},
        )
    if len(files_data) > 50:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "E001", "message": "Một lần xử lý hỗ trợ tối đa 50 ảnh."},
        )

    rubric = _resolve_rubric(exam_id)
    try:
        batch_res = grade_smart_folder_multistudent(files_data=files_data, rubric=rubric)
    except OCRLowConfidenceError as exc:
        raise HTTPException(status_code=422, detail={"error_code": "E004", "message": str(exc)})
    except OCRApiError as exc:
        print(f"[Smart Folder OCRApiError] {exc}")
        http_status = 429 if exc.error_code == "E006" else 503
        raise HTTPException(status_code=http_status, detail={"error_code": exc.error_code, "message": str(exc)})

    for rec in batch_res.students:
        db.save_submission(rec)
        SUBMISSION_DB[rec.submission_id] = rec
    return batch_res


@router.post("/submissions/grade-folder-smart-path", response_model=SmartFolderBatchResult)
def grade_folder_smart_path(req: FolderPathGradeRequest) -> SmartFolderBatchResult:
    """Chế độ Thông Minh (v0.3.0 — Đường dẫn Folder local): tự xoay ảnh + tách nhiều học sinh + xuất sub-folders."""
    try:
        files_data = load_images_from_local_folder(req.folder_path, max_files=50)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "E002", "message": str(exc)})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error_code": "E001", "message": str(exc)})

    rubric = _resolve_rubric(req.exam_id)
    try:
        batch_res = grade_smart_folder_multistudent(files_data=files_data, rubric=rubric)
    except OCRLowConfidenceError as exc:
        raise HTTPException(status_code=422, detail={"error_code": "E004", "message": str(exc)})
    except OCRApiError as exc:
        print(f"[Smart Folder Path OCRApiError] {exc}")
        http_status = 429 if exc.error_code == "E006" else 503
        raise HTTPException(status_code=http_status, detail={"error_code": exc.error_code, "message": str(exc)})

    for rec in batch_res.students:
        db.save_submission(rec)
        SUBMISSION_DB[rec.submission_id] = rec
    return batch_res


@router.get("/batches/{batch_id}/export-zip")
def export_smart_batch_zip(batch_id: str):
    """Tải trọn bộ cây thư mục đầu ra đã tách theo từng học sinh (HS_01/, HS_02/... + 00_BANG_DIEM_CA_LOP.xlsx)."""
    if not batch_id.startswith("class_") or any(ch in batch_id for ch in ("/", "\\", "..")):
        raise HTTPException(status_code=400, detail={"error_code": "E001", "message": "Mã lô không hợp lệ."})
    data_dir = os.path.dirname(os.path.abspath(settings.UPLOAD_DIR))
    batch_dir = os.path.join(data_dir, "outputs", batch_id)
    if not os.path.isdir(batch_dir):
        raise HTTPException(status_code=404, detail={"error_code": "E002", "message": f"Không tìm thấy thư mục lô {batch_id}."})

    zip_bytes = zip_directory_recursive(batch_dir)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=EduMark_Class_{batch_id}.zip"},
    )


@router.post("/submissions/review", response_model=SubmissionGradingRecord)
def review_submission(req: TeacherReviewRequest) -> SubmissionGradingRecord:
    """Giáo viên xem xét lại điểm số, ghi đè hoặc phê duyệt (Human-in-the-Loop)."""
    record = db.get_submission(req.submission_id) or SUBMISSION_DB.get(req.submission_id)
    if not record:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "E002", "message": "Không tìm thấy bài thi cần duyệt."},
        )

    if req.score_overrides:
        total = 0.0
        for q_res in record.question_results:
            if q_res.question_id in req.score_overrides:
                override_val = float(req.score_overrides[q_res.question_id])
                if override_val < 0.0 or override_val > q_res.max_score:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error_code": "E001",
                            "message": f"Điểm ghi đè {override_val} phải nằm trong khoảng [0, {q_res.max_score}].",
                        },
                    )
                q_res.suggested_score = override_val
                q_res.earned_points = override_val
            total += q_res.suggested_score
        record.final_score = round(total, 2)
        record.ai_total_score = round(total, 2)

    if req.teacher_note is not None:
        record.teacher_note = req.teacher_note

    if req.approve:
        record.status = SubmissionStatus.APPROVED
        record.needs_teacher_review = False
    elif req.score_overrides:
        record.needs_teacher_review = False

    db.save_submission(record)
    SUBMISSION_DB[record.submission_id] = record
    return record


@router.get("/submissions/{submission_id}", response_model=SubmissionGradingRecord)
def get_submission(submission_id: str) -> SubmissionGradingRecord:
    record = db.get_submission(submission_id) or SUBMISSION_DB.get(submission_id)
    if not record:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "E002", "message": "Không tìm thấy bài thi"},
        )
    return record


def _discover_submission_page_files(record: SubmissionGradingRecord) -> list[tuple[int, str, str]]:
    """
    Tìm danh sách file ảnh thực tế đã lưu trên đĩa cho submission:
    Trả về list[(page_no, display_source_file, absolute_disk_path)].
    Hỗ trợ cả bài mới có `page_files` lẫn bài đã chấm trước đó (vd `folder_b4d9c000`).
    """
    folder = os.path.join(settings.UPLOAD_DIR, record.exam_id)
    if not os.path.isdir(folder):
        return []

    orig_names = (
        list(record.folder_reassembly.original_file_order)
        if record.folder_reassembly and record.folder_reassembly.original_file_order
        else []
    )

    found: list[tuple[int, str, str]] = []
    if record.page_files:
        for idx, saved_name in enumerate(record.page_files, start=1):
            full_path = os.path.join(folder, saved_name)
            if os.path.isfile(full_path):
                disp = orig_names[idx - 1] if idx - 1 < len(orig_names) else saved_name
                found.append((idx, disp, full_path))
        if found:
            return found

    # Auto-discover theo quy ước tên file: {sub_id}.jpg, {sub_id}_p2.jpg hoặc {sub_id}_p{N}_{name}
    import re as _re
    matched: list[tuple[int, str, str]] = []
    prefix = f"{record.submission_id}_p"
    single_file = os.path.join(folder, f"{record.submission_id}.jpg")
    if os.path.isfile(single_file):
        matched.append((1, f"{record.submission_id}.jpg", single_file))

    for fname in os.listdir(folder):
        if fname.startswith(prefix):
            m = _re.match(rf"^{_re.escape(record.submission_id)}_p(\d+)(?:_(.+))?\.?(.*)$", fname)
            if m:
                p_no = int(m.group(1))
                disp = orig_names[p_no - 1] if p_no - 1 < len(orig_names) else (m.group(2) or fname)
                matched.append((p_no, disp, os.path.join(folder, fname)))

    matched.sort(key=lambda x: x[0])
    return matched


@router.get("/submissions/{submission_id}/pages")
def list_submission_pages(submission_id: str) -> list[dict]:
    """Liệt kê tất cả các trang ảnh của bài nộp kèm danh sách câu AI nhận diện trong từng trang."""
    record = db.get_submission(submission_id) or SUBMISSION_DB.get(submission_id)
    if not record:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "E002", "message": "Không tìm thấy bài thi"},
        )

    pages = _discover_submission_page_files(record)
    result: list[dict] = []
    for p_no, disp_name, _ in pages:
        # Tìm các câu & số dòng đến từ file ảnh này
        q_hits: list[dict] = []
        for q in record.extracted_questions:
            lines_from_file = [
                l for l in q.lines
                if (l.source_file and l.source_file == disp_name) or (not l.source_file and l.page_no == p_no)
            ]
            if lines_from_file or disp_name in (q.source_files or []):
                q_hits.append({"question_id": q.question_id, "line_count": len(lines_from_file)})
        result.append(
            {
                "page_no": p_no,
                "source_file": disp_name,
                "url": f"/api/v1/submissions/{submission_id}/pages/{p_no}",
                "questions": q_hits,
                "total_lines": sum(h["line_count"] for h in q_hits),
            }
        )
    return result


@router.get("/submissions/{submission_id}/pages/{page_no}")
def get_submission_page_image(submission_id: str, page_no: int):
    """Trả về file ảnh JPEG thực tế của trang thứ page_no để hiển thị đối soát trên UI."""
    record = db.get_submission(submission_id) or SUBMISSION_DB.get(submission_id)
    if not record:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "E002", "message": "Không tìm thấy bài thi"},
        )
    pages = _discover_submission_page_files(record)
    for p_no, _, full_path in pages:
        if p_no == page_no:
            return FileResponse(full_path, media_type="image/jpeg")
    raise HTTPException(
        status_code=404,
        detail={"error_code": "E002", "message": f"Không tìm thấy ảnh trang {page_no} của bài {submission_id}"},
    )


@router.get("/submissions/{submission_id}/export-sorted-folder")
def export_sorted_folder_zip(submission_id: str):
    """
    Xuất và tải về thư mục đầu ra gồm toàn bộ ảnh đã được đổi tên & sắp xếp chuẩn
    theo thứ tự câu (01_cau_1_..., 02_cau_2_...) kèm 00_KET_QUA_CHAM.md & 00_ket_qua.json.
    """
    record = db.get_submission(submission_id) or SUBMISSION_DB.get(submission_id)
    if not record:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "E002", "message": "Không tìm thấy bài thi"},
        )
    pages = _discover_submission_page_files(record)
    if not pages:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "E002", "message": "Không tìm thấy file ảnh gốc của bài thi này trên ổ đĩa."},
        )

    out_dir, sorted_names = build_sorted_output_folder(record, pages)
    if record.folder_reassembly:
        record.folder_reassembly.output_folder_path = out_dir
        record.folder_reassembly.sorted_output_files = sorted_names
        db.save_submission(record)

    from urllib.parse import quote
    zip_bytes = zip_sorted_output_folder(out_dir)
    filename = f"EduMark_Sorted_{submission_id}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "X-Output-Folder-Path": quote(out_dir, safe=":\\/"),
        },
    )


# --- Batch Processing ---
@router.post("/submissions/batch")
async def batch_grade_submissions(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    exam_id: str = Form("TOAN9_01"),
    async_mode: bool = Form(False),
) -> dict:
    """Tải lên ZIP hoặc PDF nhiều trang để chấm hàng loạt (hỗ trợ chạy nền async_mode=true)."""
    from backend.app.services.batch import (
        split_pdf_pages,
        extract_zip_images,
        batch_manager,
        BatchSizeLimitError,
        ZipExtractionError,
        PDFSplitError,
    )

    rubric = db.get_exam(exam_id)
    if not rubric:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "E002", "message": f"Không tìm thấy Barem cho exam_id: {exam_id}"},
        )

    file_bytes = await file.read()
    filename = (file.filename or "").lower()

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

        if async_mode:
            background_tasks.add_task(batch_manager.run_batch_sync, job.job_id, items, rubric)
            return job.to_dict()

        completed_job = batch_manager.run_batch_sync(job.job_id, items, rubric)
        for r in completed_job.results:
            SUBMISSION_DB[r.submission_id] = r
        return completed_job.to_dict()

    except (BatchSizeLimitError, ZipExtractionError, PDFSplitError) as err:
        raise HTTPException(status_code=400, detail={"error_code": "E001", "message": str(err)})


@router.get("/submissions/batch/{job_id}")
def get_batch_job_status(job_id: str) -> dict:
    from backend.app.services.batch import batch_manager

    job_dict = batch_manager.get_job_dict(job_id)
    if not job_dict:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "E002", "message": f"Không tìm thấy batch job với ID: {job_id}"},
        )
    return job_dict


# --- Excel Export ---
@router.get("/exports/excel/{exam_id}")
def export_exam_results_excel(
    exam_id: str,
    format_type: str = "standard",
    approved_only: bool = False,
):
    """Xuất bảng điểm Excel chuẩn hóa (.xlsx) cho VnEdu, SMAS hoặc Standard (chặt theo exam_id)."""
    from backend.app.services.batch import export_to_excel, ExportNoDataError

    records = db.list_submissions(
        exam_id=exam_id,
        status=SubmissionStatus.APPROVED.value if approved_only else None,
        limit=500,
    )

    if not records:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "E001", "message": f"Chưa có bài thi nào thuộc mã đề '{exam_id}' để xuất bảng điểm."},
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
