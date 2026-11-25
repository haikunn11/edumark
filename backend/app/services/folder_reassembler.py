from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import os
import re
import shutil
import time
import uuid
import zipfile
from datetime import datetime, timezone

from backend.app.core.cache import cache, compute_image_hash
from backend.app.core.config import settings
from backend.app.core.storage import storage
from backend.app.models.schemas import (
    ExamRubric,
    ExtractedLine,
    ExtractedQuestion,
    FolderReassemblyReport,
    GradingTelemetry,
    ImageQualityReport,
    MathVerificationSummary,
    QuestionGradingResult,
    ReassembledQuestionInfo,
    SubmissionGradingRecord,
    SubmissionStatus,
)
from backend.app.services.agent1_ocr import vision_agent
from backend.app.services.agent2_grader import reasoning_agent
from backend.app.services.math_verifier import verify_question_math
from backend.app.services.pipeline import _estimate_cost_usd, _VND_PER_USD
from backend.app.services.preprocessor import preprocessor

ALLOWED_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def normalize_question_id(raw_qid: str) -> tuple[tuple[int, int, str], str]:
    """
    Chuẩn hóa mọi kiểu đánh số câu hỏi tiếng Việt (Câu 1, Bài 2b, Q3, 1.a, 10...)
    về cặp `(sort_key, canonical_label)` để sắp xếp chuẩn tự nhiên:
    Câu 1 -> Câu 1a -> Câu 1b -> Câu 2 -> ... -> Câu 10.
    """
    s = (raw_qid or "").strip()
    if not s:
        return ((9999, 0, ""), "Câu chưa đánh số")

    # Tìm số chính + tiểu mục (a/b/c hoặc .1/.2)
    m = re.search(
        r"(?:câu|cau|bài|bai|question|q)?\s*0*(\d+)\s*(?:[.\-_)\s]*(?:ý\s*)?([a-zA-Z]|\d+))?",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        num = int(m.group(1))
        sub = (m.group(2) or "").lower()
        if sub.isdigit():
            sort_key = (num, int(sub), "")
            label = f"Câu {num}.{sub}"
        elif sub:
            sort_key = (num, 0, sub)
            label = f"Câu {num}{sub}"
        else:
            sort_key = (num, 0, "")
            label = f"Câu {num}"
        return sort_key, label

    return ((9998, 0, s.lower()), s)


def reassemble_extracted_questions(
    per_file_results: list[tuple[str, list[ExtractedQuestion]]],
) -> tuple[list[ExtractedQuestion], FolderReassemblyReport]:
    """
    Gom nhóm và sắp xếp lại toàn bộ câu hỏi từ nhiều ảnh chụp lộn xộn trong 1 folder:
    - Sắp đúng thứ tự Câu 1 -> Câu 1a -> Câu 2 -> Câu 3... (bất kể tên file hay thứ tự chụp).
    - Tự động ghép nối (merge) nếu 1 câu nằm rải rác trên 2+ ảnh khác nhau.
    - Lưu vết rõ từng dòng đến từ file ảnh nào (`source_file`).
    """
    original_files = [fname for fname, _ in per_file_results]
    original_encounter_order: list[str] = []

    # Map: canonical_label -> {sort_key, question_texts, grade_levels, student_names, student_classes, source_files, lines}
    grouped: dict[str, dict] = {}

    for file_idx, (fname, questions) in enumerate(per_file_results, start=1):
        for q in questions:
            sort_key, canon_label = normalize_question_id(q.question_id)
            if canon_label not in original_encounter_order:
                original_encounter_order.append(canon_label)

            bucket = grouped.setdefault(
                canon_label,
                {
                    "sort_key": sort_key,
                    "question_texts": [],
                    "grade_levels": [],
                    "student_names": [],
                    "student_classes": [],
                    "source_files": [],
                    "lines": [],
                },
            )
            if q.question_text and q.question_text not in bucket["question_texts"]:
                bucket["question_texts"].append(q.question_text)
            if q.grade_level and q.grade_level not in bucket["grade_levels"]:
                bucket["grade_levels"].append(q.grade_level)
            if q.student_name_detected and q.student_name_detected not in bucket["student_names"]:
                bucket["student_names"].append(q.student_name_detected)
            if q.student_class_detected and q.student_class_detected not in bucket["student_classes"]:
                bucket["student_classes"].append(q.student_class_detected)
            if fname not in bucket["source_files"]:
                bucket["source_files"].append(fname)

            for line in q.lines:
                line_copy = line.model_copy(
                    update={
                        "page_no": file_idx,
                        "source_file": fname,
                    }
                )
                bucket["lines"].append(line_copy)

    # Sắp xếp toàn bộ câu hỏi theo sort_key tự nhiên (Câu 1 -> 1a -> 2 -> 3 -> 10)
    sorted_items = sorted(grouped.items(), key=lambda kv: kv[1]["sort_key"])
    sorted_labels = [label for label, _ in sorted_items]

    merged_questions: list[ExtractedQuestion] = []
    reordered_infos: list[ReassembledQuestionInfo] = []
    merged_ids: list[str] = []

    for canon_label, bucket in sorted_items:
        reindexed_lines: list[ExtractedLine] = []
        for idx, l in enumerate(bucket["lines"]):
            reindexed_lines.append(
                l.model_copy(update={"line_index": idx, "line_no": idx + 1})
            )

        q_obj = ExtractedQuestion(
            question_id=canon_label,
            question_text=" | ".join(bucket["question_texts"]),
            grade_level=bucket["grade_levels"][0] if bucket["grade_levels"] else "",
            student_name_detected=bucket["student_names"][0] if bucket["student_names"] else "",
            student_class_detected=bucket["student_classes"][0] if bucket["student_classes"] else "",
            source_files=list(bucket["source_files"]),
            lines=reindexed_lines,
        )
        merged_questions.append(q_obj)

        is_merged = len(bucket["source_files"]) > 1
        if is_merged:
            merged_ids.append(canon_label)
        reordered_infos.append(
            ReassembledQuestionInfo(
                question_id=canon_label,
                source_files=list(bucket["source_files"]),
                line_count=len(reindexed_lines),
                merged_from_multiple_pages=is_merged,
            )
        )

    was_reordered = sorted_labels != original_encounter_order
    summary_parts = [
        f"Đã quét {len(original_files)} ảnh -> nhận diện {len(merged_questions)} câu ({', '.join(sorted_labels)})."
    ]
    if was_reordered:
        summary_parts.append("Đã tự động sắp xếp lại đúng thứ tự đề thi.")
    if merged_ids:
        summary_parts.append(f"Đã ghép nối tự động các câu trải nhiều ảnh: {', '.join(merged_ids)}.")

    report = FolderReassemblyReport(
        total_files_scanned=len(original_files),
        original_file_order=original_files,
        reordered_questions=reordered_infos,
        was_reordered=was_reordered,
        merged_question_ids=merged_ids,
        summary=" ".join(summary_parts),
    )
    return merged_questions, report


def load_images_from_local_folder(folder_path: str, max_files: int = 30) -> list[tuple[str, bytes]]:
    """Đọc toàn bộ ảnh hợp lệ trong 1 thư mục local (sắp xếp tự nhiên theo tên file)."""
    norm_path = os.path.abspath(folder_path.strip().strip('"').strip("'"))
    if not os.path.exists(norm_path) or not os.path.isdir(norm_path):
        raise FileNotFoundError(f"Không tìm thấy thư mục: {norm_path}")

    entries: list[str] = []
    for name in os.listdir(norm_path):
        full = os.path.join(norm_path, name)
        if os.path.isfile(full) and name.lower().endswith(ALLOWED_IMAGE_EXTS):
            entries.append(name)

    if not entries:
        raise ValueError(f"Thư mục '{norm_path}' không chứa ảnh hợp lệ (.jpg, .jpeg, .png, .webp).")
    if len(entries) > max_files:
        raise ValueError(f"Thư mục có {len(entries)} ảnh, vượt quá giới hạn tối đa {max_files} ảnh cho 1 bài học sinh.")

    def _nat_key(s: str):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]

    entries.sort(key=_nat_key)

    result: list[tuple[str, bytes]] = []
    for name in entries:
        with open(os.path.join(norm_path, name), "rb") as f:
            result.append((name, f.read()))
    return result


def grade_student_folder(
    files_data: list[tuple[str, bytes]],
    rubric: ExamRubric,
    student_name: str = "Học sinh ẩn danh",
    max_workers: int = 2,
) -> SubmissionGradingRecord:
    """
    Chế độ A: 1 Folder = 1 học sinh nhiều trang lộn xộn.
    1. Tiền xử lý & OCR SONG SONG từng ảnh (tận dụng MD5 Cache cho từng ảnh).
    2. Smart Reassembler tự sắp lại Câu 1 -> 2 -> 3 & ghép các mảnh cùng câu.
    3. SymPy kiểm chứng từng câu.
    4. Agent 2 chấm trọn bộ (1 lần gọi LLM cho chế độ AUTO đa câu hoặc khớp Barem chuẩn).
    5. Tổng hợp đầy đủ Telemetry (thời gian từng chặng, tổng token, tổng chi phí VND).
    """
    if not files_data:
        raise ValueError("Danh sách ảnh trong folder không được rỗng.")

    t_total_start = time.perf_counter()
    sub_id = f"folder_{uuid.uuid4().hex[:8]}"

    h = hashlib.md5()
    for fname, raw in files_data:
        h.update(fname.encode("utf-8"))
        h.update(raw)
    folder_hash = h.hexdigest()

    is_live_key = vision_agent.is_live
    workers = max(1, min(max_workers, len(files_data)))

    # --- BƯỚC 1: Tiền xử lý toàn bộ ảnh song song ---
    t_pre_start = time.perf_counter()

    def _preprocess_one(idx_item: tuple[int, tuple[str, bytes]]):
        idx, (fname, raw_bytes) = idx_item
        proc_bytes, q_rep = preprocessor.process_image(raw_bytes)
        save_name = f"{sub_id}_p{idx}_{re.sub(r'[^0-9a-zA-Z._-]', '_', fname)}"
        storage.save_file(save_name, proc_bytes, subfolder=rubric.exam_id)
        return fname, raw_bytes, proc_bytes, q_rep

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pre_results = list(pool.map(_preprocess_one, enumerate(files_data, start=1)))
    preprocess_ms = int(round((time.perf_counter() - t_pre_start) * 1000))

    reports = [r[3] for r in pre_results]
    merged_quality = ImageQualityReport(
        is_blurry=any(r.is_blurry for r in reports),
        blur_score=min(r.blur_score for r in reports),
        brightness=round(sum(r.brightness for r in reports) / len(reports), 1),
        is_too_dark=any(r.is_too_dark for r in reports),
        is_overexposed=any(r.is_overexposed for r in reports),
        perspective_corrected=any(r.perspective_corrected for r in reports),
        shadow_removed=any(r.shadow_removed for r in reports),
        is_acceptable=all(r.is_acceptable for r in reports),
        warnings=[w for r in reports for w in r.warnings],
        recommendation="Đạt chuẩn" if all(r.is_acceptable for r in reports) else "Có trang hơi mờ/thiếu sáng.",
        applied_transforms=[t for r in reports for t in r.applied_transforms][:20],
    )

    # --- BƯỚC 2: Agent 1 OCR SONG SONG từng ảnh (kèm MD5 Cache từng ảnh) ---
    t_ocr_start = time.perf_counter()

    def _ocr_one(idx_item: tuple[int, tuple[str, bytes, bytes, ImageQualityReport]]):
        idx, (fname, raw_bytes, proc_bytes, q_rep) = idx_item
        img_hash = compute_image_hash(raw_bytes)
        cache_key = f"cache:ocr:{'live' if is_live_key else 'mock'}:{img_hash}"
        cached = cache.get(cache_key)
        if cached:
            qs = [ExtractedQuestion(**q) for q in cached]
            return fname, qs, True, {
                "provider": "cache",
                "model": "cache",
                "input_tokens": 0,
                "output_tokens": 0,
                "thinking_tokens": 0,
                "total_tokens": 0,
            }
        # Giãn nhẹ giữa các luồng (0.4s) tránh burst 503 trên Gemini Free Tier
        if is_live_key and idx > 1:
            time.sleep(0.4 * (idx % max(1, workers)))
        qs, usage = vision_agent.extract_latex_with_usage(
            proc_bytes,
            applied_transforms=q_rep.applied_transforms,
        )
        cache.set(cache_key, [q.model_dump() for q in qs], ttl_seconds=86400)
        return fname, qs, False, usage

    with ThreadPoolExecutor(max_workers=workers) as pool:
        ocr_results = list(pool.map(_ocr_one, enumerate(pre_results, start=1)))
    ocr_ms = int(round((time.perf_counter() - t_ocr_start) * 1000))

    all_cache_hit = all(r[2] for r in ocr_results)
    per_file_questions = [(r[0], r[1]) for r in ocr_results]

    ocr_in = sum(int(r[3].get("input_tokens", 0) or 0) for r in ocr_results)
    ocr_out = sum(int(r[3].get("output_tokens", 0) or 0) for r in ocr_results)
    ocr_th = sum(int(r[3].get("thinking_tokens", 0) or 0) for r in ocr_results)
    ocr_tot = sum(int(r[3].get("total_tokens", 0) or 0) for r in ocr_results)
    ocr_models = [str(r[3].get("model", "")) for r in ocr_results if r[3].get("model") not in ("", "cache")]
    ocr_model_label = ocr_models[0] if ocr_models else ("cache" if all_cache_hit else "mock")
    ocr_provider = next(
        (str(r[3].get("provider")) for r in ocr_results if r[3].get("provider") not in ("", None, "cache")),
        "mock",
    )

    # --- BƯỚC 3: Smart Reassembly (Tự sắp Câu 1 -> 2 -> 3 & ghép mảnh cùng câu) ---
    reassembled_questions, reassembly_report = reassemble_extracted_questions(per_file_questions)

    detected_name = next(
        (q.student_name_detected.strip() for q in reassembled_questions if q.student_name_detected.strip()),
        "",
    )
    final_student_name = student_name
    if detected_name and (not student_name or student_name.strip() in ("", "Học sinh ẩn danh")):
        final_student_name = detected_name

    # --- BƯỚC 4: Kiểm chứng toán học SymPy cho từng câu ---
    t_sympy_start = time.perf_counter()
    mv_map: dict[str, MathVerificationSummary] = {}
    total_eq = 0
    total_valid = 0
    total_invalid = 0
    all_roots: list[str] = []
    all_issues: list[str] = []
    for q in reassembled_questions:
        mv = verify_question_math(q)
        mv_map[q.question_id] = mv
        total_eq += mv.equations_checked
        total_valid += mv.valid_transitions
        total_invalid += mv.invalid_transitions
        all_roots.extend(mv.detected_roots)
        all_issues.extend(f"[{q.question_id}] {iss}" for iss in mv.issues)

    combined_mv = MathVerificationSummary(
        equations_checked=total_eq,
        valid_transitions=total_valid,
        invalid_transitions=total_invalid,
        detected_roots=all_roots,
        issues=all_issues,
        summary=(
            f"SymPy kiểm chứng {total_eq} PT trên {len(reassembled_questions)} câu: "
            f"{total_valid} bước đúng, {total_invalid} bước nghi sai."
            if total_eq > 0
            else "Chưa trích xuất được phương trình đại số đơn giản để SymPy kiểm chứng."
        ),
    )
    sympy_ms = int(round((time.perf_counter() - t_sympy_start) * 1000))

    # --- BƯỚC 5: Agent 2 chấm trọn bộ các câu đã sắp xếp ---
    t_grade_start = time.perf_counter()
    reasoning_agent.reset_last_usage()
    question_results: list[QuestionGradingResult] = []
    exam_title = rubric.title

    if rubric.exam_id == "AUTO" or not rubric.questions:
        exam_title, question_results = reasoning_agent.grade_auto_rubric_multi(
            reassembled_questions,
            mv_map=mv_map,
        )
    else:
        question_results, _ = reasoning_agent.grade_with_exam_rubric(
            reassembled_questions,
            rubric,
            mv_map=mv_map,
        )

    grading_ms = int(round((time.perf_counter() - t_grade_start) * 1000))
    grader_usage = dict(getattr(reasoning_agent, "last_usage", {}))
    total_ms = int(round((time.perf_counter() - t_total_start) * 1000))

    grd_in = int(grader_usage.get("input_tokens", 0) or 0)
    grd_out = int(grader_usage.get("output_tokens", 0) or 0)
    grd_th = int(grader_usage.get("thinking_tokens", 0) or 0)
    grd_tot = int(grader_usage.get("total_tokens", grd_in + grd_out + grd_th) or 0)
    grd_model = str(grader_usage.get("model", "rule-based"))

    cost_usd = _estimate_cost_usd(ocr_model_label, ocr_in, ocr_out + ocr_th) + _estimate_cost_usd(
        grd_model, grd_in, grd_out + grd_th
    )

    total_ai_score = round(sum(q.suggested_score for q in question_results), 2)
    total_possible = round(sum(q.total_points or q.max_score for q in question_results), 2) or 10.0
    needs_review = any(q.needs_review for q in question_results) or merged_quality.is_blurry

    saved_page_files: list[str] = []
    for idx, (fname, _, _, _) in enumerate(pre_results, start=1):
        saved_page_files.append(f"{sub_id}_p{idx}_{re.sub(r'[^0-9a-zA-Z._-]', '_', fname)}")

    record = SubmissionGradingRecord(
        submission_id=sub_id,
        exam_id=rubric.exam_id,
        exam_title=exam_title,
        student_name=final_student_name,
        detected_student_name=detected_name,
        page_count=len(files_data),
        page_files=saved_page_files,
        rubric_version=rubric.rubric_version,
        status=SubmissionStatus.FLAGGED_REVIEW if needs_review else SubmissionStatus.GRADED_DRAFT,
        image_hash=folder_hash,
        cache_hit=all_cache_hit,
        is_mock=not is_live_key,
        quality=merged_quality,
        extracted_questions=reassembled_questions,
        math_verification=combined_mv,
        folder_reassembly=reassembly_report,
        telemetry=GradingTelemetry(
            total_ms=total_ms,
            preprocess_ms=preprocess_ms,
            ocr_ms=ocr_ms,
            sympy_ms=sympy_ms,
            grading_ms=grading_ms,
            provider=ocr_provider if ocr_provider != "mock" else str(grader_usage.get("provider", "mock")),
            ocr_model=ocr_model_label,
            grader_model=grd_model,
            ocr_input_tokens=ocr_in,
            ocr_output_tokens=ocr_out,
            ocr_thinking_tokens=ocr_th,
            ocr_total_tokens=ocr_tot,
            grader_input_tokens=grd_in,
            grader_output_tokens=grd_out,
            grader_thinking_tokens=grd_th,
            grader_total_tokens=grd_tot,
            total_input_tokens=ocr_in + grd_in,
            total_output_tokens=ocr_out + grd_out,
            total_thinking_tokens=ocr_th + grd_th,
            total_tokens=ocr_tot + grd_tot,
            estimated_cost_usd=round(cost_usd, 6),
            estimated_cost_vnd=round(cost_usd * _VND_PER_USD, 1),
        ),
        question_results=question_results,
        ai_total_score=total_ai_score,
        total_points=total_possible,
        final_score=total_ai_score,
        needs_teacher_review=needs_review,
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    # Tự động sinh Output Folder gồm các ảnh đã sắp xếp theo đúng thứ tự Câu 1 -> 2 -> 3 + Báo cáo chấm
    page_disk_items: list[tuple[int, str, str]] = []
    upload_dir = os.path.join(settings.UPLOAD_DIR, rubric.exam_id)
    for idx, (fname, _, _, _) in enumerate(pre_results, start=1):
        saved_name = saved_page_files[idx - 1]
        page_disk_items.append((idx, fname, os.path.join(upload_dir, saved_name)))
    out_dir, sorted_names = build_sorted_output_folder(record, page_disk_items)
    if record.folder_reassembly:
        record.folder_reassembly.output_folder_path = out_dir
        record.folder_reassembly.sorted_output_files = sorted_names

    return record


def _slugify_vi(text: str) -> str:
    """Tạo nhãn tên file an toàn (không dấu, không ký tự đặc biệt)."""
    rep = {
        "à":"a","á":"a","ả":"a","ã":"a","ạ":"a","ă":"a","ằ":"a","ắ":"a","ẳ":"a","ẵ":"a","ặ":"a","â":"a","ầ":"a","ấ":"a","ẩ":"a","ẫ":"a","ậ":"a",
        "è":"e","é":"e","ẻ":"e","ẽ":"e","ẹ":"e","ê":"e","ề":"e","ế":"e","ể":"e","ễ":"e","ệ":"e",
        "ì":"i","í":"i","ỉ":"i","ĩ":"i","ị":"i",
        "ò":"o","ó":"o","ỏ":"o","õ":"o","ọ":"o","ô":"o","ồ":"o","ố":"o","ổ":"o","ỗ":"o","ộ":"o","ơ":"o","ờ":"o","ớ":"o","ở":"o","ỡ":"o","ợ":"o",
        "ù":"u","ú":"u","ủ":"u","ũ":"u","ụ":"u","ư":"u","ừ":"u","ứ":"u","ử":"u","ữ":"u","ự":"u",
        "ỳ":"y","ý":"y","ỷ":"y","ỹ":"y","ỵ":"y","đ":"d",
    }
    s = "".join(rep.get(c, c) for c in text.lower())
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s).strip("_")
    return s or "khac"


def build_sorted_output_folder(
    record: SubmissionGradingRecord,
    page_disk_items: list[tuple[int, str, str]],
) -> tuple[str, list[str]]:
    """
    Xuất ra 1 thư mục đầu ra sạch tại `backend/data/outputs/{submission_id}/`:
    - Các ảnh được sắp xếp đúng thứ tự tự nhiên theo câu nhỏ nhất có trong ảnh:
      `01_cau_1_cau_2_<ten_file>.jpg`, `02_cau_3_cau_4_<ten_file>.jpg`...
    - Kèm báo cáo `00_KET_QUA_CHAM.md` và `00_ket_qua.json` để kiểm chứng ngay trên máy.
    """
    data_dir = os.path.dirname(os.path.abspath(settings.UPLOAD_DIR))
    out_dir = os.path.join(data_dir, "outputs", record.submission_id)
    os.makedirs(out_dir, exist_ok=True)

    # Tính thứ tự câu cho từng file ảnh dựa trên các câu AI đọc được từ ảnh đó
    enriched: list[tuple[tuple[int, int, str], int, str, str, list[str]]] = []
    for p_no, orig_name, disk_path in page_disk_items:
        if not os.path.isfile(disk_path):
            continue
        q_labels: list[str] = []
        sort_keys: list[tuple[int, int, str]] = []
        for q in record.extracted_questions:
            if orig_name in (q.source_files or []) or any(l.source_file == orig_name for l in q.lines):
                sk, lbl = normalize_question_id(q.question_id)
                if lbl not in q_labels:
                    q_labels.append(lbl)
                    sort_keys.append(sk)
        min_key = min(sort_keys) if sort_keys else (9999, p_no, orig_name.lower())
        enriched.append((min_key, p_no, orig_name, disk_path, q_labels))

    enriched.sort(key=lambda item: (item[0], item[1]))

    sorted_filenames: list[str] = []
    for seq_idx, (_, _, orig_name, disk_path, q_labels) in enumerate(enriched, start=1):
        label_slug = "_".join(_slugify_vi(l) for l in q_labels[:3]) if q_labels else "chua_ro_cau"
        base_stem, ext = os.path.splitext(orig_name)
        ext = ext.lower() if ext else ".jpg"
        safe_stem = re.sub(r"[^0-9a-zA-Z_-]+", "_", base_stem)[:24].strip("_") or f"img{seq_idx}"
        out_name = f"{seq_idx:02d}_{label_slug}_{safe_stem}{ext}"
        shutil.copy2(disk_path, os.path.join(out_dir, out_name))
        sorted_filenames.append(out_name)

    # Ghi file báo cáo Markdown tổng hợp ngay trong folder đầu ra
    lines_md = [
        f"# Kết Quả Chấm Bài — {record.exam_title or record.exam_id}",
        f"- **Mã bài nộp:** `{record.submission_id}`",
        f"- **Học sinh:** {record.student_name}" + (f" (Đọc từ giấy: {record.detected_student_name})" if record.detected_student_name else " *(Không tìm thấy tên ghi trên ảnh)*"),
        f"- **Tổng điểm:** **{record.final_score if record.final_score is not None else record.ai_total_score} / {record.total_points}đ**",
        f"- **Số ảnh:** {len(sorted_filenames)} ảnh | **Tổng thời gian:** {record.telemetry.total_ms / 1000:.2f}s | **Tokens:** {record.telemetry.total_tokens} | **Chi phí ước tính:** ~{record.telemetry.estimated_cost_vnd}đ VND",
        "",
        "## 1. Thứ Tự Ảnh Đã Sắp Xếp Chuẩn",
    ]
    for seq_idx, (out_name, (_, _, orig_name, _, q_labels)) in enumerate(zip(sorted_filenames, enriched), start=1):
        lines_md.append(f"{seq_idx}. `{out_name}` ← gốc `{orig_name}` | Câu nhận diện: {', '.join(q_labels) or 'Chưa rõ'}")

    lines_md.append("\n## 2. Chi Tiết Chấm Từng Câu")
    for qr in record.question_results:
        lines_md.append(f"\n### {qr.question_id} — {qr.suggested_score}/{qr.total_points}đ")
        for c in (qr.criteria_evaluations or qr.criteria_evaluation):
            lines_md.append(f"- [{c.status.upper()}] **{c.name or c.criterion_id}** ({c.earned_points}/{c.max_points}đ): {c.feedback}")
        if qr.teacher_summary_comment:
            lines_md.append(f"- *Nhận xét:* {qr.teacher_summary_comment}")

    with open(os.path.join(out_dir, "00_KET_QUA_CHAM.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines_md) + "\n")

    with open(os.path.join(out_dir, "00_ket_qua.json"), "w", encoding="utf-8") as f:
        f.write(record.model_dump_json(indent=2))

    return out_dir, sorted_filenames


def zip_sorted_output_folder(out_dir: str) -> bytes:
    """Đóng gói toàn bộ thư mục đầu ra đã sắp xếp thành file ZIP để tải từ Web."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in sorted(os.listdir(out_dir)):
            full = os.path.join(out_dir, fname)
            if os.path.isfile(full):
                zf.write(full, arcname=fname)
    return buf.getvalue()
