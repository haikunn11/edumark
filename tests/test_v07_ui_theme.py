"""v0.7.0 — Giao diện Floating Cards (planning/03_2026-11-27_ui-floating-cards/specs/TESTING-ACCEPTANCE.md)."""
import json
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)

FAMILIES = ["slate", "indigo", "sky", "cyan", "teal", "emerald", "amber", "rose", "violet", "purple"]
SHADES = ["50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"]

# Toàn bộ id tĩnh của v0.6 (commit 3db70cb) — bất biến B1 (00-ARCHITECTURE §3)
V06_IDS_INDEX = [
    'active-page-pill', 'ai-feedback-box', 'api-dot', 'api-key-modal', 'api-key-msg', 'api-label',
    'badge-confidence', 'blur-warning', 'btn-api-key', 'btn-approve', 'btn-download-class-excel',
    'btn-download-class-zip', 'btn-download-sorted-zip', 'btn-generate-rubric', 'btn-override',
    'btn-save-key', 'btn-submit-folder', 'btn-submit-upload', 'btn-toggle-raw', 'btn-toggle-scope',
    'cache-badge', 'class-batch-bar', 'class-batch-folder', 'class-batch-meta', 'class-batch-title',
    'class-student-pills', 'criteria-list', 'dark-icon', 'display-total-score', 'duplicate-q-warning',
    'error-prop-banner', 'error-prop-text', 'exam-title-header', 'folder-cluster-mode', 'folder-dropzone',
    'folder-error', 'folder-exam-id', 'folder-file-input', 'folder-file-summary', 'folder-modal',
    'folder-path-input', 'folder-student-name', 'folder-student-name-wrap', 'ftab-btn-path',
    'ftab-btn-upload', 'ftab-path', 'ftab-upload', 'gb-add-col-name', 'gb-btn-fill-batch',
    'gb-btn-fill-current', 'gb-controls-bar', 'gb-download-link', 'gb-new-cols', 'gb-new-name',
    'gb-new-students', 'gb-note-col', 'gb-panel-create', 'gb-panel-upload', 'gb-score-col', 'gb-select',
    'gb-status-msg', 'gb-table-container', 'gb-upload-file', 'gb-upload-newcol', 'gradebook-modal',
    'history-modal', 'history-modal-list', 'image-container', 'image-placeholder', 'image-viewport',
    'input-api-key', 'input-exam-id', 'input-file', 'input-override-note', 'input-override-score',
    'input-student-name', 'loading-pill', 'matrix-page-count', 'mob-qr-cert-badge', 'mob-qr-http',
    'mob-qr-https', 'mob-qr-img', 'mobile-qr-modal', 'mock-banner', 'mock-warning-card', 'modal-ai-score',
    'ocr-line-count', 'ocr-lines-container', 'override-modal', 'page-filmstrip', 'page-question-matrix',
    'paper-image', 'question-id-title', 'reassembly-badge', 'reassembly-banner', 'reassembly-chips',
    'reassembly-output-path', 'reassembly-output-row', 'reassembly-summary', 'rtab-btn-matrix',
    'rtab-btn-score', 'rtab-btn-verify', 'rtab-matrix', 'rtab-score', 'rtab-verify', 'rubric-error',
    'rubric-extra-notes', 'rubric-files-input', 'rubric-grade-input', 'rubric-modal', 'rubric-preview-box',
    'rubric-preview-id', 'rubric-preview-questions', 'rubric-text-input', 'rubric-title-input',
    'rubric-total-input', 'rubtab-btn-files', 'rubtab-btn-text', 'rubtab-files', 'rubtab-text',
    'status-display', 'student-name-display', 'sympy-banner', 'tel-cost', 'tel-cost-usd', 'tel-grade-model',
    'tel-grade-s', 'tel-grade-tokens', 'tel-ocr-model', 'tel-ocr-s', 'tel-ocr-tokens', 'tel-pre-ms',
    'tel-sympy-ms', 'tel-total-s', 'tel-total-tokens', 'telemetry-panel', 'upload-error', 'upload-modal',
    'verify-ocr-groups', 'verify-scope-label', 'zoom-level-text',
]

V06_IDS_MOBILE = [
    'btn-grade-now', 'cam-counter-pill', 'cam-done-count', 'camera-error-box', 'camera-error-msg',
    'camera-flash', 'camera-video', 'capture-buttons', 'capture-limit-label', 'chk-continuous', 'exam-select',
    'gallery-input', 'grade-btn-text', 'history-list', 'live-camera-box', 'mob-batch-excel', 'mob-batch-meta',
    'mob-batch-pills', 'mob-batch-title', 'mob-gb-col-select', 'mob-gb-download', 'mob-gb-row-select',
    'mob-gb-select', 'mob-gb-status', 'mob-mode-class', 'mob-mode-single', 'mobile-class-bar',
    'native-camera-input', 'nav-capture', 'nav-history', 'nav-result', 'override-score-input',
    'page-count-label', 'page-max-label', 'pages-tray', 'pages-tray-wrapper', 'provider-pill',
    'quality-banner', 'res-comment', 'res-criteria', 'res-exam-title', 'res-max', 'res-meta', 'res-ocr-count',
    'res-ocr-lines', 'res-score', 'res-status-badge', 'res-student-name', 'res-sympy-box',
    'res-telemetry-box', 'result-content', 'result-empty', 'student-name-input', 'tab-capture', 'tab-history',
    'tab-result', 'teacher-note-input',
]


def _html_ids(html: str) -> set:
    return set(re.findall(r'\bid="([A-Za-z0-9_-]+)"', html))


# V7-01
@pytest.mark.parametrize("path", ["/", "/app"])
def test_v7_01_pages_load_theme_after_tailwind(path):
    res = client.get(path)
    assert res.status_code == 200
    html = res.text
    assert "v0.7" in html
    assert '/static/theme.css' in html
    tw = html.index("https://cdn.tailwindcss.com")
    th = html.index('/static/theme.js')
    assert tw < th, "theme.js phải nạp SAU Tailwind CDN"
    assert "tailwind.config" not in html, "cấu hình Tailwind chỉ được đặt trong theme.js"


# V7-02
def test_v7_02_theme_assets_and_service_worker():
    css = client.get("/static/theme.css")
    js = client.get("/static/theme.js")
    sw = client.get("/static/sw.js")
    assert css.status_code == js.status_code == sw.status_code == 200
    text = css.text
    root = text[text.index(":root {"):text.index("html.dark {")]
    dark = text[text.index("html.dark {"):text.index("/* @palette:end */")]
    for fam in FAMILIES:
        for shade in SHADES:
            pat = re.compile(rf"--tw-{fam}-{shade}: \d{{1,3}} \d{{1,3}} \d{{1,3}};")
            assert pat.search(root), f"thiếu biến sáng {fam}-{shade}"
            assert pat.search(dark), f"thiếu biến tối {fam}-{shade}"
    # Sáng: nền app = #DCE4EC; Tối: giữ Tailwind slate-950 gốc (#020617)
    assert "--tw-slate-950: 220 228 236;" in root
    assert "--tw-slate-950: 2 6 23;" in dark
    assert "rgb(var(--tw-" in js.text and "<alpha-value>" in js.text
    assert "edumark-pwa-v0.7.0" in sw.text
    assert "/static/theme.css" in sw.text and "/static/theme.js" in sw.text


# V7-03
def test_v7_03_all_v06_ids_preserved():
    idx = _html_ids(client.get("/").text)
    mob = _html_ids(client.get("/app").text)
    missing_idx = sorted(set(V06_IDS_INDEX) - idx)
    missing_mob = sorted(set(V06_IDS_MOBILE) - mob)
    assert not missing_idx, f"index.html mất id: {missing_idx}"
    assert not missing_mob, f"mobile.html mất id: {missing_mob}"


# V7-04
@pytest.mark.parametrize("path", ["/", "/app"])
def test_v7_04_no_hardcoded_dark_class(path):
    html = client.get(path).text
    assert re.search(r'<html[^>]*class="[^"]*\bdark\b', html) is None


# V7-05
def test_v7_05_new_functions_present():
    idx = client.get("/").text
    for fn in ["function computeAttention", "function renderAttentionBar", "function loadHomeRecent", "function toggleDarkMode"]:
        assert fn in idx
    for el in ["attention-bar", "home-recent", "btn-new-grade", "new-grade-menu", "chip-time", "chip-cost"]:
        assert f'id="{el}"' in idx
    assert "function toggleThemeMobile" in client.get("/app").text


def _extract_compute_attention(html: str) -> str:
    start = html.index("function computeAttention(data) {")
    end = html.index("let currentAttention", start)
    return html[start:end]


# V7-06
@pytest.mark.skipif(shutil.which("node") is None, reason="cần Node.js để chạy JS của trang")
def test_v7_06_compute_attention_on_real_shapes():
    fn = _extract_compute_attention(client.get("/").text)
    crit = lambda e, m=1.0, st="passed", fb="": {"earned_points": e, "max_points": m, "status": st, "feedback": fb}
    cases = {
        # B.Nam (class_a50039a2_hs24): câu 1d tiêu chí 0.8 nhưng câu ghi 1.0
        "bnam": {"ai_total_score": 4.0, "final_score": None, "total_points": 4.0, "question_results": [
            {"question_id": "Câu 1a", "suggested_score": 1.0, "max_score": 1.0, "criteria_evaluation": [crit(1.0)]},
            {"question_id": "Câu 1d", "suggested_score": 1.0, "max_score": 1.0,
             "criteria_evaluation": [crit(0.8, st="passed", fb="Thiếu kết quả cuối cùng")]},
        ]},
        # Bùi Trọng Hiếu (hs22): 12.00 / 3.0đ
        "over": {"ai_total_score": 12.0, "final_score": None, "total_points": 3.0, "question_results": [
            {"question_id": "Câu 1a", "suggested_score": 4.0, "max_score": 1.0, "criteria_evaluation": [crit(4.0)]},
        ]},
        "clean": {"ai_total_score": 2.0, "final_score": None, "total_points": 2.0, "question_results": [
            {"question_id": "Câu 1", "suggested_score": 2.0, "max_score": 2.0, "criteria_evaluation": [crit(1.0), crit(1.0)]},
        ]},
        "review": {"ai_total_score": 1.0, "final_score": None, "total_points": 2.0, "question_results": [
            {"question_id": "Câu 2", "suggested_score": 1.0, "max_score": 2.0, "needs_review": True,
             "review_reasons": ["Chữ mờ ở dòng 3"], "criteria_evaluation": [crit(1.0, 2.0, "partial")]},
        ]},
    }
    script = fn + "\nconst cases = " + json.dumps(cases, ensure_ascii=False) + ";\n" \
        "const out = {}; for (const k in cases) out[k] = computeAttention(cases[k]);\n" \
        "process.stdout.write(JSON.stringify(out));"
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)

    assert [a["level"] for a in out["bnam"]] == ["warn"]
    assert out["bnam"][0]["qid"] == "Câu 1d"
    assert "0.80" in out["bnam"][0]["title"] and "1.00" in out["bnam"][0]["title"]

    assert out["over"][0]["level"] == "error" and out["over"][0]["qid"] is None
    assert "vượt barem" in out["over"][0]["title"]
    assert any(a["qid"] == "Câu 1a" and a["level"] == "error" for a in out["over"])

    assert out["clean"] == []

    assert out["review"] == [{"level": "warn", "qid": "Câu 2", "title": "Câu 2 cần thầy cô xem", "detail": "Chữ mờ ở dòng 3"}]
