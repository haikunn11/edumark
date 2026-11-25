import io
import os
import zipfile
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.app.main import app
from backend.app.models.schemas import ExtractedLine, ExtractedQuestion, ImageQualityReport, LineType
from backend.app.services.preprocessor import detect_sideways_orientation_cw
from backend.app.services.student_clusterer import (
    PageProfile,
    _norm_token,
    _rotate_jpeg_cw,
    cluster_pages_into_students,
)

client = TestClient(app)


def _make_img(w: int = 400, h: int = 300, text: str = "Test") -> bytes:
    img = Image.new("RGB", (w, h), color=(248, 248, 248))
    ImageDraw.Draw(img).text((20, 30), text, fill=(20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _profile(idx: int, fname: str, qids: list[str], brand: str = "", name: str = "") -> PageProfile:
    qs = [
        ExtractedQuestion(
            question_id=qid,
            student_name_detected=name,
            paper_brand_detected=brand,
            lines=[ExtractedLine(line_no=1, raw_text=f"Bai lam {qid}", latex=f"x = {idx}", line_type=LineType.EQUATION)],
        )
        for qid in qids
    ]
    return PageProfile(
        page_index=idx,
        filename=fname,
        raw_bytes=b"x",
        proc_bytes=b"x",
        saved_filename=fname,
        saved_disk_path=fname,
        quality=ImageQualityReport(),
        questions=qs,
        student_name=name,
        paper_brand=brand,
    )


def test_cluster_separates_5_students_and_pairs_same_paper_brand():
    """
    Mô phỏng đúng 6 ảnh thật của người dùng:
    - p1 (HAI TIEN): Bài 3, Bài 4
    - p2 (KOKUYO): Bài 2c, Bài 3
    - p3 (KLONG): Bài 2a, 2b, 2c, 3, 4
    - p4 (ClassMate): Bài 2a, 2b, 2c
    - p5 (HAI TIEN): Bài 2a, 2b, 2c  -> PHẢI ghép đúng vào cùng học sinh với p1 (HAI TIEN)!
    - p6 (THIEN LONG): Bài 2c, Bài 3, Bài 4
    => Kết quả PHẢI tách đúng 5 học sinh độc lập, HS HAI TIEN có đủ 2 trang!
    """
    pages = [
        _profile(1, "8a90.jpg", ["Bài 3", "Bài 4"], brand="HAI TIEN"),
        _profile(2, "1387.jpg", ["Bài 2c", "Bài 3"], brand="KOKUYO"),
        _profile(3, "95cf.jpg", ["Bài 2a", "Bài 2b", "Bài 2c", "Bài 3", "Bài 4"], brand="KLONG"),
        _profile(4, "a3d6.jpg", ["Bài 2a", "Bài 2b", "Bài 2c"], brand="ClassMate"),
        _profile(5, "dff7.jpg", ["Bài 2a", "Bài 2b", "Bài 2c"], brand="HAI TIEN"),
        _profile(6, "f5fa.jpg", ["Bài 2c", "Bài 3", "Bài 4"], brand="THIEN LONG"),
    ]
    clusters = cluster_pages_into_students(pages)
    assert len(clusters) == 5

    hai_tien_cluster = next(cl for cl in clusters if "HAI TIEN" in cl[0])
    assert len(hai_tien_cluster[2]) == 2
    assert {p.filename for p in hai_tien_cluster[2]} == {"8a90.jpg", "dff7.jpg"}
    assert "cùng loại giấy" in hai_tien_cluster[1].lower()
    assert "họ tên" not in hai_tien_cluster[1].lower()


def test_norm_token_treats_empty_and_placeholders_as_empty():
    """Chuỗi rỗng hoặc placeholder không được trả về token giả gây ghép nhầm theo Họ tên."""
    assert _norm_token("") == ""
    assert _norm_token("   ") == ""
    assert _norm_token("Không rõ") == ""
    assert _norm_token("Nguyen Van A") == "nguyenvana"


def test_opencv_detect_sideways_orientation_cw():
    """OpenCV local nhận diện chính xác ảnh kẻ ngang chụp thẳng (0°) vs chụp ngang (270° / 90°)."""
    # 1. Ảnh giấy kẻ ngang đứng thẳng (600x800)
    upright = Image.new("RGB", (600, 800), (245, 245, 245))
    du = ImageDraw.Draw(upright)
    for y in range(60, 760, 28):
        du.line([(40, y), (560, y)], fill=(130, 140, 160), width=1)
    # Mực viết tay tập trung ở nửa trên-trái
    for y in range(80, 340, 28):
        du.rectangle([(70, y - 10), (360, y - 2)], fill=(25, 40, 140))
    assert detect_sideways_orientation_cw(upright) == 0

    # 2. Xoay 90° CW -> cần xoay 270° CW để đứng thẳng lại
    sideways_cw = upright.rotate(-90, expand=True)
    assert detect_sideways_orientation_cw(sideways_cw) == 270

    # 3. Xoay 90° CCW -> cần xoay 90° CW để đứng thẳng lại
    sideways_ccw = upright.rotate(90, expand=True)
    assert detect_sideways_orientation_cw(sideways_ccw) == 90


def test_cluster_hard_conflict_same_brand_overlapping_questions():
    """Nếu 2 em cùng dùng giấy HAI TIEN nhưng CẢ HAI đều làm Bài 2a -> CẤM gộp nhầm!"""
    pages = [
        _profile(1, "hs1.jpg", ["Bài 2a", "Bài 2b"], brand="HAI TIEN"),
        _profile(2, "hs2.jpg", ["Bài 2a", "Bài 2b"], brand="HAI TIEN"),
    ]
    clusters = cluster_pages_into_students(pages)
    assert len(clusters) == 2


def test_rotate_jpeg_cw_90_turns_landscape_to_portrait():
    """Xoay 90 độ theo chiều kim đồng hồ biến ảnh nằm ngang 600x400 thành thẳng đứng 400x600."""
    raw = _make_img(600, 400)
    rot = _rotate_jpeg_cw(raw, 90)
    im = Image.open(io.BytesIO(rot))
    assert im.size == (400, 600)


def test_api_smart_folder_upload_and_export_class_zip():
    """API /submissions/grade-folder-smart-upload tách học sinh, tạo cây thư mục + Excel + ZIP."""
    files = [
        ("files", ("hs_a.jpg", _make_img(400, 300, "HS A"), "image/jpeg")),
        ("files", ("hs_b.jpg", _make_img(400, 300, "HS B"), "image/jpeg")),
    ]
    res = client.post(
        "/api/v1/submissions/grade-folder-smart-upload",
        files=files,
        data={"exam_id": "AUTO"},
    )
    assert res.status_code == 200
    data = res.json()
    # Offline mock cả 2 ảnh đều ra Q1 -> Hard Conflict -> tách thành 2 học sinh độc lập!
    assert data["total_images_scanned"] == 2
    assert data["total_students_detected"] == 2
    assert os.path.isdir(data["output_root_folder"])
    assert os.path.isfile(data["excel_file_path"])

    # Tải trọn bộ ZIP cây thư mục lớp
    batch_id = data["batch_id"]
    res_zip = client.get(f"/api/v1/batches/{batch_id}/export-zip")
    assert res_zip.status_code == 200
    assert "application/zip" in res_zip.headers["content-type"]

    zf = zipfile.ZipFile(io.BytesIO(res_zip.content))
    names = zf.namelist()
    assert "00_BANG_DIEM_CA_LOP.xlsx" in names
    assert "00_TONG_HOP_PHAN_LOAI.md" in names
    assert any(n.startswith("HS_01_") for n in names)
    assert any(n.startswith("HS_02_") for n in names)
