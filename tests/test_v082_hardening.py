"""v0.8.2 — Carbon/Argon consensus Top-5 hardening & regression tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import io
import json
from types import SimpleNamespace
import zipfile

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.app.api import routes
from backend.app.core.config import settings
from backend.app.core.database import db
from backend.app.core.storage import LocalStorageEngine
from backend.app.models.schemas import ExtractedLine, ExtractedQuestion, LineType
from backend.app.services import provider_auth
from backend.app.services.agent1_ocr import (
    OCRLowConfidenceError,
    parse_and_validate_ocr_json,
    vision_agent,
)
from backend.app.services.agent2_grader import reasoning_agent
from backend.app.services.batch import batch_manager, extract_zip_images
from backend.app.services.pipeline import grading_pipeline

client = TestClient(routes.router if False else __import__("backend.app.main", fromlist=["app"]).app)


def _tiny_jpeg_bytes(color=(240, 240, 240)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="JPEG")
    return buf.getvalue()


def test_v82_01_admin_guards_and_local_folder_blocked_in_staging(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", "tok-123")
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")

    for ep in ("/api/v1/auth/providers/accounts", "/api/v1/auth/providers/logs", "/api/v1/system/mobile-access"):
        assert client.get(ep).status_code == 403
        assert client.get(ep, headers={"X-Admin-Token": "tok-123"}).status_code == 200

    assert client.post(
        "/api/v1/submissions/grade-folder-path",
        json={"folder_path": "d:/some/path"},
        headers={"X-Admin-Token": "tok-123"},
    ).status_code == 403
    assert client.post(
        "/api/v1/submissions/grade-folder-smart-path",
        json={"folder_path": "d:/some/path"},
        headers={"X-Admin-Token": "tok-123"},
    ).status_code == 403

    ks = client.get("/api/v1/config/key-status").json()
    assert ks["requires_admin_token"] is True


def test_v82_02_exam_id_and_storage_path_traversal_blocked(tmp_path):
    res = client.post(
        "/api/v1/exams",
        json={"exam_id": "../evil", "title": "Bad", "exam_title": "Bad", "total_points": 10.0, "questions": []},
    )
    assert res.status_code == 400

    eng = LocalStorageEngine(base_dir=str(tmp_path))
    with pytest.raises(ValueError):
        eng.save_file("x.jpg", b"abc", subfolder="../escape")


def test_v82_03_ssrf_base_url_validation_and_ollama_local_carveout(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "local")
    with pytest.raises(ValueError):
        provider_auth.create_or_update_api_key_account(
            provider="custom",
            base_url="http://169.254.169.254/latest/meta-data",
            label="Bad Meta",
            activate=False,
        )

    ok_local = provider_auth.create_or_update_api_key_account(
        provider="custom",
        base_url="http://localhost:11434/v1",
        label="Ollama Local",
        activate=False,
    )
    assert ok_local["base_url"] == "http://localhost:11434/v1"
    client.delete(f"/api/v1/auth/providers/accounts/{ok_local['account_id']}")

    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    with pytest.raises(ValueError):
        provider_auth.create_or_update_api_key_account(
            provider="custom",
            base_url="http://localhost:11434/v1",
            label="Blocked Staging Local",
            activate=False,
        )


def test_v82_04_legacy_config_key_preserves_env_and_detects_openrouter(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_API_TOKEN=keep-me\nGOOGLE_OAUTH_CLIENT_ID=keep-cid\nGEMINI_API_KEY=old\n", encoding="utf-8")

    # Test _upsert_env_file preserves untouched keys
    routes._upsert_env_file(str(env_file), {"OPENROUTER_API_KEY": "sk-or-mock_123"})
    content = env_file.read_text(encoding="utf-8")
    assert "ADMIN_API_TOKEN=keep-me" in content
    assert "GOOGLE_OAUTH_CLIENT_ID=keep-cid" in content
    assert "OPENROUTER_API_KEY=sk-or-mock_123" in content

    # Cô lập không cho test ghi vào file .env thật của repo
    redirected_updates = {}
    monkeypatch.setattr(routes, "_upsert_env_file", lambda _p, upd: redirected_updates.update(upd))
    monkeypatch.setattr(settings, "GEMINI_API_KEY", settings.GEMINI_API_KEY)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", settings.OPENAI_API_KEY)
    monkeypatch.setattr(settings, "AI_PROVIDER", settings.AI_PROVIDER)

    bad = client.post("/api/v1/config/key", json={"api_key": "mock_abc\nADMIN_API_TOKEN=pwn"})
    assert bad.status_code == 400

    res = client.post("/api/v1/config/key", json={"api_key": "mock_gemini_safe"})
    assert res.status_code == 200
    assert res.json()["account"]["provider"] == "gemini"
    assert redirected_updates.get("GEMINI_API_KEY") == "mock_gemini_safe"
    client.delete(f"/api/v1/auth/providers/accounts/{res.json()['account']['account_id']}")


def test_v82_05_teacher_final_score_override_on_multi_question_preserves_ai_score():
    img = _tiny_jpeg_bytes()
    rec = grading_pipeline.process_submission(img, routes.get_default_secondary_math_rubric(), student_name="HS Test")
    # Simulate 2-question record saved in DB
    q2 = rec.question_results[0].model_copy(deep=True)
    q2.question_id = "Câu 2"
    rec.question_results[0].question_id = "Câu 1"
    rec.question_results.append(q2)
    rec.total_points = 10.0
    rec.ai_total_score = 6.5
    rec.final_score = 6.5
    db.save_submission(rec)

    res = client.post(
        "/api/v1/submissions/review",
        json={
            "submission_id": rec.submission_id,
            "score_overrides": {},
            "final_score_override": 7.25,
            "teacher_note": "Cộng 0.75 khuyến khích",
            "approve": True,
        },
    )
    assert res.status_code == 200
    updated = res.json()
    assert updated["final_score"] == 7.25
    assert updated["ai_total_score"] == 6.5
    assert updated["status"] == "APPROVED"


def test_v82_06_batch_zip_deduplicates_basenames_and_reruns_do_not_clobber():
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr("lop1/bai1.jpg", _tiny_jpeg_bytes((220, 220, 220)))
        zf.writestr("lop2/bai1.jpg", _tiny_jpeg_bytes((200, 200, 200)))

    extracted = extract_zip_images(zbuf.getvalue())
    names = [n for n, _ in extracted]
    assert len(names) == 2 and len(set(names)) == 2

    rubric = routes.get_default_secondary_math_rubric()
    j1 = batch_manager.create_job(rubric.exam_id, len(extracted))
    j2 = batch_manager.create_job(rubric.exam_id, len(extracted))
    r1 = batch_manager.run_batch_sync(j1.job_id, extracted, rubric)
    r2 = batch_manager.run_batch_sync(j2.job_id, extracted, rubric)
    ids1 = {r.submission_id for r in r1.results}
    ids2 = {r.submission_id for r in r2.results}
    assert len(ids1) == 2 and len(ids2) == 2 and ids1.isdisjoint(ids2)


def test_v82_07_multiquestion_auto_pipeline_splits_questions(monkeypatch):
    grading_pipeline.cache.clear()
    q1 = ExtractedQuestion(
        question_id="Câu 1",
        lines=[ExtractedLine(line_index=0, line_no=1, raw_text="x + 1 = 3", latex="x + 1 = 3", line_type=LineType.EQUATION)],
    )
    q2 = ExtractedQuestion(
        question_id="Câu 2",
        lines=[ExtractedLine(line_index=0, line_no=1, raw_text="2x = 8", latex="2x = 8", line_type=LineType.EQUATION)],
    )
    monkeypatch.setattr(vision_agent, "extract_latex", lambda *a, **kw: [q1, q2])
    monkeypatch.setattr(vision_agent, "extract_latex_multipage", lambda *a, **kw: [q1, q2])

    rec = grading_pipeline.process_multipage_submission([_tiny_jpeg_bytes()], routes.get_auto_rubric(), student_name="Multi AUTO")
    assert len(rec.question_results) == 2
    assert {qr.question_id for qr in rec.question_results} == {"Câu 1", "Câu 2"}


def test_v82_08_ocr_and_grader_null_latex_low_confidence_and_no_model_drift(monkeypatch):
    raw = 'Phản hồi JSON: {"orientation_deg": 0, "questions": [{"question_id": "Q1", "confidence": null, "lines": [{"line_no": 1, "raw_text": "\\triangle ABC \\forall x", "latex": "\\triangle ABC \\forall x", "confidence": null}]}]}'
    qs = parse_and_validate_ocr_json(raw)
    assert len(qs) == 1
    assert "\\triangle" in qs[0].lines[0].latex
    assert "\\forall" in qs[0].lines[0].latex
    assert qs[0].confidence == 0.9

    # OCRLowConfidenceError phải ném 422 thay vì bị nuốt thành 503
    monkeypatch.setattr(vision_agent, "openai_api_key", "sk-fake-live")
    monkeypatch.setattr(
        provider_auth,
        "build_openai_client",
        lambda **kw: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **ckw: SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content='{"questions": []}'))],
                        usage=None,
                    )
                )
            )
        ),
    )
    res = client.post(
        "/api/v1/submissions/grade",
        files={"file": ("blur.jpg", _tiny_jpeg_bytes(), "image/jpeg")},
        data={"exam_id": "AUTO"},
    )
    assert res.status_code == 422

    # Null score trong Grader không crash & self.model_name không bị mutate khi fallback
    parsed_res = reasoning_agent._parse_rubric_response(
        {"suggested_score": None, "confidence": None, "criteria_evaluations": [{"criterion_id": "C1", "score": None, "max_score": 2.0}]},
        routes.get_default_secondary_math_rubric().questions[0],
    )
    assert parsed_res.suggested_score == 0.0

    orig_model = reasoning_agent.model_name
    monkeypatch.setattr(reasoning_agent, "api_key", "AIza-fake")
    fake_client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda model, **kw: (
                (_ for _ in ()).throw(RuntimeError("503 overloaded"))
                if model == orig_model
                else SimpleNamespace(text='{"ok": true}', usage_metadata=None)
            )
        )
    )
    monkeypatch.setattr(provider_auth, "build_gemini_client", lambda **kw: fake_client)
    out = reasoning_agent._generate_gemini_json("test")
    assert out == {"ok": True}
    assert reasoning_agent.model_name == orig_model


def test_v82_09_concurrent_google_oauth_refresh_locked_single_call(monkeypatch):
    expired_iso = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    acct = db.save_provider_account(
        {
            "account_id": "acc_gemini_locktest",
            "provider": "gemini",
            "auth_type": "oauth",
            "label": "Gemini Lock Test",
            "secret_json": json.dumps(
                {
                    "access_token": "ya29.old",
                    "refresh_token": "1//ref",
                    "expires_at": expired_iso,
                    "client_id": "cid",
                    "client_secret": "csec",
                }
            ),
            "is_active": True,
        }
    )
    provider_auth.sync_active_provider_to_runtime()

    calls = 0

    def fake_post(url, data=None, timeout=20.0, **kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            status_code=200,
            text="ok",
            json=lambda: {"access_token": "ya29.fresh_once", "expires_in": 3600},
        )

    monkeypatch.setattr(provider_auth.httpx, "post", fake_post)
    creds = json.loads(acct["secret_json"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: provider_auth.refresh_google_oauth_token("acc_gemini_locktest", creds), range(4)))

    assert calls == 1
    assert all(r["access_token"] == "ya29.fresh_once" for r in results)

    # Transient 503 marks status='error' instead of 'expired'
    def fake_post_503(url, data=None, timeout=20.0, **kwargs):
        return SimpleNamespace(status_code=503, text="backend unavailable")

    monkeypatch.setattr(provider_auth.httpx, "post", fake_post_503)
    with pytest.raises(RuntimeError):
        provider_auth.refresh_google_oauth_token("acc_gemini_locktest", results[0])
    assert db.get_provider_account("acc_gemini_locktest")["status"] == "error"

    db.delete_provider_account("acc_gemini_locktest")
    provider_auth.sync_active_provider_to_runtime()
