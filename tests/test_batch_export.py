import io
import zipfile
import pytest
import pymupdf
import openpyxl
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.main import app
from backend.app.services.batch import (
    split_pdf_pages,
    extract_zip_images,
    export_to_excel,
    batch_manager,
    PDFSplitError,
    ZipExtractionError,
)
from backend.app.models.schemas import (
    SubmissionGradingRecord,
    QuestionGradingResult,
    CriterionEvaluation,
    SubmissionStatus,
    ExamRubric,
    QuestionRubric,
    RubricCriterion,
)

client = TestClient(app)


def create_sample_pdf_bytes(num_pages: int = 3) -> bytes:
    """Tạo file PDF mẫu có nhiều trang bằng pymupdf."""
    doc = pymupdf.open()
    for i in range(num_pages):
        page = doc.new_page(width=595, height=842)  # A4 size
        page.insert_text((50, 100), f"Bai lam hoc sinh trang {i + 1}", fontsize=14)
        page.insert_text((50, 150), f"2x - {i + 1} = 5", fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def create_sample_zip_bytes(num_images: int = 3) -> bytes:
    """Tạo file ZIP mẫu chứa nhiều ảnh JPEG."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(1, num_images + 1):
            img = Image.new("RGB", (300, 200), color=(240, 240, 240))
            img_buf = io.BytesIO()
            img.save(img_buf, format="JPEG")
            zf.writestr(f"student_{i:02d}.jpg", img_buf.getvalue())
        # Thêm file rác để test bộ lọc
        zf.writestr("__MACOSX/._dummy", b"trash")
        zf.writestr(".DS_Store", b"trash")
    return buf.getvalue()


def create_dummy_records(num_records: int = 3) -> list[SubmissionGradingRecord]:
    """Tạo danh sách kết quả chấm bài mẫu để test xuất Excel."""
    records = []
    for i in range(1, num_records + 1):
        rec = SubmissionGradingRecord(
            submission_id=f"SUB_{i:03d}",
            exam_id="TOAN9_01",
            student_name=f"Học sinh {i}",
            ai_total_score=1.5,
            final_score=1.75 if i == 1 else 1.5,
            status=SubmissionStatus.APPROVED if i == 1 else SubmissionStatus.GRADED_DRAFT,
            teacher_note="Làm bài tốt" if i == 1 else "",
            question_results=[
                QuestionGradingResult(
                    question_id="Q1",
                    suggested_score=1.5,
                    earned_points=1.5,
                    max_score=2.0,
                    error_propagation_detected=(i == 2),
                    criteria_evaluations=[
                        CriterionEvaluation(criterion_id="Q1_C1_DKXD", score=0.5, max_score=0.5, status="passed", feedback="Đúng"),
                        CriterionEvaluation(criterion_id="Q1_C2_BIEN_DOI", score=1.0, max_score=1.0, status="passed", feedback="Đúng"),
                    ],
                )
            ],
        )
        records.append(rec)
    return records


def test_wp15_pdf_page_splitter():
    """WP-15: Tách file PDF 3 trang thành 3 ảnh độc lập."""
    pdf_bytes = create_sample_pdf_bytes(num_pages=3)
    pages = split_pdf_pages(pdf_bytes, max_pages=50)
    assert len(pages) == 3
    assert pages[0][0] == 1
    assert len(pages[0][1]) > 0  # bytes ảnh JPEG


def test_wp14_zip_image_extraction():
    """WP-14: Giải nén ZIP chứa 3 ảnh, tự động lọc file rác và sắp xếp theo tên."""
    zip_bytes = create_sample_zip_bytes(num_images=3)
    images = extract_zip_images(zip_bytes, max_files=50)
    assert len(images) == 3
    assert images[0][0] == "student_01.jpg"
    assert images[1][0] == "student_02.jpg"
    assert images[2][0] == "student_03.jpg"


def test_wp16_export_excel_standard():
    """WP-16: Xuất bảng điểm chuẩn Standard có 2 sheets: Tổng hợp & Chi tiết."""
    records = create_dummy_records(3)
    excel_bytes = export_to_excel("TOAN9_01", records, format_type="standard")
    assert len(excel_bytes) > 0

    # Đọc lại workbook bằng openpyxl để kiểm tra cấu trúc
    wb = openpyxl.load_workbook(io.BytesIO(excel_bytes))
    assert "Bảng Điểm Tổng Hợp" in wb.sheetnames
    assert "Chi Tiết Tiêu Chí Chấm" in wb.sheetnames

    ws1 = wb["Bảng Điểm Tổng Hợp"]
    assert ws1.cell(row=1, column=1).value == "STT"
    assert ws1.cell(row=2, column=3).value == "Học sinh 1"


def test_wp16_export_excel_vnedu_and_smas():
    """WP-16: Xuất bảng điểm tương thích VnEdu và SMAS."""
    records = create_dummy_records(2)

    # Test VnEdu
    vnedu_bytes = export_to_excel("TOAN9_01", records, format_type="vnedu")
    wb_vnedu = openpyxl.load_workbook(io.BytesIO(vnedu_bytes))
    assert "Diem_VnEdu" in wb_vnedu.sheetnames

    # Test SMAS
    smas_bytes = export_to_excel("TOAN9_01", records, format_type="smas")
    wb_smas = openpyxl.load_workbook(io.BytesIO(smas_bytes))
    assert "Diem_SMAS" in wb_smas.sheetnames


def test_api_batch_upload_and_status():
    """WP-17 & WP-18: Kiểm thử API /submissions/batch với file ZIP và kiểm tra trạng thái job."""
    zip_bytes = create_sample_zip_bytes(num_images=2)
    files = {"file": ("batch_exam.zip", zip_bytes, "application/zip")}
    data = {"exam_id": "TOAN9_01"}

    res = client.post("/api/v1/submissions/batch", files=files, data=data)
    assert res.status_code == 200
    batch_res = res.json()
    assert "job_id" in batch_res
    assert batch_res["total_submissions"] == 2
    assert batch_res["completed_submissions"] == 2
    assert batch_res["status"] == "completed"

    job_id = batch_res["job_id"]

    # Tra cứu tiến độ job
    res_status = client.get(f"/api/v1/submissions/batch/{job_id}")
    assert res_status.status_code == 200
    assert res_status.json()["job_id"] == job_id


def test_api_export_excel_endpoint():
    """WP-16: Kiểm thử tải file Excel qua API endpoint /exports/excel/{exam_id}."""
    # Tự tạo bài nộp (v0.7.1): trước đây test dựa vào test batch chạy trước cùng tiến trình ⇒ chạy đơn lẻ
    # hoặc bị pytest-xdist chia sang worker khác thì endpoint trả 400 "chưa có bài nộp".
    seed = client.post(
        "/api/v1/submissions/batch",
        files={"file": ("batch_exam.zip", create_sample_zip_bytes(num_images=1), "application/zip")},
        data={"exam_id": "TOAN9_01"},
    )
    assert seed.status_code == 200

    res = client.get("/api/v1/exports/excel/TOAN9_01?format_type=standard")
    assert res.status_code == 200
    assert "application/vnd.openxmlformats" in res.headers["content-type"]
    assert len(res.content) > 0
