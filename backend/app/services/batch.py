from __future__ import annotations

import io
import os
import re
import uuid
import zipfile
from datetime import datetime, timezone
from typing import List, Literal, Tuple
import pymupdf
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from backend.app.models.schemas import (
    ExamRubric,
    SubmissionGradingRecord,
    SubmissionStatus,
    GradingResult,
)
from backend.app.services.pipeline import grading_pipeline


class BatchSizeLimitError(Exception):
    """Vượt quá giới hạn tối đa 50 bài thi trong 1 lần upload."""
    pass


class ZipExtractionError(Exception):
    """File ZIP bị lỗi, mã hóa hoặc không hợp lệ."""
    pass


class PDFSplitError(Exception):
    """File PDF bị lỗi, có mật khẩu hoặc không đọc được."""
    pass


class ExportNoDataError(Exception):
    """Không có bài thi nào đã được phê duyệt để xuất bảng điểm."""
    pass


def split_pdf_pages(pdf_bytes: bytes, max_pages: int = 50) -> list[tuple[int, bytes]]:
    """
    Tách file PDF nhiều trang thành từng ảnh bài thi riêng biệt (WP-15).
    Mỗi trang = 1 bài thi học sinh. Render ở độ phân giải 200-300 DPI.
    """
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise PDFSplitError(f"Không thể mở file PDF: {exc}")

    total_pages = len(doc)
    if total_pages == 0:
        raise PDFSplitError("File PDF không có trang nào.")

    if total_pages > max_pages:
        raise BatchSizeLimitError(f"File PDF có {total_pages} trang, vượt quá giới hạn tối đa {max_pages} bài/lần.")

    results: list[tuple[int, bytes]] = []
    # 200 DPI matrix (200 / 72 ≈ 2.77)
    zoom = 200.0 / 72.0
    mat = pymupdf.Matrix(zoom, zoom)

    for page_num in range(total_pages):
        page = doc[page_num]
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("jpeg")
        results.append((page_num + 1, img_bytes))

    doc.close()
    return results


def extract_zip_images(zip_bytes: bytes, max_files: int = 50) -> list[tuple[str, bytes]]:
    """
    Giải nén file ZIP chứa nhiều ảnh bài thi (WP-14).
    Tự động lọc các file rác (.DS_Store, __MACOSX) và sắp xếp tự nhiên theo tên.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except Exception as exc:
        raise ZipExtractionError(f"Không thể đọc file ZIP: {exc}")

    valid_extensions = (".jpg", ".jpeg", ".png")
    image_entries: list[str] = []

    for name in zf.namelist():
        if name.startswith("__MACOSX") or name.endswith("/"):
            continue
        lower_name = name.lower()
        if lower_name.endswith(valid_extensions):
            image_entries.append(name)

    if not image_entries:
        raise ZipExtractionError("File ZIP không chứa bất kỳ ảnh JPEG/PNG nào hợp lệ.")

    if len(image_entries) > max_files:
        raise BatchSizeLimitError(f"File ZIP chứa {len(image_entries)} ảnh, vượt quá giới hạn tối đa {max_files} bài/lần.")

    # Sắp xếp tự nhiên (1.jpg, 2.jpg, 10.jpg)
    def natural_sort_key(s: str):
        return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]

    image_entries.sort(key=natural_sort_key)

    results: list[tuple[str, bytes]] = []
    for name in image_entries:
        raw_bytes = zf.read(name)
        base_name = os.path.basename(name)
        results.append((base_name, raw_bytes))

    return results


class BatchJob:
    """Đối tượng theo dõi trạng thái công việc chấm hàng loạt (WP-17)."""

    def __init__(self, job_id: str, exam_id: str, total_submissions: int) -> None:
        self.job_id = job_id
        self.exam_id = exam_id
        self.status: Literal["queued", "processing", "completed", "failed"] = "queued"
        self.total_submissions = total_submissions
        self.completed_submissions = 0
        self.failed_submissions = 0
        self.results: list[SubmissionGradingRecord] = []
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.completed_at: str | None = None
        self.error_message: str | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "exam_id": self.exam_id,
            "status": self.status,
            "total_submissions": self.total_submissions,
            "completed_submissions": self.completed_submissions,
            "failed_submissions": self.failed_submissions,
            "progress_percentage": round((self.completed_submissions / max(1, self.total_submissions)) * 100, 1),
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error_message": self.error_message,
            "results_count": len(self.results),
        }


class BatchJobManager:
    """Quản trị hàng đợi các tác vụ xử lý bài thi hàng loạt."""

    def __init__(self) -> None:
        self._jobs: dict[str, BatchJob] = {}

    def create_job(self, exam_id: str, total_count: int) -> BatchJob:
        job_id = f"batch_{uuid.uuid4().hex[:8]}"
        job = BatchJob(job_id=job_id, exam_id=exam_id, total_submissions=total_count)
        self._jobs[job_id] = job
        return job

    def get_job(self, job_id: str) -> BatchJob | None:
        return self._jobs.get(job_id)

    def run_batch_sync(
        self,
        job_id: str,
        items: list[tuple[str, bytes]],
        rubric: ExamRubric,
    ) -> BatchJob:
        job = self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} không tồn tại.")

        job.status = "processing"
        for label, img_bytes in items:
            try:
                sub_id = f"{job.exam_id}_{label}"
                rec = grading_pipeline.process_submission(
                    image_bytes=img_bytes,
                    rubric=rubric,
                    student_name=f"Học sinh ({label})",
                    submission_id=sub_id,
                )
                job.results.append(rec)
                job.completed_submissions += 1
            except Exception as exc:
                job.failed_submissions += 1

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc).isoformat()
        return job


batch_manager = BatchJobManager()


def export_to_excel(
    exam_id: str,
    records: list[SubmissionGradingRecord],
    format_type: Literal["standard", "vnedu", "smas"] = "standard",
) -> bytes:
    """
    Xuất bảng điểm Excel chuẩn hóa (.xlsx) bằng openpyxl (WP-16).
    Hỗ trợ 3 format:
      1. 'standard': Bảng điểm đầy đủ có Sheet 1 (Tổng kết) & Sheet 2 (Chi tiết bước giải).
      2. 'vnedu': Định dạng nhập điểm tương thích VnEdu.
      3. 'smas': Định dạng nhập điểm tương thích SMAS Viettel.
    """
    if not records:
        raise ExportNoDataError("Không có bản ghi điểm nào để xuất Excel.")

    wb = openpyxl.Workbook()
    # Font & Color palettes
    header_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
    sub_header_fill = PatternFill(start_color="312E81", end_color="312E81", fill_type="solid")
    zebra_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    bold_font = Font(name="Arial", size=10, bold=True)
    normal_font = Font(name="Arial", size=10)
    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1'),
    )

    if format_type == "vnedu":
        ws = wb.active
        ws.title = "Diem_VnEdu"
        headers = ["STT", "Mã học sinh", "Họ và tên", "Điểm kiểm tra", "Trạng thái", "Ghi chú"]
        ws.append(headers)
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for idx, rec in enumerate(records, start=1):
            score = rec.final_score if rec.final_score is not None else rec.ai_total_score
            row = [idx, f"HS_{idx:03d}", rec.student_name, score, rec.status, rec.teacher_note or ""]
            ws.append(row)
            for c_idx in range(1, len(row) + 1):
                cell = ws.cell(row=idx + 1, column=c_idx)
                cell.font = normal_font
                cell.border = thin_border
                if idx % 2 == 0:
                    cell.fill = zebra_fill

    elif format_type == "smas":
        ws = wb.active
        ws.title = "Diem_SMAS"
        headers = ["STT", "MaHS", "HoTen", "DiemKT", "GhiChu"]
        ws.append(headers)
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for idx, rec in enumerate(records, start=1):
            score = rec.final_score if rec.final_score is not None else rec.ai_total_score
            row = [idx, f"SMAS_{idx:03d}", rec.student_name, score, rec.teacher_note or ""]
            ws.append(row)
            for c_idx in range(1, len(row) + 1):
                cell = ws.cell(row=idx + 1, column=c_idx)
                cell.font = normal_font
                cell.border = thin_border

    else:
        # Standard: 2 Sheets
        ws1 = wb.active
        ws1.title = "Bảng Điểm Tổng Hợp"
        headers1 = [
            "STT", "Mã Bài Thi", "Tên Học Sinh", "Điểm AI", "Điểm Chốt",
            "Trạng Thái", "Lỗi Lan Truyền", "Cache Hit", "Ghi Chú Giáo Viên"
        ]
        ws1.append(headers1)
        for col_idx in range(1, len(headers1) + 1):
            cell = ws1.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        for idx, rec in enumerate(records, start=1):
            has_ep = any(q.error_propagation_detected for q in rec.question_results)
            row = [
                idx,
                rec.submission_id,
                rec.student_name,
                rec.ai_total_score,
                rec.final_score if rec.final_score is not None else rec.ai_total_score,
                rec.status,
                "Có (Bảo lưu)" if has_ep else "Không",
                "Hit" if rec.cache_hit else "Miss",
                rec.teacher_note or "",
            ]
            ws1.append(row)
            for c_idx in range(1, len(row) + 1):
                cell = ws1.cell(row=idx + 1, column=c_idx)
                cell.font = normal_font
                cell.border = thin_border
                if idx % 2 == 0:
                    cell.fill = zebra_fill

        # Sheet 2: Chi tiết từng bước giải
        ws2 = wb.create_sheet(title="Chi Tiết Tiêu Chí Chấm")
        headers2 = ["STT", "Mã Bài", "Câu Hỏi", "Mã Tiêu Chí", "Điểm Đạt", "Điểm Tối Đa", "Trạng Thái", "Nhận Xét Chi Tiết"]
        ws2.append(headers2)
        for col_idx in range(1, len(headers2) + 1):
            cell = ws2.cell(row=1, column=col_idx)
            cell.fill = sub_header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")

        row_counter = 2
        for idx, rec in enumerate(records, start=1):
            for q in rec.question_results:
                for c in (q.criteria_evaluations or q.criteria_evaluation):
                    r_data = [
                        idx,
                        rec.submission_id,
                        q.question_id,
                        c.criterion_id,
                        c.earned_points or c.score,
                        c.max_points or c.max_score,
                        c.status,
                        c.feedback,
                    ]
                    ws2.append(r_data)
                    for c_idx in range(1, len(r_data) + 1):
                        cell = ws2.cell(row=row_counter, column=c_idx)
                        cell.font = normal_font
                        cell.border = thin_border
                    row_counter += 1

    # Tự động điều chỉnh độ rộng cột cho đẹp mắt
    for sheet in wb.worksheets:
        for col in sheet.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            sheet.column_dimensions[col_letter].width = max(max_len + 3, 10)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
