import io
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.main import app

client = TestClient(app)


def create_dummy_jpeg() -> bytes:
    """Tạo byte ảnh JPEG hợp lệ phục vụ test upload."""
    img = Image.new("RGB", (400, 300), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_api_health():
    """Kiểm tra endpoint /health."""
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"


def test_api_list_and_get_exams():
    """Kiểm tra lấy danh sách và chi tiết đề thi."""
    # List exams
    res_list = client.get("/api/v1/exams")
    assert res_list.status_code == 200
    exams = res_list.json()
    assert len(exams) > 0
    assert any(e["exam_id"] == "TOAN9_01" for e in exams)

    # Get single exam
    res_single = client.get("/api/v1/exams/TOAN9_01")
    assert res_single.status_code == 200
    exam = res_single.json()
    assert exam["exam_id"] == "TOAN9_01"
    assert len(exam["questions"]) == 1


def test_api_get_exam_not_found():
    """Kiểm tra lỗi 404 E002 khi exam_id không tồn tại."""
    res = client.get("/api/v1/exams/NON_EXISTENT_ID")
    assert res.status_code == 404
    detail = res.json()["detail"]
    assert detail["error_code"] == "E002"


def test_api_grade_submission_flow():
    """Kiểm tra upload ảnh và chấm bài qua endpoint /submissions/grade."""
    img_bytes = create_dummy_jpeg()
    files = {"file": ("student_exam.jpg", img_bytes, "image/jpeg")}
    data = {"exam_id": "TOAN9_01", "student_name": "Trần Thị Bích"}

    res = client.post("/api/v1/submissions/grade", files=files, data=data)
    assert res.status_code == 200
    result = res.json()
    assert "submission_id" in result
    assert result["student_name"] == "Trần Thị Bích"
    assert result["status"] in ("GRADED_DRAFT", "FLAGGED_REVIEW")
    assert len(result["question_results"]) == 1

    sub_id = result["submission_id"]

    # Kiểm tra lấy chi tiết bài thi
    res_get = client.get(f"/api/v1/submissions/{sub_id}")
    assert res_get.status_code == 200
    assert res_get.json()["submission_id"] == sub_id


def test_api_grade_submission_invalid_file():
    """Kiểm tra từ chối file sai định dạng (400 E001)."""
    files = {"file": ("malicious.exe", b"MZ_INVALID_HEADER", "application/x-dosexec")}
    data = {"exam_id": "TOAN9_01"}

    res = client.post("/api/v1/submissions/grade", files=files, data=data)
    assert res.status_code == 400
    detail = res.json()["detail"]
    assert detail["error_code"] == "E001"


def test_api_teacher_review_and_override():
    """Kiểm tra luồng giáo viên phê duyệt và ghi đè điểm số (WP-13)."""
    # 1. Chấm bài trước
    img_bytes = create_dummy_jpeg()
    files = {"file": ("exam.jpg", img_bytes, "image/jpeg")}
    res_grade = client.post("/api/v1/submissions/grade", files=files, data={"exam_id": "TOAN9_01"})
    sub_id = res_grade.json()["submission_id"]

    # 2. Giáo viên override điểm và phê duyệt
    review_payload = {
        "submission_id": sub_id,
        "score_overrides": {"Q1": 1.75},
        "teacher_note": "Cộng điểm sáng tạo và chữ đẹp.",
        "approve": True,
    }
    res_rev = client.post("/api/v1/submissions/review", json=review_payload)
    assert res_rev.status_code == 200
    rev_data = res_rev.json()
    assert rev_data["status"] == "APPROVED"
    assert rev_data["final_score"] == 1.75
    assert rev_data["teacher_note"] == "Cộng điểm sáng tạo và chữ đẹp."
