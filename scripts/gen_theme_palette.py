"""Sinh khối biến màu Tailwind cho theme Floating Cards (v0.7.0).

Ghi đè đoạn giữa hai mốc `/* @palette:start */` và `/* @palette:end */`
trong backend/app/static/theme.css. Xem planning/03_2026-11-27_ui-floating-cards/specs/SPEC-P01-theme-layer.md.

Chạy:  python scripts/gen_theme_palette.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

THEME_CSS = Path(__file__).resolve().parent.parent / "backend" / "app" / "static" / "theme.css"
SHADES = ["50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"]

# Chế độ Tối = bảng màu Tailwind v3.4.17 gốc (tái hiện giao diện v0.6).
TAILWIND_DARK: dict[str, dict[str, str]] = {
    "slate": {"50": "#f8fafc", "100": "#f1f5f9", "200": "#e2e8f0", "300": "#cbd5e1", "400": "#94a3b8", "500": "#64748b", "600": "#475569", "700": "#334155", "800": "#1e293b", "900": "#0f172a", "950": "#020617"},
    "indigo": {"50": "#eef2ff", "100": "#e0e7ff", "200": "#c7d2fe", "300": "#a5b4fc", "400": "#818cf8", "500": "#6366f1", "600": "#4f46e5", "700": "#4338ca", "800": "#3730a3", "900": "#312e81", "950": "#1e1b4b"},
    "sky": {"50": "#f0f9ff", "100": "#e0f2fe", "200": "#bae6fd", "300": "#7dd3fc", "400": "#38bdf8", "500": "#0ea5e9", "600": "#0284c7", "700": "#0369a1", "800": "#075985", "900": "#0c4a6e", "950": "#082f49"},
    "cyan": {"50": "#ecfeff", "100": "#cffafe", "200": "#a5f3fc", "300": "#67e8f9", "400": "#22d3ee", "500": "#06b6d4", "600": "#0891b2", "700": "#0e7490", "800": "#155e75", "900": "#164e63", "950": "#083344"},
    "teal": {"50": "#f0fdfa", "100": "#ccfbf1", "200": "#99f6e4", "300": "#5eead4", "400": "#2dd4bf", "500": "#14b8a6", "600": "#0d9488", "700": "#0f766e", "800": "#115e59", "900": "#134e4a", "950": "#042f2e"},
    "emerald": {"50": "#ecfdf5", "100": "#d1fae5", "200": "#a7f3d0", "300": "#6ee7b7", "400": "#34d399", "500": "#10b981", "600": "#059669", "700": "#047857", "800": "#065f46", "900": "#064e3b", "950": "#022c22"},
    "amber": {"50": "#fffbeb", "100": "#fef3c7", "200": "#fde68a", "300": "#fcd34d", "400": "#fbbf24", "500": "#f59e0b", "600": "#d97706", "700": "#b45309", "800": "#92400e", "900": "#78350f", "950": "#451a03"},
    "rose": {"50": "#fff1f2", "100": "#ffe4e6", "200": "#fecdd3", "300": "#fda4af", "400": "#fb7185", "500": "#f43f5e", "600": "#e11d48", "700": "#be123c", "800": "#9f1239", "900": "#881337", "950": "#4c0519"},
    "violet": {"50": "#f5f3ff", "100": "#ede9fe", "200": "#ddd6fe", "300": "#c4b5fd", "400": "#a78bfa", "500": "#8b5cf6", "600": "#7c3aed", "700": "#6d28d9", "800": "#5b21b6", "900": "#4c1d95", "950": "#2e1065"},
    "purple": {"50": "#faf5ff", "100": "#f3e8ff", "200": "#e9d5ff", "300": "#d8b4fe", "400": "#c084fc", "500": "#a855f7", "600": "#9333ea", "700": "#7e22ce", "800": "#6b21a8", "900": "#581c87", "950": "#3b0764"},
}

# Chế độ Sáng: slate đảo ramp (950 = nền app, 900 = bề mặt card, 100 = chữ đậm).
LIGHT_SLATE = {
    "50": "#1D2129", "100": "#262A33", "200": "#3A4250", "300": "#5B6575", "400": "#6E7A8A", "500": "#94A0B0",
    "600": "#CBD5E1", "700": "#E3E9F0", "800": "#F4F7FA", "900": "#FFFFFF", "950": "#DCE4EC",
}

# (màu gốc 500, màu nút 600) theo nghĩa — SPEC-P01 §1.
LIGHT_SEMANTIC = {
    "indigo": ("#3382BF", "#3382BF"),   # blue — hành động chính
    "sky": ("#4798D0", "#3F8DC6"),      # blue-2 — thông tin
    "cyan": ("#54C2D8", "#2C9DB5"),     # cyan — AI/OCR
    "teal": ("#8E6BD8", "#7B57CB"),     # purple — icon tính năng
    "violet": ("#8E6BD8", "#7B57CB"),
    "purple": ("#8E6BD8", "#7B57CB"),
    "emerald": ("#6FB043", "#5C9B33"),  # green — đúng/duyệt
    "amber": ("#F6AF2F", "#C98210"),    # orange — cần xem
    "rose": ("#CF2967", "#C02060"),     # magenta — lỗi
}


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _mix(hex_color: str, target: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    r, g, b = _rgb(hex_color)
    return tuple(round(c + (t - c) * amount) for c, t in zip((r, g, b), target))  # type: ignore[return-value]


WHITE, BLACK = (255, 255, 255), (0, 0, 0)


def light_ramp(base: str, button: str) -> dict[str, tuple[int, int, int]]:
    """Ramp đảo vai trò: 50–400 thành chữ đậm, 800–950 thành nền nhạt (SPEC-P01 §1).
    UI v0.6 chỉ dùng 50/100 làm màu CHỮ sáng trên nền tối (đo 2026-11-27: 3/3 lần là text-*)."""
    return {
        "50": _mix(base, BLACK, 0.55), "100": _mix(base, BLACK, 0.50),
        "200": _mix(base, BLACK, 0.45), "300": _mix(base, BLACK, 0.38), "400": _mix(base, BLACK, 0.25),
        "500": _rgb(base), "600": _rgb(button), "700": _mix(base, BLACK, 0.15),
        "800": _mix(base, WHITE, 0.72), "900": _mix(base, WHITE, 0.86), "950": _mix(base, WHITE, 0.92),
    }


def build_block() -> str:
    def fmt(rgb: tuple[int, int, int]) -> str:
        return f"{rgb[0]} {rgb[1]} {rgb[2]}"

    light, dark = [], []
    for fam in TAILWIND_DARK:
        ramp = {s: _rgb(v) for s, v in LIGHT_SLATE.items()} if fam == "slate" else light_ramp(*LIGHT_SEMANTIC[fam])
        light.append("  " + " ".join(f"--tw-{fam}-{s}: {fmt(ramp[s])};" for s in SHADES))
        dark.append("  " + " ".join(f"--tw-{fam}-{s}: {fmt(_rgb(TAILWIND_DARK[fam][s]))};" for s in SHADES))
    return (
        "/* @palette:start — sinh bởi scripts/gen_theme_palette.py, CẤM sửa tay */\n"
        ":root {\n" + "\n".join(light) + "\n}\n"
        "html.dark {\n" + "\n".join(dark) + "\n}\n"
        "/* @palette:end */"
    )


def main() -> None:
    try:  # console Windows cp1252 không in được tiếng Việt
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    css = THEME_CSS.read_text(encoding="utf-8")
    pattern = re.compile(r"/\* @palette:start.*?/\* @palette:end \*/", re.S)
    if not pattern.search(css):
        raise SystemExit(f"Không thấy mốc @palette trong {THEME_CSS}")
    THEME_CSS.write_text(pattern.sub(lambda _m: build_block(), css), encoding="utf-8")
    print(f"OK: đã ghi khối màu {len(TAILWIND_DARK)} họ × {len(SHADES)} sắc vào {THEME_CSS.name}")


if __name__ == "__main__":
    main()
