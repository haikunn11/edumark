import io
import openpyxl
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.app.core.database import db
from backend.app.main import app

client = TestClient(app)


def _sample_img(text: str = "x = 3") -> bytes:
    img = Image.new("RGB", (400, 300), color=(250, 250, 250))
    ImageDraw.Draw(img).text((20, 40), text, fill=(10, 10, 10))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def test_gradebook_create_and_fill_exact_row_col():
    """Tạo bảng điểm mới có danh sách HS -> chấm 1 bài (nhiều trang) -> fill vào đúng hàng & tải .xlsx."""
    res_gb = client.post(
        "/api/v1/gradebooks/create",
        json={
            "name": "Bảng điểm Lớp 9A",
            "exam_id": "TOAN9_01",
            "student_names": ["Trần Thị Bình", "Nguyễn Văn An"],
        },
    )
    assert res_gb.status_code == 200
    gb = res_gb.json()
    gb_id = gb["gradebook_id"]
    assert gb["total_rows"] == 2
    assert any(c["name"] == "Q1" for c in gb["columns"])

    # Chấm 1 bài nhiều trang cho Trần Thị Bình
    res_sub = client.post(
        "/api/v1/submissions/grade-multipage",
        files=[("files", ("p1.jpg", _sample_img("Binh p1"), "image/jpeg"))],
        data={"exam_id": "TOAN9_01", "student_name": "Trần Thị Bình"},
    )
    assert res_sub.status_code == 200
    sub = res_sub.json()

    # Fill vào đúng dòng #2 (Trần Thị Bình)
    res_fill = client.post(
        f"/api/v1/gradebooks/{gb_id}/fill-submission",
        json={"submission_id": sub["submission_id"], "target_row": 2},
    )
    assert res_fill.status_code == 200
    filled = res_fill.json()
    assert filled["filled_row_index"] == 2
    assert filled["match_mode"] == "exact_row"

    res_dl = client.get(f"/api/v1/gradebooks/{gb_id}/download")
    assert res_dl.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(res_dl.content))
    ws = wb.active
    assert ws.cell(row=2, column=gb["name_col"]).value == "Trần Thị Bình"
    assert float(ws.cell(row=2, column=gb["score_col"]).value) == float(sub["ai_total_score"])


def test_gradebook_upload_existing_template_fuzzy_match_and_add_column():
    """Upload mẫu Excel có sẵn (header dòng 2, giữ nguyên Mã HS) -> khớp tên tiếng Việt -> thêm cột Điểm 15p & fill."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["TRƯỜNG THCS THỬ NGHIỆM - LỚP 7A"])
    ws.append(["STT", "Mã HS", "Họ và tên", "Điểm GK", "Nhận xét"])
    ws.append([1, "HS001", "Lê Hoàng Nam", "", ""])
    ws.append([2, "HS002", "Nguyễn Văn An", 8.5, "Cũ"])
    buf = io.BytesIO()
    wb.save(buf)

    res_up = client.post(
        "/api/v1/gradebooks/upload-template",
        files=[("file", ("mau_lop_7a.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        data={"name": "Mẫu 7A", "exam_id": "TOAN9_01", "new_score_col_name": "Điểm 15p"},
    )
    assert res_up.status_code == 200
    gb = res_up.json()
    gb_id = gb["gradebook_id"]
    assert gb["header_row"] == 2
    assert gb["name_col"] == 3
    assert gb["score_col"] == 6  # Cột mới 'Điểm 15p' ở F

    # Chấm 1 bài của "Nguyen Van An" (không dấu) -> vẫn phải tự khớp đúng hàng 4 ("Nguyễn Văn An")!
    res_sub = client.post(
        "/api/v1/submissions/grade",
        files={"file": ("an.jpg", _sample_img("An"), "image/jpeg")},
        data={"exam_id": "TOAN9_01", "student_name": "Nguyen Van An"},
    )
    assert res_sub.status_code == 200
    sub = res_sub.json()

    res_fill = client.post(
        f"/api/v1/gradebooks/{gb_id}/fill-submission",
        json={"submission_id": sub["submission_id"]},
    )
    assert res_fill.status_code == 200
    filled = res_fill.json()
    assert filled["filled_row_index"] == 4
    assert filled["match_mode"] in ("exact_name", "fuzzy_matched")

    # Tải về kiểm tra: giữ nguyên Mã HS002 + Điểm GK 8.5 cũ, chỉ fill Điểm 15p ở cột 6
    dl = client.get(f"/api/v1/gradebooks/{gb_id}/download")
    wb2 = openpyxl.load_workbook(io.BytesIO(dl.content))
    ws2 = wb2.active
    assert ws2.cell(row=4, column=2).value == "HS002"
    assert float(ws2.cell(row=4, column=4).value) == 8.5
    assert float(ws2.cell(row=4, column=6).value) == float(sub["ai_total_score"])

    # Thêm thêm cột bằng PATCH /config
    res_cfg = client.patch(
        f"/api/v1/gradebooks/{gb_id}/config",
        json={"add_column_name": "Điểm HK1"},
    )
    assert res_cfg.status_code == 200
    assert any(c["name"] == "Điểm HK1" for c in res_cfg.json()["columns"])


def test_gradebook_fill_multi_student_batch():
    """Chấm folder nhiều học sinh -> fill toàn bộ danh sách vào Bảng điểm Excel."""
    res_batch = client.post(
        "/api/v1/submissions/grade-folder-smart-upload",
        files=[
            ("files", ("hs1.jpg", _sample_img("HS1"), "image/jpeg")),
            ("files", ("hs2.jpg", _sample_img("HS2"), "image/jpeg")),
        ],
        data={"exam_id": "TOAN9_01"},
    )
    assert res_batch.status_code == 200
    batch = res_batch.json()

    gb_res = client.post("/api/v1/gradebooks/create", json={"name": "Bảng điểm Lớp Chấm Batch", "exam_id": "TOAN9_01"})
    gb_id = gb_res.json()["gradebook_id"]

    fill_res = client.post(
        f"/api/v1/gradebooks/{gb_id}/fill-batch",
        json={"batch_id": batch["batch_id"]},
    )
    assert fill_res.status_code == 200
    data = fill_res.json()
    assert len(data["filled_batch"]) == 2
    assert data["filled_count"] == 2
