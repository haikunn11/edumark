"""v0.8.0 — AI Provider Auth Hub (OpenRouter OAuth PKCE, Google Gemini OAuth2 + Refresh, Multi-Account & Audit Log)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from backend.app.core.config import settings
from backend.app.core.database import db
from backend.app.main import app
from backend.app.services import provider_auth
from backend.app.services.agent1_ocr import vision_agent
from backend.app.services.agent2_grader import reasoning_agent

client = TestClient(app)


def test_v8_01_catalog_and_forwarded_callback_origin():
    res = client.get(
        "/api/v1/auth/providers/catalog",
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "y.tail03c7eb.ts.net"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["origin"] == "https://y.tail03c7eb.ts.net"
    by_id = {p["provider"]: p for p in data["providers"]}
    assert {"openrouter", "gemini", "openai", "groq", "deepseek", "custom"} <= set(by_id)
    assert by_id["openrouter"]["supports_oauth"] is True
    assert by_id["openrouter"]["redirect_uri"] == "https://y.tail03c7eb.ts.net/api/v1/auth/providers/openrouter/callback"
    assert by_id["gemini"]["redirect_uri"] == "https://y.tail03c7eb.ts.net/api/v1/auth/providers/gemini/callback"


def test_v8_02_multi_account_strict_active_isolation_and_secret_masking():
    r1 = client.post(
        "/api/v1/auth/providers/accounts",
        json={
            "provider": "openai",
            "api_key": "sk-secret-openai-1234567890",
            "label": "OpenAI Trường",
            "vision_model": "gpt-4o-mini",
            "reasoning_model": "gpt-4o",
            "activate": True,
        },
    )
    assert r1.status_code == 200
    a1 = r1.json()["account"]
    assert "sk-secret-openai-1234567890" not in r1.text
    assert a1["secret_masked"].endswith("7890")
    assert vision_agent.active_provider_name == "openai"
    assert vision_agent._active_provider() == "openai"
    assert vision_agent.api_key == ""  # Không giữ key provider khác

    # Thêm tài khoản Gemini và kích hoạt -> chỉ chạy đúng Gemini, xóa sạch OpenAI khỏi runtime (NO fallback)
    r2 = client.post(
        "/api/v1/auth/providers/accounts",
        json={
            "provider": "gemini",
            "api_key": "AIzaSecretGemini9876543210",
            "label": "Gemini Cá Nhân",
            "activate": True,
        },
    )
    assert r2.status_code == 200
    a2 = r2.json()["account"]
    assert "AIzaSecretGemini9876543210" not in r2.text
    assert vision_agent.active_provider_name == "gemini"
    assert vision_agent.active_account_id == a2["account_id"]
    assert vision_agent.openai_api_key == ""
    assert reasoning_agent.openai_api_key == ""
    assert vision_agent._active_provider() == "gemini"

    # Chuyển lại về OpenAI bằng /activate -> chỉ OpenAI active, Gemini bị gỡ khỏi runtime
    act = client.post(f"/api/v1/auth/providers/accounts/{a1['account_id']}/activate")
    assert act.status_code == 200
    assert vision_agent.active_account_id == a1["account_id"]
    assert vision_agent.active_provider_name == "openai"
    assert vision_agent.api_key == ""
    assert vision_agent.gemini_oauth_credentials is None

    # Kiểm tra /config/key-status và Auth Logs không rò rỉ raw key
    ks = client.get("/api/v1/config/key-status").json()
    assert ks["configured"] is True
    assert ks["active_account_id"] == a1["account_id"]
    assert ks["account_label"] == "OpenAI Trường"

    logs_res = client.get("/api/v1/auth/providers/logs?limit=20")
    assert logs_res.status_code == 200
    assert "sk-secret-openai-1234567890" not in logs_res.text
    assert "AIzaSecretGemini9876543210" not in logs_res.text
    events = [lg["event_type"] for lg in logs_res.json()["logs"]]
    assert "activated" in events and "api_key_added" in events

    # Xóa tài khoản sạch sẽ
    assert client.delete(f"/api/v1/auth/providers/accounts/{a1['account_id']}").status_code == 200
    assert client.delete(f"/api/v1/auth/providers/accounts/{a2['account_id']}").status_code == 200


def test_v8_03_openrouter_oauth_pkce_flow_and_logs(monkeypatch):
    start = client.post(
        "/api/v1/auth/providers/oauth/start",
        json={"provider": "openrouter", "label": "OpenRouter Cá Nhân"},
        headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "y.tail03c7eb.ts.net"},
    )
    assert start.status_code == 200
    sdata = start.json()
    assert sdata["auth_url"].startswith("https://openrouter.ai/auth?")
    state = sdata["state"]

    captured: dict = {}

    def fake_post(url, json=None, timeout=25.0, **kwargs):
        captured["url"] = url
        captured["json"] = json
        return SimpleNamespace(
            status_code=200,
            text='{"key": "sk-or-v1-oauth-token-xyz99"}',
            json=lambda: {"key": "sk-or-v1-oauth-token-xyz99", "user_id": "teacher-01"},
        )

    monkeypatch.setattr(provider_auth.httpx, "post", fake_post)

    cb = client.get(f"/api/v1/auth/providers/openrouter/callback?code=or_code_123&state={state}")
    assert cb.status_code == 200
    assert "edumark-provider-auth" in cb.text
    assert "sk-or-v1-oauth-token-xyz99" not in cb.text
    assert captured["url"] == "https://openrouter.ai/api/v1/auth/keys"
    assert captured["json"]["code"] == "or_code_123"
    assert captured["json"]["code_challenge_method"] == "S256"

    # Kiểm tra runtime đã chuyển sang OpenRouter (openrouter/openai protocol)
    assert vision_agent.active_provider_name == "openrouter"
    assert vision_agent.openai_base_url == "https://openrouter.ai/api/v1"
    assert vision_agent._active_provider() == "openai"

    logs = client.get("/api/v1/auth/providers/logs?provider=openrouter").json()["logs"]
    assert any(lg["event_type"] == "oauth_callback_ok" and lg["status"] == "success" for lg in logs)
    assert "sk-or-v1-oauth-token-xyz99" not in str(logs)

    # Cleanup
    acct_id = vision_agent.active_account_id
    client.delete(f"/api/v1/auth/providers/accounts/{acct_id}")


def test_v8_04_google_gemini_oauth2_callback_and_auto_refresh(monkeypatch):
    start = client.post(
        "/api/v1/auth/providers/oauth/start",
        json={
            "provider": "gemini",
            "client_id": "test-google-cid.apps.googleusercontent.com",
            "client_secret": "test-google-csec",
        },
    )
    assert start.status_code == 200
    sdata = start.json()
    q = parse_qs(urlparse(sdata["auth_url"]).query)
    assert q["access_type"] == ["offline"]
    assert q["code_challenge_method"] == ["S256"]

    def fake_post(url, data=None, timeout=25.0, **kwargs):
        if data and data.get("grant_type") == "authorization_code":
            return SimpleNamespace(
                status_code=200,
                text="ok",
                json=lambda: {
                    "access_token": "ya29.initial-access-1111",
                    "refresh_token": "1//refresh-token-2222",
                    "expires_in": 3600,
                    "scope": "openid email https://www.googleapis.com/auth/generative-language.retriever",
                },
            )
        if data and data.get("grant_type") == "refresh_token":
            return SimpleNamespace(
                status_code=200,
                text="ok",
                json=lambda: {
                    "access_token": "ya29.refreshed-access-9999",
                    "expires_in": 3600,
                },
            )
        raise AssertionError(f"Unexpected post {url} {data}")

    def fake_get(url, headers=None, timeout=10.0, **kwargs):
        return SimpleNamespace(status_code=200, json=lambda: {"email": "giaovien@truong.edu.vn"})

    monkeypatch.setattr(provider_auth.httpx, "post", fake_post)
    monkeypatch.setattr(provider_auth.httpx, "get", fake_get)

    cb = client.get(f"/api/v1/auth/providers/gemini/callback?code=g_code_456&state={sdata['state']}")
    assert cb.status_code == 200
    assert "giaovien@truong.edu.vn" in cb.text
    assert vision_agent.active_provider_name == "gemini"
    assert vision_agent._active_provider() == "gemini"

    # Mô phỏng token sắp hết hạn -> build_gemini_client tự refresh_token và ghi Auth Log
    acct_id = vision_agent.active_account_id
    expired_iso = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    creds = dict(vision_agent.gemini_oauth_credentials)
    creds["expires_at"] = expired_iso

    gemini_client = provider_auth.build_gemini_client(oauth_credentials=creds, account_id=acct_id)
    assert gemini_client is not None
    assert vision_agent.gemini_oauth_credentials["access_token"] == "ya29.refreshed-access-9999"

    logs = client.get("/api/v1/auth/providers/logs?provider=gemini").json()["logs"]
    assert any(lg["event_type"] == "token_refreshed" and lg["status"] == "success" for lg in logs)
    assert "ya29.refreshed-access-9999" not in str(logs)
    assert "1//refresh-token-2222" not in str(logs)

    client.delete(f"/api/v1/auth/providers/accounts/{acct_id}")


def test_v8_05_admin_token_guard_and_ui_elements(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", "super-secret-admin")
    res = client.post(
        "/api/v1/auth/providers/accounts",
        json={"provider": "openai", "api_key": "sk-blocked"},
    )
    assert res.status_code == 403

    idx_html = client.get("/").text
    for marker in ("auth-tab-accounts", "auth-tab-connect", "auth-tab-logs", "startProviderOAuth", "loadAuthProviderLogs"):
        assert marker in idx_html
    mob_html = client.get("/app").text
    assert "provider-pill" in mob_html
