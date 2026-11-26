from __future__ import annotations

from copy import copy
import io
import os
import re
import unicodedata
import uuid

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from backend.app.core.database import db
from backend.app.models.schemas import SubmissionGradingRecord
from backend.app.services.folder_reassembler import normalize_question_id


def _gradebooks_dir() -> str:
    """Lưu file Excel bảng điểm cùng thư mục gốc của db.db_path (tự cô lập trong pytest!)."""
    if db.db_path == ":memory:":
        base = os.path.abspath("backend/data")
    else:
        base = os.path.dirname(os.path.abspath(db.db_path))
    path = os.path.join(base, "gradebooks")
    os.makedirs(path, exist_ok=True)
    return path


def _norm_vi_name(s: str) -> str:
    """Chuẩn hóa tên học sinh tiếng Việt (bỏ dấu, lowercase, gộp khoảng trắng)."""
    text = (s or "").replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    ascii_str = re.sub(r"[^0-9a-zA-Z\s]+", " ", ascii_str).lower()
    return " ".join(ascii_str.split())


def detect_header_and_columns(ws) -> tuple[int, list[dict], int, int, int]:
    """
    Tự động dò hàng tiêu đề (trong 10 hàng đầu), danh sách cột và gợi ý cột Tên / Điểm / Nhận xét.
    Trả về (header_row, columns, name_col, score_col, note_col) — index 1-based (0 nếu chưa có).
    """
    max_r = min(10, ws.max_row or 1)
    max_c = max(1, ws.max_column or 1)

    header_kw = ("stt", "tên", "ten", "họ", "ho", "mã", "ma", "điểm", "diem", "lớp", "lop", "nhận xét", "nhan xet", "ghi chú", "ghi chu")
    best_row = 1
    best_score = -1

    for r in range(1, max_r + 1):
        vals = [str(ws.cell(row=r, column=c).value or "").strip() for c in range(1, max_c + 1)]
        non_empty = [v for v in vals if v]
        if not non_empty:
            continue
        kw_hits = sum(1 for v in non_empty if any(k in v.lower() for k in header_kw))
        row_score = len(non_empty) + kw_hits * 3
        if row_score > best_score:
            best_score = row_score
            best_row = r

    columns: list[dict] = []
    name_col = 0
    score_col = 0
    note_col = 0

    for c in range(1, max_c + 1):
        raw_h = str(ws.cell(row=best_row, column=c).value or "").strip()
        if not raw_h:
            continue
        col_letter = get_column_letter(c)
        columns.append({"col_index": c, "col_letter": col_letter, "name": raw_h})
        low = _norm_vi_name(raw_h)
        if not name_col and any(k in low for k in ("ho va ten", "ho ten", "ten hoc sinh", "hoc sinh", "hoten", "name")):
            name_col = c
        elif not name_col and ("ten" in low and "ma" not in low):
            name_col = c
        if not score_col and any(k in low for k in ("tong diem", "diem chot", "diem kt", "diem so", "diem ai", "diem thi", "diem", "score")):
            score_col = c
        if not note_col and any(k in low for k in ("nhan xet", "ghi chu", "danh gia", "note", "comment")):
            note_col = c

    if not name_col and columns:
        # Nếu cột 1 là STT thì chọn cột 2 làm cột tên
        first_low = _norm_vi_name(columns[0]["name"])
        if ("stt" in first_low or "ma" in first_low) and len(columns) >= 2:
            name_col = columns[1]["col_index"]
        else:
            name_col = columns[0]["col_index"]

    return best_row, columns, name_col, score_col, note_col


def _build_concise_comment(record: SubmissionGradingRecord) -> str:
    if (record.teacher_note or "").strip():
        return record.teacher_note.strip()
    parts: list[str] = []
    for qr in record.question_results:
        c = (qr.teacher_summary_comment or "").strip()
        if not c or "không tìm thấy trong bài nộp" in c.lower():
            continue
        parts.append(f"[{qr.question_id}] {c}" if len(record.question_results) > 1 else c)
    return " | ".join(parts)[:500]


def read_gradebook_preview(gradebook_id: str, max_rows: int = 200) -> dict:
    meta = db.get_gradebook(gradebook_id)
    if not meta:
        raise FileNotFoundError(f"Không tìm thấy bảng điểm với mã '{gradebook_id}'.")
    fpath = meta["file_path"]
    if not os.path.isfile(fpath):
        raise FileNotFoundError(f"File Excel bảng điểm không tồn tại: {fpath}")

    wb = openpyxl.load_workbook(fpath, data_only=False)
    ws = wb.active
    header_row = int(meta.get("header_row") or 1)
    max_c = ws.max_column or 1

    columns: list[dict] = []
    for c in range(1, max_c + 1):
        h_val = str(ws.cell(row=header_row, column=c).value or "").strip()
        if h_val:
            columns.append({"col_index": c, "col_letter": get_column_letter(c), "name": h_val})

    name_col = int(meta.get("name_col") or 0)
    score_col = int(meta.get("score_col") or 0)
    note_col = int(meta.get("note_col") or 0)

    rows: list[dict] = []
    filled_count = 0
    for r in range(header_row + 1, min((ws.max_row or header_row) + 1, header_row + 1 + max_rows)):
        cells: dict[str, str] = {}
        has_any = False
        for col_meta in columns:
            val = ws.cell(row=r, column=col_meta["col_index"]).value
            if val is not None and str(val).strip() != "":
                has_any = True
                cells[col_meta["col_letter"]] = str(val)
            else:
                cells[col_meta["col_letter"]] = ""
        if not has_any:
            continue
        stu_name = cells.get(get_column_letter(name_col), "") if name_col else ""
        if score_col and cells.get(get_column_letter(score_col), "") != "":
            filled_count += 1
        rows.append({"row_index": r, "student_name": stu_name, "cells": cells})

    return {
        "gradebook_id": gradebook_id,
        "name": meta["name"],
        "exam_id": meta.get("exam_id", "AUTO"),
        "header_row": header_row,
        "name_col": name_col,
        "score_col": score_col,
        "note_col": note_col,
        "columns": columns,
        "rows": rows,
        "total_rows": len(rows),
        "filled_count": filled_count,
        "download_url": f"/api/v1/gradebooks/{gradebook_id}/download",
        "updated_at": meta.get("updated_at", ""),
    }


def list_gradebooks() -> list[dict]:
    items = db.list_gradebooks()
    res: list[dict] = []
    for m in items:
        if os.path.isfile(m.get("file_path", "")):
            res.append(read_gradebook_preview(m["gradebook_id"], max_rows=30))
    return res


def create_gradebook(
    name: str,
    exam_id: str = "AUTO",
    columns: list[str] | None = None,
    student_names: list[str] | None = None,
    question_ids: list[str] | None = None,
) -> dict:
    """Tạo một Bảng điểm Excel mới (có thể kèm sẵn cột câu hỏi và danh sách học sinh)."""
    gb_id = f"gb_{uuid.uuid4().hex[:8]}"
    gb_name = (name or "").strip() or "Bảng điểm EduMark"

    if columns:
        headers = [c.strip() for c in columns if c.strip()]
    else:
        headers = ["STT", "Họ và tên", "Lớp"] + [q for q in (question_ids or []) if q] + ["Tổng điểm", "Nhận xét", "Trạng thái"]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Bang_Diem"

    header_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid")
    header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
    normal_font = Font(name="Arial", size=10)
    thin_border = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="thin", color="CBD5E1"),
    )

    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    clean_students = [s.strip() for s in (student_names or []) if s.strip()]
    header_row, cols_meta, name_col, score_col, note_col = detect_header_and_columns(ws)
    stt_col = next((c["col_index"] for c in cols_meta if "stt" in _norm_vi_name(c["name"])), 0)

    for idx, stu in enumerate(clean_students, start=1):
        r_idx = header_row + idx
        if stt_col:
            ws.cell(row=r_idx, column=stt_col, value=idx)
        if name_col:
            ws.cell(row=r_idx, column=name_col, value=stu)
        for c in range(1, len(headers) + 1):
            c_cell = ws.cell(row=r_idx, column=c)
            c_cell.font = normal_font
            c_cell.border = thin_border

    for c_idx, h in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(c_idx)].width = max(len(h) + 6, 14)

    fpath = os.path.join(_gradebooks_dir(), f"{gb_id}.xlsx")
    wb.save(fpath)

    meta = {
        "gradebook_id": gb_id,
        "name": gb_name,
        "exam_id": exam_id or "AUTO",
        "file_path": fpath,
        "header_row": header_row,
        "name_col": name_col,
        "score_col": score_col,
        "note_col": note_col,
    }
    db.save_gradebook(meta)
    return read_gradebook_preview(gb_id)


def _append_header_column(ws, header_row: int, col_name: str) -> int:
    """Thêm cột mới vào cuối bảng hiện có và sao chép style từ ô tiêu đề bên trái."""
    new_col = 1
    for c in range(1, (ws.max_column or 1) + 1):
        if str(ws.cell(row=header_row, column=c).value or "").strip():
            new_col = c + 1
    prev_cell = ws.cell(row=header_row, column=max(1, new_col - 1))
    new_cell = ws.cell(row=header_row, column=new_col, value=col_name.strip())
    if prev_cell.has_style:
        new_cell.font = copy(prev_cell.font)
        new_cell.fill = copy(prev_cell.fill)
        new_cell.border = copy(prev_cell.border)
        new_cell.alignment = copy(prev_cell.alignment)
    ws.column_dimensions[get_column_letter(new_col)].width = max(len(col_name) + 6, 14)
    return new_col


def import_excel_template(
    file_bytes: bytes,
    filename: str,
    name: str = "",
    exam_id: str = "AUTO",
    new_score_col_name: str = "",
) -> dict:
    """Nạp file Excel (.xlsx) mẫu có sẵn của giáo viên (giữ nguyên toàn bộ định dạng & hàng/cột cũ)."""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes))
    except Exception as exc:
        raise ValueError(f"Không thể đọc file Excel .xlsx: {exc}")

    ws = wb.active
    header_row, cols_meta, name_col, score_col, note_col = detect_header_and_columns(ws)
    if not cols_meta:
        raise ValueError("File Excel không có dòng tiêu đề cột hợp lệ để fill điểm.")

    if (new_score_col_name or "").strip():
        score_col = _append_header_column(ws, header_row, new_score_col_name.strip())
    elif not score_col:
        score_col = _append_header_column(ws, header_row, "Điểm EduMark")

    gb_id = f"gb_{uuid.uuid4().hex[:8]}"
    gb_name = (name or "").strip() or os.path.splitext(os.path.basename(filename or "Bang_Diem"))[0]
    fpath = os.path.join(_gradebooks_dir(), f"{gb_id}.xlsx")
    wb.save(fpath)

    meta = {
        "gradebook_id": gb_id,
        "name": gb_name,
        "exam_id": exam_id or "AUTO",
        "file_path": fpath,
        "header_row": header_row,
        "name_col": name_col,
        "score_col": score_col,
        "note_col": note_col,
    }
    db.save_gradebook(meta)
    return read_gradebook_preview(gb_id)


def update_gradebook_config(
    gradebook_id: str,
    name_col: int | None = None,
    score_col: int | None = None,
    note_col: int | None = None,
    add_column_name: str = "",
) -> dict:
    meta = db.get_gradebook(gradebook_id)
    if not meta:
        raise FileNotFoundError(f"Không tìm thấy bảng điểm '{gradebook_id}'.")

    fpath = meta["file_path"]
    header_row = int(meta.get("header_row") or 1)
    if (add_column_name or "").strip():
        wb = openpyxl.load_workbook(fpath)
        ws = wb.active
        new_c = _append_header_column(ws, header_row, add_column_name.strip())
        wb.save(fpath)
        if score_col is None:
            meta["score_col"] = new_c

    if name_col is not None and name_col >= 1:
        meta["name_col"] = int(name_col)
    if score_col is not None and score_col >= 1:
        meta["score_col"] = int(score_col)
    if note_col is not None and note_col >= 0:
        meta["note_col"] = int(note_col)

    db.save_gradebook(meta)
    return read_gradebook_preview(gradebook_id)


def fill_submission_into_gradebook(
    gradebook_id: str,
    record: SubmissionGradingRecord,
    target_row: int | None = None,
    score_col: int | None = None,
    note_col: int | None = None,
    student_name_override: str | None = None,
    auto_append: bool = True,
) -> dict:
    """
    Điền điểm 1 bài nộp vào đúng hàng (row) và cột (column) trong Bảng điểm Excel.
    - Nếu chỉ định `target_row`: ghi chính xác vào dòng đó.
    - Nếu không: tự khớp theo tên học sinh (không phân biệt dấu tiếng Việt), nếu chưa có thì thêm dòng mới.
    - Tự động điền thêm điểm từng câu (`Câu 1`, `Câu 2a`...) nếu bảng có cột trùng tên câu hỏi.
    """
    meta = db.get_gradebook(gradebook_id)
    if not meta:
        raise FileNotFoundError(f"Không tìm thấy bảng điểm '{gradebook_id}'.")

    fpath = meta["file_path"]
    wb = openpyxl.load_workbook(fpath)
    ws = wb.active
    header_row = int(meta.get("header_row") or 1)
    eff_name_col = int(meta.get("name_col") or 1)
    eff_score_col = int(score_col or meta.get("score_col") or 0)
    eff_note_col = int(note_col if note_col is not None else (meta.get("note_col") or 0))

    if not eff_score_col:
        eff_score_col = _append_header_column(ws, header_row, "Điểm EduMark")
        meta["score_col"] = eff_score_col

    eff_name = (student_name_override or record.student_name or "Học sinh ẩn danh").strip()
    norm_target = _norm_vi_name(eff_name)

    # Tìm hàng đích
    chosen_row = 0
    match_mode = ""
    last_data_row = header_row
    for r in range(header_row + 1, (ws.max_row or header_row) + 1):
        row_has_data = any(
            ws.cell(row=r, column=c).value not in (None, "")
            for c in range(1, (ws.max_column or 1) + 1)
        )
        if row_has_data:
            last_data_row = r

    if target_row is not None and target_row > header_row:
        chosen_row = int(target_row)
        match_mode = "exact_row"
    else:
        # Khớp chính xác trước, rồi tới khớp gần đúng (chứa tên)
        fuzzy_cand = 0
        for r in range(header_row + 1, last_data_row + 1):
            cell_name = str(ws.cell(row=r, column=eff_name_col).value or "").strip()
            if not cell_name:
                continue
            norm_cell = _norm_vi_name(cell_name)
            if norm_cell == norm_target and norm_target:
                chosen_row = r
                match_mode = "exact_name"
                break
            if not fuzzy_cand and norm_target and (norm_target in norm_cell or norm_cell in norm_target):
                fuzzy_cand = r
        if not chosen_row and fuzzy_cand:
            chosen_row = fuzzy_cand
            match_mode = "fuzzy_matched"

    if not chosen_row:
        if not auto_append:
            raise ValueError(f"Không tìm thấy học sinh '{eff_name}' trong bảng điểm để điền tự động.")
        chosen_row = last_data_row + 1
        match_mode = "appended"
        # Ghi STT nếu cột 1 là STT
        col1_h = _norm_vi_name(str(ws.cell(row=header_row, column=1).value or ""))
        if "stt" in col1_h and eff_name_col != 1:
            ws.cell(row=chosen_row, column=1, value=max(1, chosen_row - header_row))
        ws.cell(row=chosen_row, column=eff_name_col, value=eff_name)
    elif match_mode == "exact_row" and not str(ws.cell(row=chosen_row, column=eff_name_col).value or "").strip():
        ws.cell(row=chosen_row, column=eff_name_col, value=eff_name)

    final_score = round(float(record.final_score if record.final_score is not None else record.ai_total_score), 2)
    ws.cell(row=chosen_row, column=eff_score_col, value=final_score)

    if eff_note_col > 0 and eff_note_col != eff_score_col:
        ws.cell(row=chosen_row, column=eff_note_col, value=_build_concise_comment(record))

    # Tự điền điểm từng câu + Trạng thái nếu bảng có cột trùng tên
    q_score_by_canon = {
        normalize_question_id(qr.question_id)[1].lower(): round(float(qr.suggested_score), 2)
        for qr in record.question_results
    }
    for c in range(1, (ws.max_column or 1) + 1):
        if c in (eff_name_col, eff_score_col, eff_note_col):
            continue
        h_text = str(ws.cell(row=header_row, column=c).value or "").strip()
        if not h_text:
            continue
        h_norm = _norm_vi_name(h_text)
        if "trang thai" in h_norm or h_norm == "status":
            ws.cell(row=chosen_row, column=c, value=record.status.value)
            continue
        _, h_q_canon = normalize_question_id(h_text)
        if h_q_canon.lower() in q_score_by_canon:
            ws.cell(row=chosen_row, column=c, value=q_score_by_canon[h_q_canon.lower()])

    wb.save(fpath)
    db.save_gradebook(meta)

    preview = read_gradebook_preview(gradebook_id)
    preview["filled_row_index"] = chosen_row
    preview["match_mode"] = match_mode
    preview["filled_student"] = str(ws.cell(row=chosen_row, column=eff_name_col).value or eff_name)
    preview["filled_score"] = final_score
    return preview


def fill_batch_into_gradebook(
    gradebook_id: str,
    records: list[SubmissionGradingRecord],
    score_col: int | None = None,
    note_col: int | None = None,
    auto_append: bool = True,
) -> dict:
    """Điền trọn bộ nhiều học sinh (kết quả chấm folder) vào Bảng điểm Excel."""
    if not records:
        raise ValueError("Không có bản ghi học sinh nào để điền vào bảng.")
    filled_rows: list[dict] = []
    preview: dict = {}
    for rec in records:
        preview = fill_submission_into_gradebook(
            gradebook_id=gradebook_id,
            record=rec,
            target_row=None,
            score_col=score_col,
            note_col=note_col,
            auto_append=auto_append,
        )
        filled_rows.append(
            {
                "submission_id": rec.submission_id,
                "student_name": preview["filled_student"],
                "row_index": preview["filled_row_index"],
                "match_mode": preview["match_mode"],
                "score": preview["filled_score"],
            }
        )
    preview["filled_batch"] = filled_rows
    return preview
