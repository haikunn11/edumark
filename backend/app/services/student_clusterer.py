from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
import io
import os
import re
import shutil
import time
import uuid
import zipfile
from datetime import datetime, timezone
from PIL import Image

from backend.app.core.cache import cache, compute_image_hash
from backend.app.core.config import settings
from backend.app.core.storage import storage
from backend.app.models.schemas import (
    ExamRubric,
    ExtractedQuestion,
    FolderReassemblyReport,
    GradingTelemetry,
    ImageQualityReport,
    MathVerificationSummary,
    QuestionGradingResult,
    SmartFolderBatchResult,
    SubmissionGradingRecord,
    SubmissionStatus,
)
from backend.app.services.agent1_ocr import vision_agent
from backend.app.services.agent2_grader import reasoning_agent
from backend.app.services.batch import export_to_excel
from backend.app.services.folder_reassembler import (
    _slugify_vi,
    build_sorted_output_folder,
    normalize_question_id,
    reassemble_extracted_questions,
)
from backend.app.services.math_verifier import verify_question_math
from backend.app.services.pipeline import _estimate_cost_usd, _VND_PER_USD
from backend.app.services.preprocessor import preprocessor


@dataclass
class PageProfile:
    page_index: int
    filename: str
    raw_bytes: bytes
    proc_bytes: bytes
    saved_filename: str
    saved_disk_path: str
    quality: ImageQualityReport
    questions: list[ExtractedQuestion]
    student_name: str = ""
    student_class: str = ""
    paper_brand: str = ""
    handwriting_style: str = ""
    orientation_deg: int = 0
    was_rotated: bool = False
    cache_hit: bool = False
    ocr_usage: dict = field(default_factory=dict)

    @property
    def canonical_qids(self) -> set[str]:
        return {normalize_question_id(q.question_id)[1] for q in self.questions if q.question_id}


_EMPTY_TOKENS = {"", "khongro", "chuaro", "unknown", "none", "null", "na", "khongco", "hocsinh", "chuaroten"}


def _norm_token(s: str) -> str:
    raw = (s or "").strip()
    if not raw:
        return ""
    tok = re.sub(r"[^0-9a-zA-Z]+", "", _slugify_vi(raw)).lower()
    return "" if tok in _EMPTY_TOKENS else tok


def _rotate_jpeg_cw(image_bytes: bytes, deg_cw: int) -> bytes:
    """Xoay ảnh JPEG theo chiều kim đồng hồ (90 / 180 / 270 độ) để chữ đứng thẳng."""
    if deg_cw not in (90, 180, 270):
        return image_bytes
    img = Image.open(io.BytesIO(image_bytes))
    # PIL.Image.rotate xoay ngược chiều kim đồng hồ -> dùng -deg_cw để xoay cùng chiều kim đồng hồ
    rotated = img.rotate(-deg_cw, expand=True)
    out = io.BytesIO()
    rotated.convert("RGB").save(out, format="JPEG", quality=92, optimize=True)
    return out.getvalue()


def _has_hard_conflict(cluster_pages: list[PageProfile], candidate: PageProfile) -> bool:
    """
    Trả về True nếu chắc chắn `candidate` KHÔNG thuộc cùng học sinh với `cluster_pages`:
    1. Khác Họ tên (cả hai đều có tên rõ ràng).
    2. Khác thương hiệu giấy (vd HAI TIEN vs KOKUYO vs KLONG vs ClassMate vs THIEN LONG).
    3. Cùng giải trùng ít nhất 1 câu hoàn chỉnh (trùng >= 2 mã câu, hoặc trùng câu chính như Câu 2a, Câu 3).
    """
    c_name = _norm_token(candidate.student_name)
    c_brand = _norm_token(candidate.paper_brand)
    c_qids = candidate.canonical_qids

    cluster_names = {_norm_token(p.student_name) for p in cluster_pages if _norm_token(p.student_name)}
    cluster_brands = {_norm_token(p.paper_brand) for p in cluster_pages if _norm_token(p.paper_brand)}
    cluster_qids: set[str] = set()
    for p in cluster_pages:
        cluster_qids.update(p.canonical_qids)

    # 1. Khác tên học sinh
    if c_name and cluster_names and c_name not in cluster_names:
        return True

    # 2. Khác thương hiệu giấy (vở)
    if c_brand and cluster_brands and c_brand not in cluster_brands:
        return True

    # 3. Trùng mã câu hoàn chỉnh (vd cả 2 ảnh đều làm Câu 2a, hoặc cả 2 đều làm Bài 3)
    overlap = c_qids.intersection(cluster_qids)
    # Bỏ qua nhãn chung chung kiểu "Câu chưa đánh số"
    meaningful_overlap = {q for q in overlap if q != "Câu chưa đánh số"}
    if meaningful_overlap:
        return True

    return False


def cluster_pages_into_students(pages: list[PageProfile]) -> list[tuple[str, str, list[PageProfile]]]:
    """
    Thuật toán gom cụm trang bài làm thành từng học sinh độc lập (An toàn — CẤM gộp nhầm học sinh):
    Trả về list[(student_label, cluster_reason, list[PageProfile])].
    """
    clusters: list[dict] = []

    for page in pages:
        p_name = _norm_token(page.student_name)
        p_brand = _norm_token(page.paper_brand)
        matched_cluster = None
        match_reason = ""

        # Ưu tiên 1: Ghép theo TRÙNG HỌ TÊN (không xung đột)
        if p_name:
            for cl in clusters:
                cl_names = {_norm_token(p.student_name) for p in cl["pages"] if _norm_token(p.student_name)}
                if p_name in cl_names and not _has_hard_conflict(cl["pages"], page):
                    matched_cluster = cl
                    match_reason = f"Ghép tự động theo Họ tên: {page.student_name}"
                    break

        # Ưu tiên 2: Ghép theo CÙNG THƯƠNG HIỆU GIẤY + câu hỏi nối tiếp KHÔNG TRÙNG
        if matched_cluster is None and p_brand:
            for cl in clusters:
                cl_brands = {_norm_token(p.paper_brand) for p in cl["pages"] if _norm_token(p.paper_brand)}
                if p_brand in cl_brands and not _has_hard_conflict(cl["pages"], page):
                    matched_cluster = cl
                    match_reason = f"Ghép theo cùng loại giấy ({page.paper_brand}) + câu hỏi nối tiếp không trùng"
                    break

        if matched_cluster is not None:
            matched_cluster["pages"].append(page)
            if match_reason and match_reason not in matched_cluster["reasons"]:
                matched_cluster["reasons"].append(match_reason)
        else:
            clusters.append({"pages": [page], "reasons": []})

    result: list[tuple[str, str, list[PageProfile]]] = []
    for idx, cl in enumerate(clusters, start=1):
        cl_pages: list[PageProfile] = cl["pages"]
        detected_name = next((p.student_name.strip() for p in cl_pages if p.student_name.strip()), "")
        detected_brand = next((p.paper_brand.strip() for p in cl_pages if p.paper_brand.strip()), "")

        if detected_name:
            label = detected_name
        elif detected_brand:
            label = f"Học sinh {idx:02d} (Giấy {detected_brand})"
        else:
            label = f"Học sinh {idx:02d} (Chưa rõ tên)"

        if cl["reasons"]:
            reason = "; ".join(cl["reasons"])
        elif len(cl_pages) == 1:
            reason = (
                f"Tách riêng 1 học sinh ({detected_brand or 'ảnh đơn'})"
                if not detected_name
                else f"Nhận diện trực tiếp theo tên {detected_name}"
            )
        else:
            reason = f"Nhóm {len(cl_pages)} trang"

        result.append((label, reason, cl_pages))

    return result


def zip_directory_recursive(root_dir: str) -> bytes:
    """Đóng gói toàn bộ cây thư mục (bao gồm mọi thư mục con HS_01/, HS_02/... + Excel) thành file ZIP."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _, filenames in os.walk(root_dir):
            for fname in sorted(filenames):
                full_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(full_path, root_dir)
                zf.write(full_path, arcname=rel_path)
    return buf.getvalue()


def grade_smart_folder_multistudent(
    files_data: list[tuple[str, bytes]],
    rubric: ExamRubric,
    max_workers: int = 2,
) -> SmartFolderBatchResult:
    """
    Chế độ Thông Minh (v0.3.0):
    1. Tiền xử lý & OCR từng ảnh (đọc kèm hướng xoay 90°/180°/270°, hãng giấy, nét chữ, tên HS).
    2. Tự động xoay thẳng đứng các ảnh chụp ngang/ngược và lưu lại lên đĩa.
    3. Tự động phân cụm nhiều học sinh (`cluster_pages_into_students`) — CẤM gộp nhầm học sinh trùng câu!
    4. Với từng học sinh: sắp xếp đúng thứ tự Câu 1 -> 2 -> 3, kiểm chứng SymPy, chấm điểm riêng.
    5. Xuất cây thư mục sạch tại `backend/data/outputs/class_<batch_id>/`:
       - Mỗi học sinh 1 thư mục con riêng (`HS_01_.../`) chứa ảnh đã xoay thẳng & sắp thứ tự + `00_KET_QUA_CHAM.md`.
       - File tổng hợp `00_BANG_DIEM_CA_LOP.xlsx` và `00_TONG_HOP_PHAN_LOAI.md`.
    """
    if not files_data:
        raise ValueError("Danh sách ảnh trong folder không được rỗng.")

    t_total_start = time.perf_counter()
    batch_id = f"class_{uuid.uuid4().hex[:8]}"
    is_live_key = vision_agent.is_live
    workers = max(1, min(max_workers, len(files_data)))

    # --- BƯỚC 1: Tiền xử lý toàn bộ ảnh ---
    t_pre_start = time.perf_counter()
    upload_dir = os.path.join(settings.UPLOAD_DIR, rubric.exam_id)
    os.makedirs(upload_dir, exist_ok=True)

    def _pre_one(idx_item: tuple[int, tuple[str, bytes]]):
        idx, (fname, raw_bytes) = idx_item
        proc_bytes, q_rep = preprocessor.process_image(raw_bytes)
        safe_fn = re.sub(r"[^0-9a-zA-Z._-]", "_", fname)
        saved_name = f"{batch_id}_p{idx}_{safe_fn}"
        disk_path = storage.save_file(saved_name, proc_bytes, subfolder=rubric.exam_id)
        return idx, fname, raw_bytes, proc_bytes, saved_name, disk_path, q_rep

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pre_items = list(pool.map(_pre_one, enumerate(files_data, start=1)))
    preprocess_ms = int(round((time.perf_counter() - t_pre_start) * 1000))

    # --- BƯỚC 2: OCR song song có giãn nhịp + Tự xoay ảnh chụp ngang 90°/180°/270° ---
    t_ocr_start = time.perf_counter()

    def _ocr_one(item) -> PageProfile:
        idx, fname, raw_bytes, proc_bytes, saved_name, disk_path, q_rep = item
        img_hash = compute_image_hash(raw_bytes)
        # Dùng key cache riêng v3 để có trường paper_brand_detected & orientation_deg
        cache_key = f"cache:ocr:v3:{'live' if is_live_key else 'mock'}:{img_hash}"
        cached = cache.get(cache_key)
        if cached:
            qs = [ExtractedQuestion(**q) for q in cached]
            cache_hit = True
            usage = {"provider": "cache", "model": "cache", "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0, "total_tokens": 0}
        else:
            if is_live_key and idx > 1:
                time.sleep(0.4 * (idx % max(1, workers)))
            qs, usage = vision_agent.extract_latex_with_usage(proc_bytes, applied_transforms=q_rep.applied_transforms)
            cache.set(cache_key, [q.model_dump() for q in qs], ttl_seconds=86400)
            cache_hit = False

        s_name = next((q.student_name_detected.strip() for q in qs if q.student_name_detected.strip()), "")
        s_cls = next((q.student_class_detected.strip() for q in qs if q.student_class_detected.strip()), "")
        p_brand = next((q.paper_brand_detected.strip() for q in qs if q.paper_brand_detected.strip()), "")
        hw_style = next((q.handwriting_style.strip() for q in qs if q.handwriting_style.strip()), "")
        orient = next((q.orientation_deg for q in qs if q.orientation_deg in (90, 180, 270)), 0)

        pre_rot_deg = 0
        for tr in q_rep.applied_transforms:
            m_rot = re.match(r"^auto_rotated_(90|180|270)deg_cw$", tr)
            if m_rot:
                pre_rot_deg = int(m_rot.group(1))
                break

        was_rotated = pre_rot_deg in (90, 180, 270)
        final_proc = proc_bytes
        if was_rotated:
            orient = pre_rot_deg
        elif orient in (90, 180, 270):
            try:
                final_proc = _rotate_jpeg_cw(proc_bytes, orient)
                with open(disk_path, "wb") as f:
                    f.write(final_proc)
                was_rotated = True
                q_rep.applied_transforms.append(f"auto_rotated_{orient}deg_cw")
            except Exception:
                was_rotated = False

        return PageProfile(
            page_index=idx,
            filename=fname,
            raw_bytes=raw_bytes,
            proc_bytes=final_proc,
            saved_filename=saved_name,
            saved_disk_path=disk_path,
            quality=q_rep,
            questions=qs,
            student_name=s_name,
            student_class=s_cls,
            paper_brand=p_brand,
            handwriting_style=hw_style,
            orientation_deg=orient,
            was_rotated=was_rotated,
            cache_hit=cache_hit,
            ocr_usage=usage,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        page_profiles = list(pool.map(_ocr_one, pre_items))
    ocr_ms = int(round((time.perf_counter() - t_ocr_start) * 1000))

    rotated_images = [f"{p.filename} ({p.orientation_deg}°)" for p in page_profiles if p.was_rotated]

    # --- BƯỚC 3: Tự động phân cụm thành từng Học Sinh ---
    student_clusters = cluster_pages_into_students(page_profiles)

    data_dir = os.path.dirname(os.path.abspath(settings.UPLOAD_DIR))
    batch_out_root = os.path.join(data_dir, "outputs", batch_id)
    os.makedirs(batch_out_root, exist_ok=True)

    # --- BƯỚC 4: Chấm riêng từng học sinh & Xuất từng thư mục con HS_01/, HS_02/... ---
    t_sympy_total = 0.0
    t_grade_total = 0.0
    student_records: list[SubmissionGradingRecord] = []

    total_grd_in = 0
    total_grd_out = 0
    total_grd_th = 0
    total_grd_tot = 0
    last_grd_model = "rule-based"

    for s_idx, (s_label, cluster_reason, cl_pages) in enumerate(student_clusters, start=1):
        sub_id = f"{batch_id}_hs{s_idx:02d}"
        per_file_qs = [(p.filename, p.questions) for p in cl_pages]
        reassembled_qs, reassembly_rep = reassemble_extracted_questions(per_file_qs)

        # SymPy kiểm chứng từng câu của học sinh này
        t_s0 = time.perf_counter()
        mv_map: dict[str, MathVerificationSummary] = {}
        eq_c = v_c = inv_c = 0
        roots: list[str] = []
        issues: list[str] = []
        for q in reassembled_qs:
            mv = verify_question_math(q)
            mv_map[q.question_id] = mv
            eq_c += mv.equations_checked
            v_c += mv.valid_transitions
            inv_c += mv.invalid_transitions
            roots.extend(mv.detected_roots)
            issues.extend(f"[{q.question_id}] {iss}" for iss in mv.issues)
        s_sympy_ms = int(round((time.perf_counter() - t_s0) * 1000))
        t_sympy_total += s_sympy_ms

        combined_mv = MathVerificationSummary(
            equations_checked=eq_c,
            valid_transitions=v_c,
            invalid_transitions=inv_c,
            detected_roots=roots,
            issues=issues,
            summary=(
                f"SymPy kiểm chứng {eq_c} PT trên {len(reassembled_qs)} câu: {v_c} bước đúng, {inv_c} bước nghi sai."
                if eq_c > 0
                else "Chưa trích xuất được phương trình đại số đơn giản để SymPy kiểm chứng."
            ),
        )

        # Agent 2 chấm riêng bài học sinh này (Ưu tiên Biểu điểm chuẩn cố định nếu giáo viên đã chọn đề)
        t_g0 = time.perf_counter()
        reasoning_agent.reset_last_usage()
        q_results: list[QuestionGradingResult] = []
        matched_rubric_map: dict[str, ExtractedQuestion] = {}
        exam_title = rubric.title
        is_fixed_rubric = rubric.exam_id != "AUTO" and bool(rubric.questions)
        if not is_fixed_rubric:
            exam_title, q_results = reasoning_agent.grade_auto_rubric_multi(reassembled_qs, mv_map=mv_map)
        else:
            q_results, matched_rubric_map = reasoning_agent.grade_with_exam_rubric(reassembled_qs, rubric, mv_map=mv_map)

        # Lưu usage lần chấm đầu
        g_usage_first = dict(getattr(reasoning_agent, "last_usage", {}))
        reread_grd_in = reread_grd_out = reread_grd_th = reread_grd_tot = 0

        # --- BƯỚC 4b: 2-Pass Forensic OCR Re-Read (tối đa 2 câu nghi sai / học sinh khi dùng Live Key) ---
        reread_ocr_in = reread_ocr_out = reread_ocr_th = reread_ocr_tot = 0
        if is_live_key and q_results:
            page_by_fname = {p.filename: p for p in cl_pages}
            rq_by_id = {rq.question_id: rq for rq in rubric.questions} if is_fixed_rubric else {}
            reread_count = 0
            for q_idx, q_res in enumerate(q_results):
                if reread_count >= 2:
                    break
                sq = matched_rubric_map.get(q_res.question_id) if is_fixed_rubric else next(
                    (q for q in reassembled_qs if q.question_id == q_res.question_id), None
                )
                if not sq or not sq.lines or not sq.source_files:
                    continue
                q_mv = mv_map.get(sq.question_id)
                has_issue = (
                    (q_mv is not None and q_mv.invalid_transitions > 0)
                    or (
                        q_res.suggested_score < (q_res.max_score or q_res.total_points or 1.0)
                        and any(c.status in ("failed", "partial") for c in q_res.criteria_evaluations)
                    )
                )
                if not has_issue:
                    continue
                src_page = page_by_fname.get(sq.source_files[0])
                if not src_page:
                    continue

                reread_count += 1
                expected_prompt = rq_by_id[q_res.question_id].question_text if q_res.question_id in rq_by_id else sq.question_text
                prev_lines = [l.raw_text or l.latex for l in sq.lines]
                updated_q, rr_usage = vision_agent.reread_question_from_image(
                    image_bytes=src_page.proc_bytes,
                    question_id=q_res.question_id,
                    expected_prompt=expected_prompt,
                    previous_lines=prev_lines,
                    suspected_issue=q_res.teacher_summary_comment,
                )
                reread_ocr_in += int(rr_usage.get("input_tokens", 0) or 0)
                reread_ocr_out += int(rr_usage.get("output_tokens", 0) or 0)
                reread_ocr_th += int(rr_usage.get("thinking_tokens", 0) or 0)
                reread_ocr_tot += int(rr_usage.get("total_tokens", 0) or 0)

                if updated_q and updated_q.lines:
                    old_sig = re.sub(r"\s+", "", "".join(prev_lines))
                    new_sig = re.sub(r"\s+", "", "".join(l.raw_text or l.latex for l in updated_q.lines))
                    if new_sig and new_sig != old_sig:
                        for nl in updated_q.lines:
                            nl.source_file = sq.source_files[0]
                        sq.lines = updated_q.lines
                        sq.ocr_warnings.append("2-pass OCR đã soi lại ảnh gốc và hiệu chỉnh nét chữ")
                        new_mv = verify_question_math(sq)
                        mv_map[sq.question_id] = new_mv
                        reasoning_agent.reset_last_usage()
                        if is_fixed_rubric and q_res.question_id in rq_by_id:
                            q_results[q_idx] = reasoning_agent.grade_question(
                                sq, rq_by_id[q_res.question_id], run_self_critique=True, use_llm=True, math_verification=new_mv
                            )
                        else:
                            _, new_res = reasoning_agent.grade_auto_rubric(sq, math_verification=new_mv)
                            new_res.question_id = q_res.question_id
                            new_res.max_score = q_res.max_score
                            new_res.total_points = q_res.total_points
                            q_results[q_idx] = new_res
                        rr_g = dict(getattr(reasoning_agent, "last_usage", {}))
                        reread_grd_in += int(rr_g.get("input_tokens", 0) or 0)
                        reread_grd_out += int(rr_g.get("output_tokens", 0) or 0)
                        reread_grd_th += int(rr_g.get("thinking_tokens", 0) or 0)
                        reread_grd_tot += int(rr_g.get("total_tokens", 0) or 0)

        s_grade_ms = int(round((time.perf_counter() - t_g0) * 1000))
        t_grade_total += s_grade_ms

        g_in = int(g_usage_first.get("input_tokens", 0) or 0) + reread_grd_in
        g_out = int(g_usage_first.get("output_tokens", 0) or 0) + reread_grd_out
        g_th = int(g_usage_first.get("thinking_tokens", 0) or 0) + reread_grd_th
        g_tot = int(g_usage_first.get("total_tokens", 0) or 0) + reread_grd_tot
        g_model = str(g_usage_first.get("model", "rule-based"))
        total_grd_in += g_in
        total_grd_out += g_out
        total_grd_th += g_th
        total_grd_tot += g_tot
        if g_model != "rule-based":
            last_grd_model = g_model

        s_ocr_in = sum(int(p.ocr_usage.get("input_tokens", 0) or 0) for p in cl_pages) + reread_ocr_in
        s_ocr_out = sum(int(p.ocr_usage.get("output_tokens", 0) or 0) for p in cl_pages) + reread_ocr_out
        s_ocr_th = sum(int(p.ocr_usage.get("thinking_tokens", 0) or 0) for p in cl_pages) + reread_ocr_th
        s_ocr_tot = sum(int(p.ocr_usage.get("total_tokens", 0) or 0) for p in cl_pages) + reread_ocr_tot
        s_ocr_model = next((str(p.ocr_usage.get("model")) for p in cl_pages if p.ocr_usage.get("model") not in ("", None, "cache")), "mock")
        s_cost_usd = _estimate_cost_usd(s_ocr_model, s_ocr_in, s_ocr_out + s_ocr_th) + _estimate_cost_usd(g_model, g_in, g_out + g_th)

        # Lưu ảnh riêng cho submission học sinh này để API /submissions/{sub_id}/pages hoạt động 1-1
        student_saved_files: list[str] = []
        student_disk_items: list[tuple[int, str, str]] = []
        for p_idx, p in enumerate(cl_pages, start=1):
            safe_fn = re.sub(r"[^0-9a-zA-Z._-]", "_", p.filename)
            s_fname = f"{sub_id}_p{p_idx}_{safe_fn}"
            s_path = storage.save_file(s_fname, p.proc_bytes, subfolder=rubric.exam_id)
            student_saved_files.append(s_fname)
            student_disk_items.append((p_idx, p.filename, s_path))

        ai_score = round(sum(q.suggested_score for q in q_results), 2)
        max_pts = round(sum(q.total_points or q.max_score for q in q_results), 2) or 10.0
        detected_name = next((p.student_name for p in cl_pages if p.student_name), "")
        needs_rev = any(q.needs_review for q in q_results) or any(p.quality.is_blurry for p in cl_pages) or (not detected_name and len(cl_pages) > 1)

        reassembly_rep.cluster_label = s_label
        reassembly_rep.cluster_reason = cluster_reason
        reassembly_rep.rotated_files = [f"{p.filename} ({p.orientation_deg}°)" for p in cl_pages if p.was_rotated]

        h_sub = hashlib.md5(b"".join(p.raw_bytes for p in cl_pages)).hexdigest()
        s_rec = SubmissionGradingRecord(
            submission_id=sub_id,
            exam_id=rubric.exam_id,
            exam_title=exam_title,
            student_name=s_label,
            detected_student_name=detected_name,
            page_count=len(cl_pages),
            page_files=student_saved_files,
            rubric_version=rubric.rubric_version,
            status=SubmissionStatus.FLAGGED_REVIEW if needs_rev else SubmissionStatus.GRADED_DRAFT,
            image_hash=h_sub,
            cache_hit=all(p.cache_hit for p in cl_pages),
            is_mock=not is_live_key,
            quality=cl_pages[0].quality,
            extracted_questions=reassembled_qs,
            math_verification=combined_mv,
            folder_reassembly=reassembly_rep,
            telemetry=GradingTelemetry(
                total_ms=s_sympy_ms + s_grade_ms,
                preprocess_ms=0,
                ocr_ms=0,
                sympy_ms=s_sympy_ms,
                grading_ms=s_grade_ms,
                provider="gemini" if is_live_key else "mock",
                ocr_model=s_ocr_model,
                grader_model=g_model,
                ocr_input_tokens=s_ocr_in,
                ocr_output_tokens=s_ocr_out,
                ocr_thinking_tokens=s_ocr_th,
                ocr_total_tokens=s_ocr_tot,
                grader_input_tokens=g_in,
                grader_output_tokens=g_out,
                grader_thinking_tokens=g_th,
                grader_total_tokens=g_tot,
                total_input_tokens=s_ocr_in + g_in,
                total_output_tokens=s_ocr_out + g_out,
                total_thinking_tokens=s_ocr_th + g_th,
                total_tokens=s_ocr_tot + g_tot,
                estimated_cost_usd=round(s_cost_usd, 6),
                estimated_cost_vnd=round(s_cost_usd * _VND_PER_USD, 1),
            ),
            question_results=q_results,
            ai_total_score=ai_score,
            total_points=max_pts,
            final_score=ai_score,
            needs_teacher_review=needs_rev,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # Xuất thư mục con riêng cho học sinh này trong `batch_out_root/HS_01_.../`
        sub_folder_name = f"HS_{s_idx:02d}_{_slugify_vi(s_label)[:28]}"
        sub_out_dir = os.path.join(batch_out_root, sub_folder_name)
        tmp_dir, sorted_names = build_sorted_output_folder(s_rec, student_disk_items)
        if os.path.isdir(sub_out_dir):
            shutil.rmtree(sub_out_dir)
        shutil.copytree(tmp_dir, sub_out_dir)

        s_rec.folder_reassembly.output_folder_path = sub_out_dir
        s_rec.folder_reassembly.sorted_output_files = sorted_names
        student_records.append(s_rec)

    # --- BƯỚC 5: Xuất Excel tổng hợp cả lớp + File báo cáo phân loại tại thư mục gốc lô ---
    excel_path = os.path.join(batch_out_root, "00_BANG_DIEM_CA_LOP.xlsx")
    if student_records:
        excel_bytes = export_to_excel(exam_id=rubric.exam_id, records=student_records, format_type="standard")
        with open(excel_path, "wb") as f:
            f.write(excel_bytes)

    total_ocr_in = sum(int(p.ocr_usage.get("input_tokens", 0) or 0) for p in page_profiles) + sum(r.telemetry.ocr_input_tokens for r in student_records if False)
    total_ocr_in = sum(r.telemetry.ocr_input_tokens for r in student_records)
    total_ocr_out = sum(r.telemetry.ocr_output_tokens for r in student_records)
    total_ocr_th = sum(r.telemetry.ocr_thinking_tokens for r in student_records)
    total_ocr_tot = sum(r.telemetry.ocr_total_tokens for r in student_records)
    ocr_model_top = next((str(p.ocr_usage.get("model")) for p in page_profiles if p.ocr_usage.get("model") not in ("", None, "cache")), "mock")

    total_ms = int(round((time.perf_counter() - t_total_start) * 1000))
    batch_cost_usd = _estimate_cost_usd(ocr_model_top, total_ocr_in, total_ocr_out + total_ocr_th) + _estimate_cost_usd(
        last_grd_model, total_grd_in, total_grd_out + total_grd_th
    )

    batch_telemetry = GradingTelemetry(
        total_ms=total_ms,
        preprocess_ms=preprocess_ms,
        ocr_ms=ocr_ms,
        sympy_ms=int(t_sympy_total),
        grading_ms=int(t_grade_total),
        provider="gemini" if is_live_key else "mock",
        ocr_model=ocr_model_top,
        grader_model=last_grd_model,
        ocr_input_tokens=total_ocr_in,
        ocr_output_tokens=total_ocr_out,
        ocr_thinking_tokens=total_ocr_th,
        ocr_total_tokens=total_ocr_tot,
        grader_input_tokens=total_grd_in,
        grader_output_tokens=total_grd_out,
        grader_thinking_tokens=total_grd_th,
        grader_total_tokens=total_grd_tot,
        total_input_tokens=total_ocr_in + total_grd_in,
        total_output_tokens=total_ocr_out + total_grd_out,
        total_thinking_tokens=total_ocr_th + total_grd_th,
        total_tokens=total_ocr_tot + total_grd_tot,
        estimated_cost_usd=round(batch_cost_usd, 6),
        estimated_cost_vnd=round(batch_cost_usd * _VND_PER_USD, 1),
    )

    summary_lines = [
        f"# Báo Cáo Phân Loại & Chấm Cả Folder — `{batch_id}`",
        f"- **Tổng số ảnh quét:** {len(page_profiles)} ảnh",
        f"- **Số học sinh tự động tách được:** **{len(student_records)} học sinh**",
        f"- **Ảnh tự động xoay đứng:** {', '.join(rotated_images) if rotated_images else 'Không có'}",
        f"- **Tổng thời gian:** {total_ms / 1000:.2f}s | **Tổng tokens:** {batch_telemetry.total_tokens:,} | **Tổng chi phí:** ~{batch_telemetry.estimated_cost_vnd}đ VND",
        "",
        "## Danh Sách Thư Mục Từng Học Sinh",
    ]
    for s_idx, rec in enumerate(student_records, start=1):
        sub_name = os.path.basename(rec.folder_reassembly.output_folder_path) if rec.folder_reassembly else rec.submission_id
        q_list = ", ".join(q.question_id for q in rec.extracted_questions)
        summary_lines.append(
            f"{s_idx}. **`{sub_name}/`** — **{rec.student_name}** | {rec.page_count} ảnh | Câu: {q_list} | Điểm: **{rec.ai_total_score}/{rec.total_points}đ** | *{rec.folder_reassembly.cluster_reason if rec.folder_reassembly else ''}*"
        )

    with open(os.path.join(batch_out_root, "00_TONG_HOP_PHAN_LOAI.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines) + "\n")

    summary_str = (
        f"Đã quét {len(page_profiles)} ảnh -> tự động tách thành {len(student_records)} học sinh độc lập"
        + (f" (đã xoay đứng {len(rotated_images)} ảnh chụp ngang)" if rotated_images else "")
        + "."
    )

    return SmartFolderBatchResult(
        batch_id=batch_id,
        exam_id=rubric.exam_id,
        total_images_scanned=len(page_profiles),
        total_students_detected=len(student_records),
        output_root_folder=batch_out_root,
        excel_file_path=excel_path,
        rotated_images=rotated_images,
        telemetry=batch_telemetry,
        students=student_records,
        summary=summary_str,
    )
