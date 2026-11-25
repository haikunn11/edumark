import io
import os
import tempfile
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.app.main import app
from backend.app.models.schemas import ExtractedLine, ExtractedQuestion, LineType
from backend.app.services.folder_reassembler import (
    normalize_question_id,
    reassemble_extracted_questions,
)

client = TestClient(app)


def _dummy_page(label: str) -> bytes:
    img = Image.new("RGB", (400, 300), color=(248, 248, 248))
    d = ImageDraw.Draw(img)
    d.text((20, 40), label, fill=(20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def test_normalize_question_id_natural_order():
    """Sắp xếp tự nhiên: Câu 1 -> Câu 1a -> Câu 1b -> Câu 2 -> Câu 10."""
    raw_ids = ["Câu 10", "Bài 2", "Q1b", "cau 1a", "1.", "Bài 3"]
    normalized = [normalize_question_id(r) for r in raw_ids]
    sorted_labels = [label for _, label in sorted(normalized, key=lambda x: x[0])]
    assert sorted_labels == ["Câu 1", "Câu 1a", "Câu 1b", "Câu 2", "Câu 3", "Câu 10"]


def test_reassemble_shuffled_and_split_questions():
    """
    Folder lộn xộn:
      - IMG_09.jpg chứa Câu 3
      - IMG_02.jpg chứa Câu 1 + phần đầu Câu 2
      - IMG_05.jpg chứa phần tiếp Câu 2
    -> Hệ thống phải tự sắp đúng Câu 1 -> Câu 2 -> Câu 3 và ghép nối Câu 2 từ 2 ảnh!
    """
    per_file = [
        (
            "IMG_09.jpg",
            [
                ExtractedQuestion(
                    question_id="Bài 3",
                    question_text="Giải hệ PT",
                    lines=[ExtractedLine(line_no=1, raw_text="x + y = 5", latex="x + y = 5", line_type=LineType.EQUATION)],
                )
            ],
        ),
        (
            "IMG_02.jpg",
            [
                ExtractedQuestion(
                    question_id="Câu 1",
                    student_name_detected="Trần Minh Anh",
                    question_text="Giải PT bậc nhất",
                    lines=[ExtractedLine(line_no=1, raw_text="2x = 6 <=> x = 3", latex="2x = 6 \\Leftrightarrow x = 3", line_type=LineType.EQUATION)],
                ),
                ExtractedQuestion(
                    question_id="Q2",
                    question_text="Rút gọn",
                    lines=[ExtractedLine(line_no=1, raw_text="A = x^2 - 1", latex="A = x^2 - 1", line_type=LineType.EQUATION)],
                ),
            ],
        ),
        (
            "IMG_05.jpg",
            [
                ExtractedQuestion(
                    question_id="Cau 2",
                    lines=[ExtractedLine(line_no=1, raw_text="Vậy A = (x-1)(x+1)", latex="A = (x-1)(x+1)", line_type=LineType.CONCLUSION)],
                )
            ],
        ),
    ]

    merged, report = reassemble_extracted_questions(per_file)
    assert [q.question_id for q in merged] == ["Câu 1", "Câu 2", "Câu 3"]
    assert report.was_reordered is True
    assert report.merged_question_ids == ["Câu 2"]
    assert merged[0].student_name_detected == "Trần Minh Anh"

    # Kiểm tra Câu 2 đã được nối đủ 2 dòng từ 2 file khác nhau và lưu đúng source_file
    q2 = merged[1]
    assert len(q2.lines) == 2
    assert q2.lines[0].source_file == "IMG_02.jpg"
    assert q2.lines[1].source_file == "IMG_05.jpg"
    assert q2.lines[0].line_no == 1 and q2.lines[1].line_no == 2


def test_api_grade_folder_upload():
    """API /submissions/grade-folder-upload nhận nhiều ảnh từ folder và trả đủ report + telemetry."""
    files = [
        ("files", ("IMG_09.jpg", _dummy_page("Cau 3"), "image/jpeg")),
        ("files", ("IMG_02.jpg", _dummy_page("Cau 1"), "image/jpeg")),
        ("files", ("notes.txt", b"ignore me", "text/plain")),
    ]
    res = client.post(
        "/api/v1/submissions/grade-folder-upload",
        files=files,
        data={"exam_id": "AUTO", "student_name": "Nguyễn Thị Lan"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["page_count"] == 2
    assert data["folder_reassembly"] is not None
    assert data["folder_reassembly"]["total_files_scanned"] == 2
    assert data["telemetry"]["total_ms"] >= 0

    # Kiểm tra endpoint liệt kê & tải ảnh từng trang để verify 1-1 trên UI
    sub_id = data["submission_id"]
    res_pages = client.get(f"/api/v1/submissions/{sub_id}/pages")
    assert res_pages.status_code == 200
    pages = res_pages.json()
    assert len(pages) == 2
    assert pages[0]["source_file"] == "IMG_09.jpg"
    res_img = client.get(pages[0]["url"])
    assert res_img.status_code == 200
    assert "image/jpeg" in res_img.headers["content-type"]

    # Kiểm tra thư mục đầu ra đã sắp xếp trên đĩa + tải ZIP
    out_dir = data["folder_reassembly"]["output_folder_path"]
    assert out_dir and os.path.isdir(out_dir)
    assert os.path.isfile(os.path.join(out_dir, "00_KET_QUA_CHAM.md"))
    res_zip = client.get(f"/api/v1/submissions/{sub_id}/export-sorted-folder")
    assert res_zip.status_code == 200
    assert "application/zip" in res_zip.headers["content-type"]


def test_api_grade_folder_local_path():
    """API /submissions/grade-folder-path đọc trực tiếp folder local trên máy Windows."""
    with tempfile.TemporaryDirectory() as tmpdir:
        for fname in ("IMG_08.jpg", "IMG_01.jpg", "IMG_04.jpg"):
            with open(os.path.join(tmpdir, fname), "wb") as f:
                f.write(_dummy_page(fname))

        res = client.post(
            "/api/v1/submissions/grade-folder-path",
            json={"folder_path": tmpdir, "exam_id": "AUTO", "student_name": "Học sinh Folder Local"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["page_count"] == 3
        assert data["folder_reassembly"]["total_files_scanned"] == 3
        assert data["student_name"] == "Học sinh Folder Local"

    # Kiểm tra báo lỗi 404 khi thư mục không tồn tại
    res_bad = client.post(
        "/api/v1/submissions/grade-folder-path",
        json={"folder_path": r"D:\khong_ton_tai_xyz_999", "exam_id": "AUTO"},
    )
    assert res_bad.status_code == 404
