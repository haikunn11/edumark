from __future__ import annotations

import re
from typing import Any

import sympy as sp
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application,
    convert_xor,
)

from backend.app.models.schemas import ExtractedQuestion, LineType, MathVerificationSummary

_TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)


def _latex_frac_to_ascii(expr: str) -> str:
    """Chuyển \\frac{a}{b} (kể cả lồng 1 cấp) thành ((a)/(b))."""
    pattern = re.compile(r"\\frac\{([^{}]+)\}\{([^{}]+)\}")
    prev = None
    cur = expr
    while prev != cur:
        prev = cur
        cur = pattern.sub(r"((\1)/(\2))", cur)
    return cur


def _normalize_math_fragment(raw: str) -> str:
    """Chuẩn hóa một mảnh phương trình LaTeX/text sang chuỗi SymPy parse được."""
    s = raw.strip()
    if not s:
        return ""
    s = s.replace("$", "")
    s = re.sub(r"\\text\{[^}]*\}", " ", s)
    s = re.sub(r"\\left|\\right|\\,|\\;|\\:|\\!", "", s)
    s = _latex_frac_to_ascii(s)
    s = re.sub(r"\\sqrt\{([^{}]+)\}", r"sqrt(\1)", s)
    s = s.replace("\\cdot", "*").replace("\\times", "*").replace("×", "*")
    s = s.replace("\\div", "/").replace("÷", "/")
    s = s.replace("−", "-").replace("–", "-")
    # Loại bỏ các lời dẫn tiếng Việt phổ biến trước biểu thức
    s = re.sub(
        r"^(?:ta có|suy ra|vậy|tương đương|khi đó|do đó|giải|đkxđ|điều kiện|kết luận|cách\s*\d+|c\d+)\s*[:.]?\s*",
        "",
        s,
        flags=re.IGNORECASE,
    )
    # Loại bỏ phần chú thích tiếng Việt trong ngoặc cuối dòng, ví dụ "(thỏa mãn)", "(tinh sai)"
    s = re.sub(r"\([^)]*[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ][^)]*\)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\((?:thoa man|tinh sai|tm|loai|nhan|dkxd)[^)]*\)", "", s, flags=re.IGNORECASE)
    s = s.strip(" .,;:")
    return s


def _parse_single_equation(eq_text: str) -> tuple[sp.Eq, set[sp.Symbol]] | None:
    """Parse một phương trình 'LHS = RHS' thành SymPy Eq. Trả về None nếu không parse được."""
    cleaned = _normalize_math_fragment(eq_text)
    if "=" not in cleaned:
        return None
    # Bỏ qua tập nghiệm S = {3}, bất đẳng thức !=, <=, >=
    if any(tok in cleaned for tok in ("!=", "\\neq", "≠", "<=", ">=", "{", "}", "\\{")):
        return None
    parts = cleaned.split("=")
    if len(parts) != 2:
        return None
    lhs_s, rhs_s = parts[0].strip(), parts[1].strip()
    if not lhs_s or not rhs_s:
        return None
    # Chỉ nhận biểu thức toán học sạch (ký tự biến ASCII, số, toán tử, hàm sqrt)
    allowed = re.compile(r"^[0-9a-zA-Z\s\+\-\*/\^\(\)\.]+$")
    if not (allowed.match(lhs_s) and allowed.match(rhs_s)):
        return None
    try:
        local_dict: dict[str, Any] = {
            "x": sp.Symbol("x"),
            "y": sp.Symbol("y"),
            "z": sp.Symbol("z"),
            "t": sp.Symbol("t"),
            "m": sp.Symbol("m"),
            "k": sp.Symbol("k"),
            "sqrt": sp.sqrt,
        }
        lhs = parse_expr(lhs_s, local_dict=local_dict, transformations=_TRANSFORMS)
        rhs = parse_expr(rhs_s, local_dict=local_dict, transformations=_TRANSFORMS)
        eq = sp.Eq(lhs, rhs)
        syms = eq.free_symbols
        if not syms or len(syms) > 2:
            return None
        return eq, syms
    except Exception:
        return None


def _extract_equations_from_line(text: str) -> list[tuple[str, sp.Eq, set[sp.Symbol]]]:
    """Tách các bước phương trình trên cùng 1 dòng ngăn bởi <=> hoặc =>."""
    chunks = re.split(r"\\Leftrightarrow|\\Rightarrow|<=>|=>|⇔|⇒", text)
    parsed: list[tuple[str, sp.Eq, set[sp.Symbol]]] = []
    for chunk in chunks:
        res = _parse_single_equation(chunk)
        if res is not None:
            eq, syms = res
            parsed.append((chunk.strip(), eq, syms))
    return parsed


def _solve_univariate(eq: sp.Eq, sym: sp.Symbol) -> set[sp.Expr] | None:
    """Giải phương trình 1 ẩn trả về tập nghiệm chuẩn hóa."""
    try:
        sols = sp.solve(eq, sym)
        return {sp.simplify(s) for s in sols if s.is_real is not False}
    except Exception:
        return None


def _normalize_numeric_expr(raw: str) -> str:
    """Chuẩn hóa biểu thức số học, tiền tệ, phần trăm (20%) và góc hình học (180° - 70°)."""
    s = (raw or "").strip()
    if not s:
        return ""
    # Nếu có lời dẫn kiểu "Số tiền còn lại: 700.000 - 640.000" -> lấy phần sau dấu hai chấm cuối
    if ":" in s:
        left_c, right_c = s.rsplit(":", 1)
        if re.search(r"[a-zA-Zà-ỹÀ-Ỹ]", left_c) and re.search(r"\d", right_c):
            s = right_c.strip()

    s = s.replace("$", "")
    s = re.sub(r"\\text\{[^}]*\}", " ", s)
    s = re.sub(r"\\left|\\right|\\,|\\;|\\:|\\!", "", s)
    s = _latex_frac_to_ascii(s)
    s = re.sub(r"\\sqrt\{([^{}]+)\}", r"sqrt(\1)", s)
    s = s.replace("\\cdot", "*").replace("\\times", "*").replace("×", "*")
    s = s.replace("\\div", "/").replace("÷", "/")
    s = s.replace("−", "-").replace("–", "-")
    # Bỏ ký hiệu độ của góc (180^\circ -> 180, 70° -> 70)
    s = re.sub(r"\^\{?\s*\\circ\s*\}?|\\circ|°", "", s)
    # Bỏ đơn vị phổ biến tiếng Việt (đồng, đ, VND, cm, m...)
    s = re.sub(r"\((?:đồng|đ|vnd|cm|mm|dm|m|km|g|kg)\)", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(?:đồng|vnd|cm|mm|dm|km|kg)\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"(?<=\d)\s*đ\b", "", s, flags=re.IGNORECASE)
    # Chuyển dấu phân cách hàng nghìn VN: 250.000 -> 250000, 1.200.000 -> 1200000, 500 000 -> 500000
    s = re.sub(r"(?<!\d)(\d{1,3}(?:\.\d{3})+)(?!\d)", lambda m: m.group(1).replace(".", ""), s)
    s = re.sub(r"(?<!\d)(\d{1,3}(?:\s+\d{3})+)(?!\d)", lambda m: re.sub(r"\s+", "", m.group(1)), s)
    # Dấu chấm viết tay trước phần trăm hoặc sau ngoặc đóng: "(800000).20%" -> "(800000)*20%"
    s = re.sub(r"\)\s*\.\s*(?=\d|\()", ")*", s)
    # Chuyển phần trăm 20% -> *(20/100) nếu dính sau số, hoặc (20/100) nếu đứng riêng
    s = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", s)
    s = s.strip(" .,;:")
    return s


def _check_arithmetic_line(text: str) -> list[tuple[str, bool, str]]:
    """
    Kiểm chứng tính đúng/sai của các phép tính số học, tiền tệ, phần trăm và số đo góc
    (ví dụ: `250.000 * 2 = 500.000 đ` hoặc `\\widehat{A_2} = 180° - 70° = 110°`).
    Trả về list[(expr_desc, is_valid, issue_msg)].
    """
    results: list[tuple[str, bool, str]] = []
    pure_num_re = re.compile(r"^[0-9\s\+\-\*/\^\(\)\.]+$")
    chunks = re.split(r"\\Leftrightarrow|\\Rightarrow|<=>|=>|⇔|⇒", text or "")

    for chunk in chunks:
        if "=" not in chunk:
            continue
        if any(tok in chunk for tok in ("!=", "\\neq", "≠", "<=", ">=", "\\{", "\\}")) or re.search(r"s\s*=\s*\{", chunk, re.IGNORECASE):
            continue
        segs = chunk.split("=")
        norm_segs: list[tuple[str, str]] = []
        for seg in segs:
            ns = _normalize_numeric_expr(seg)
            if ns and pure_num_re.match(ns) and re.search(r"\d", ns):
                norm_segs.append((seg.strip(), ns))

        for i in range(len(norm_segs) - 1):
            raw_a, expr_a = norm_segs[i]
            raw_b, expr_b = norm_segs[i + 1]
            # Chỉ kiểm tra nếu có ít nhất 1 phép toán hoặc cả 2 vế là số khác nhau
            has_op = any(op in (expr_a + expr_b) for op in ("+", "-", "*", "/"))
            if not has_op and expr_a.strip() == expr_b.strip():
                continue
            try:
                va = complex(sp.N(parse_expr(expr_a, transformations=_TRANSFORMS)))
                vb = complex(sp.N(parse_expr(expr_b, transformations=_TRANSFORMS)))
                if abs(va.imag) > 1e-6 or abs(vb.imag) > 1e-6:
                    continue
                if abs(va.real - vb.real) <= 1e-4:
                    results.append((f"{raw_a} = {raw_b}", True, ""))
                else:
                    exp_fmt = int(round(va.real)) if abs(va.real - round(va.real)) < 1e-6 else round(va.real, 4)
                    got_fmt = int(round(vb.real)) if abs(vb.real - round(vb.real)) < 1e-6 else round(vb.real, 4)
                    results.append(
                        (
                            f"{raw_a} = {raw_b}",
                            False,
                            f"Tính toán sai ('{raw_a} = {raw_b}'): giá trị đúng là {exp_fmt}, nhưng bài viết ra {got_fmt}.",
                        )
                    )
            except Exception:
                continue

    return results


def verify_question_math(question: ExtractedQuestion) -> MathVerificationSummary:
    """
    Kiểm chứng toán học tất định bằng SymPy cho toàn bộ các dòng lời giải:
    - Đại số (phương trình 1 ẩn tương đương qua từng dòng)
    - Số học, tiền tệ, phần trăm (hàng nghìn `250.000`, `20%`)
    - Góc hình học (`180° - 70° = 110°`)
    (tự động bỏ qua các dòng học sinh đã gạch bỏ `is_strikethrough` / `SCRATCH`).
    """
    active_lines = [
        line for line in question.lines
        if not line.is_strikethrough and line.line_type != LineType.SCRATCH
    ]

    steps: list[tuple[int, str, sp.Eq, set[sp.Symbol]]] = []
    arith_checks: list[tuple[int, str, bool, str]] = []
    for line in active_lines:
        candidate = line.latex or line.raw_latex or line.raw_text
        if not candidate:
            continue
        ln_no = line.line_no or (line.line_index + 1)
        eq_hits = _extract_equations_from_line(candidate)
        if eq_hits:
            for raw_chunk, eq, syms in eq_hits:
                steps.append((ln_no, raw_chunk, eq, syms))
        else:
            for desc, ok, msg in _check_arithmetic_line(candidate):
                arith_checks.append((ln_no, desc, ok, msg))

    if not steps and not arith_checks:
        return MathVerificationSummary(
            equations_checked=0,
            summary="Chưa trích xuất được phương trình đại số đơn giản để SymPy kiểm chứng tự động.",
        )

    valid_transitions = 0
    invalid_transitions = 0
    issues: list[str] = []
    detected_roots: list[str] = []

    prev_sols: set[sp.Expr] | None = None
    prev_sym: sp.Symbol | None = None
    prev_line_no: int | None = None

    for line_no, raw_chunk, eq, syms in steps:
        if len(syms) != 1:
            continue
        sym = next(iter(syms))
        sols = _solve_univariate(eq, sym)
        if sols is None:
            continue

        if eq.lhs == sym and not eq.rhs.free_symbols:
            detected_roots.append(f"{sym} = {sp.simplify(eq.rhs)}")

        if prev_sols is not None and prev_sym == sym:
            if sols == prev_sols or (sols and sols.issubset(prev_sols)):
                valid_transitions += 1
            else:
                invalid_transitions += 1
                prev_str = ", ".join(str(s) for s in sorted(prev_sols, key=str)) or "∅"
                curr_str = ", ".join(str(s) for s in sorted(sols, key=str)) or "∅"
                issues.append(
                    f"Dòng {line_no} ('{raw_chunk}'): Tập nghiệm đổi từ {{{prev_str}}} (dòng {prev_line_no}) sang {{{curr_str}}} — nghi ngờ sai biến đổi/tính toán."
                )

        prev_sols = sols
        prev_sym = sym
        prev_line_no = line_no

    for line_no, desc, ok, msg in arith_checks:
        if ok:
            valid_transitions += 1
        else:
            invalid_transitions += 1
            issues.append(f"Dòng {line_no}: {msg}")

    total_checked = len(steps) + len(arith_checks)
    if invalid_transitions > 0:
        summary = (
            f"SymPy kiểm tra {total_checked} biểu thức: phát hiện {invalid_transitions} bước biến đổi/tính toán sai. "
            + " ".join(issues[:2])
        )
    elif valid_transitions > 0:
        summary = (
            f"SymPy xác nhận {valid_transitions}/{valid_transitions} bước biến đổi & tính toán chính xác."
        )
    else:
        summary = f"SymPy đã đọc {total_checked} phương trình toán học hợp lệ."

    return MathVerificationSummary(
        equations_checked=total_checked,
        valid_transitions=valid_transitions,
        invalid_transitions=invalid_transitions,
        detected_roots=detected_roots,
        issues=issues,
        summary=summary,
    )
