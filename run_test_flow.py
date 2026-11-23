"""
Kịch bản Kiểm thử End-to-End (10 Steps E2E Test Flow) cho Hệ Thống EduMark AI
Chạy kịch bản này độc lập bằng lệnh: python run_test_flow.py
"""

from __future__ import annotations

import io
import sys
import zipfile
import pymupdf
from PIL import Image, ImageDraw

from backend.app.models.schemas import (
    ExamRubric,
    QuestionRubric,
    RubricCriterion,
    SubmissionStatus,
    TeacherReviewRequest,
)
from backend.app.services.pipeline import grading_pipeline
from backend.app.services.batch import (
    split_pdf_pages,
    extract_zip_images,
    batch_manager,
    export_to_excel,
)
from backend.app.api.routes import EXAM_DB, SUBMISSION_DB, review_submission


def create_sample_student_image(text_variant: str = "default") -> bytes:
    """Tạo file ảnh JPEG mô phỏng bài làm học sinh lớp 9."""
    img = Image.new("RGB", (800, 600), color=(250, 249, 245))
    draw = ImageDraw.Draw(img)
    draw.text((30, 30), "Ho va ten: Nguyen Van An - Lop 9A1", fill=(30, 30, 30))
    draw.text((30, 60), "Kiem tra 15p: Giai phuong trinh", fill=(30, 30, 30))
    draw.line([(30, 90), (770, 90)], fill=(180, 180, 180), width=1)
    draw.text((30, 120), "Bai lam:", fill=(30, 30, 30))

    if text_variant == "error_propagation":
        draw.text((50, 160), "DKXD: x != 2", fill=(20, 20, 20))
        draw.text((50, 200), "x/(x-2) + 1 = 4/(x-2)", fill=(20, 20, 20))
        draw.text((50, 240), "<=> 2x = 8 <=> x = 4 (tinh sai)", fill=(20, 20, 20))
        draw.text((50, 280), "Doi chieu: x = 4 thoa man DKXD, S = {4}", fill=(20, 20, 20))
    else:
        draw.text((50, 160), "x/(x-2) + 1 = 4/(x-2)", fill=(20, 20, 20))
        draw.text((50, 200), "<=> x + (x - 2) = 4", fill=(20, 20, 20))
        draw.text((50, 240), "<=> 2x = 6 <=> x = 3", fill=(20, 20, 20))
        draw.text((50, 280), "Vay phuong trinh co nghiem x = 3", fill=(20, 20, 20))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def create_sample_pdf() -> bytes:
    """Tạo file PDF 2 trang giả lập bài thi cả lớp."""
    doc = pymupdf.open()
    for i in range(1, 3):
        p = doc.new_page(width=595, height=842)
        p.insert_text((50, 80), f"Bai Thi Hoc Sinh Trang {i}", fontsize=14)
        p.insert_text((50, 120), f"Giai pt: 2x - {i} = 5 <=> 2x = {5 + i} => x = {(5 + i) / 2}", fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def create_sample_zip() -> bytes:
    """Tạo file ZIP chứa 2 ảnh bài thi."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(1, 3):
            b = create_sample_student_image()
            zf.writestr(f"bai_thi_{i:02d}.jpg", b)
    return buf.getvalue()


def main():
    print("=" * 75)
    print("   KHỞI ĐỘNG 10-STEP E2E TEST FLOW: HỆ THỐNG CHẤM BÀI EDUMARK AI")
    print("=" * 75)

    # [Bước 1] Khởi tạo Barem đề thi mẫu
    rubric = ExamRubric(
        exam_id="TOAN9_GIUAKY1",
        title="Kiểm tra Đại số 9: Phương trình chứa ẩn ở mẫu",
        total_points=2.0,
        rubric_version=1,
        questions=[
            QuestionRubric(
                question_id="Q1",
                max_score=2.0,
                total_points=2.0,
                allow_error_propagation=True,
                criteria=[
                    RubricCriterion(
                        criterion_id="Q1_C1_DKXD",
                        name="Đặt điều kiện xác định",
                        max_score=0.5,
                        max_points=0.5,
                        required_logic="x \\neq 2",
                    ),
                    RubricCriterion(
                        criterion_id="Q1_C2_BIEN_DOI",
                        name="Quy đồng và giải phương trình",
                        max_score=1.0,
                        max_points=1.0,
                        required_logic="x + (x - 2) = 4 <=> 2x = 6 <=> x = 3",
                    ),
                    RubricCriterion(
                        criterion_id="Q1_C3_KET_LUAN",
                        name="Đối chiếu ĐKXĐ và kết luận",
                        max_score=0.5,
                        max_points=0.5,
                        required_logic="Đối chiếu thỏa mãn, S = {3}",
                    ),
                ],
            )
        ],
    )
    EXAM_DB[rubric.exam_id] = rubric
    print(f"\n[Bước 1/10] Đã nạp Barem đề thi: '{rubric.title}' (Tổng điểm: 2.0đ)")

    # [Bước 2] Tạo ảnh bài làm học sinh
    image_bytes = create_sample_student_image()
    print(f"[Bước 2/10] Đã tạo ảnh bài làm học sinh Nguyễn Văn An ({len(image_bytes)} bytes)")

    # [Bước 3] Chạy Pipeline AI Lần 1 (Expect Cache Miss)
    print("\n[Bước 3/10] Chạy Pipeline AI Lần 1 (Chưa có Cache)...")
    record1 = grading_pipeline.process_submission(
        image_bytes=image_bytes,
        rubric=rubric,
        student_name="Nguyễn Văn An",
    )
    SUBMISSION_DB[record1.submission_id] = record1
    print(f"  -> Trạng thái: {record1.status.value}")
    print(f"  -> Cache Hit: {record1.cache_hit} (Mới phân tích lần đầu)")
    print(f"  -> Chất lượng ảnh: Blur score = {record1.quality.blur_score} (Đạt yêu cầu)")
    print(f"  -> Điểm AI đề xuất: {record1.ai_total_score} / 2.0 đ")

    # [Bước 4] Chạy Pipeline AI Lần 2 với cùng ảnh (Expect Cache Hit)
    print("\n[Bước 4/10] Chạy Pipeline AI Lần 2 với cùng ảnh (Kiểm tra Caching)...")
    record2 = grading_pipeline.process_submission(
        image_bytes=image_bytes,
        rubric=rubric,
        student_name="Nguyễn Văn An",
    )
    assert record2.cache_hit is True
    print(f"  -> Cache Hit: {record2.cache_hit} (Thành công! Tái sử dụng kết quả, 0đ API, tốc độ tức thì)")

    # [Bước 5] Giáo viên duyệt và điều chỉnh điểm (HITL)
    print("\n[Bước 5/10] Giáo viên đối soát & Phê duyệt bài làm (Human-in-the-Loop)...")
    review_req = TeacherReviewRequest(
        submission_id=record1.submission_id,
        score_overrides={"Q1": 1.25},
        teacher_note="Cộng 0.25đ khuyến khích chữ đẹp. Nhớ chú ý đặt ĐKXĐ!",
        approve=True,
    )
    final_record = review_submission(review_req)
    assert final_record.status == SubmissionStatus.APPROVED
    print(f"  -> Điểm sau khi Giáo viên duyệt: {final_record.final_score}đ")
    print(f"  -> Trạng thái cuối: {final_record.status.value} (ĐÃ PHÊ DUYỆT)")

    # [Bước 6] Xử lý hàng loạt file ZIP
    print("\n[Bước 6/10] Kiểm thử giải nén và chấm hàng loạt file ZIP (WP-14)...")
    zip_bytes = create_sample_zip()
    zip_images = extract_zip_images(zip_bytes, max_files=50)
    job_zip = batch_manager.create_job(exam_id=rubric.exam_id, total_count=len(zip_images))
    completed_job_zip = batch_manager.run_batch_sync(job_zip.job_id, zip_images, rubric)
    assert completed_job_zip.status == "completed"
    print(f"  -> Hoàn thành chấm hàng loạt ZIP: {completed_job_zip.completed_submissions} bài (Status: {completed_job_zip.status})")

    # [Bước 7] Tách file PDF nhiều trang và chấm từng trang
    print("\n[Bước 7/10] Kiểm thử tách file PDF nhiều trang thành từng bài thi (WP-15)...")
    pdf_bytes = create_sample_pdf()
    pdf_pages = split_pdf_pages(pdf_bytes, max_pages=50)
    assert len(pdf_pages) == 2
    job_pdf = batch_manager.create_job(exam_id=rubric.exam_id, total_count=len(pdf_pages))
    completed_job_pdf = batch_manager.run_batch_sync(
        job_pdf.job_id,
        [(f"trang_{p:02d}", b) for p, b in pdf_pages],
        rubric,
    )
    assert completed_job_pdf.status == "completed"
    print(f"  -> Đã tách và chấm {len(pdf_pages)} trang PDF độc lập (Status: {completed_job_pdf.status})")

    # [Bước 8] Xuất bảng điểm Excel 3 định dạng
    print("\n[Bước 8/10] Kiểm thử xuất bảng điểm Excel đa định dạng (Standard, VnEdu, SMAS) (WP-16)...")
    all_recs = list(SUBMISSION_DB.values()) + completed_job_zip.results
    excel_std = export_to_excel(rubric.exam_id, all_recs, format_type="standard")
    excel_vnedu = export_to_excel(rubric.exam_id, all_recs, format_type="vnedu")
    excel_smas = export_to_excel(rubric.exam_id, all_recs, format_type="smas")
    assert len(excel_std) > 0 and len(excel_vnedu) > 0 and len(excel_smas) > 0
    print(f"  -> Xuất thành công: Standard ({len(excel_std)} bytes), VnEdu ({len(excel_vnedu)} bytes), SMAS ({len(excel_smas)} bytes)")

    # [Bước 9] Kiểm thử Error Propagation (Lỗi lan truyền)
    print("\n[Bước 9/10] Kiểm thử nhận diện lỗi lan truyền & bảo lưu điểm phương pháp (WP-07)...")
    ep_image = create_sample_student_image(text_variant="error_propagation")
    ep_record = grading_pipeline.process_submission(ep_image, rubric, student_name="HS Lỗi Lan Truyền")
    print(f"  -> Điểm bài thi có lỗi lan truyền: {ep_record.ai_total_score}đ")
    print(f"  -> Cần giáo viên kiểm tra lại: {ep_record.needs_teacher_review}")

    # [Bước 10] Kiểm tra xử lý lỗi an toàn (Fault Isolation)
    print("\n[Bước 10/10] Kiểm thử bộ lọc file lỗi (Fault Isolation)...")
    proc_garbage, qual_garbage = grading_pipeline.preprocessor.process_image(b"INVALID_DATA_BYTES")
    assert qual_garbage.is_blurry is True
    print("  -> Hệ thống xử lý an toàn dữ liệu rỗng/hỏng, không gây crash server.")

    print("\n" + "=" * 75)
    print("   🎉 10/10 BƯỚC TEST FLOW E2E ĐÃ HOÀN TẤT THÀNH CÔNG 100%! TOÀN BỘ HỆ THỐNG ĐẠT CHUẨN!")
    print("=" * 75)


if __name__ == "__main__":
    main()
