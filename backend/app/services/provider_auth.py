from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

import httpx

from backend.app.core.cache import cache
from backend.app.core.config import settings
from backend.app.core.database import db

_RUNTIME_SYNC_LOCK = threading.RLock()
_OAUTH_REFRESH_LOCKS_GUARD = threading.Lock()
_OAUTH_REFRESH_LOCKS: dict[str, threading.Lock] = {}


def _get_refresh_lock(account_id: str) -> threading.Lock:
    key = account_id or "__runtime__"
    with _OAUTH_REFRESH_LOCKS_GUARD:
        lock = _OAUTH_REFRESH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _OAUTH_REFRESH_LOCKS[key] = lock
        return lock


def _validate_provider_base_url(base_url: str, provider: str = "custom") -> str:
    """Chống SSRF cho base_url (chặn metadata cloud, giữ Ollama localhost khi ENVIRONMENT=local)."""
    raw = (base_url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").strip().lower()
    if scheme not in ("http", "https") or not host:
        raise ValueError("Base URL phải bắt đầu bằng http:// hoặc https:// và có tên miền hợp lệ.")

    blocked_hosts = {"metadata.google.internal", "169.254.169.254", "metadata"}
    if host in blocked_hosts or host.endswith(".internal"):
        raise ValueError(f"Base URL không được phép trỏ vào host nội bộ/metadata: {host}")

    try:
        ip_obj = ipaddress.ip_address(host)
        if ip_obj.is_link_local or str(ip_obj) == "169.254.169.254":
            raise ValueError(f"Base URL không được phép dùng dải IP link-local/metadata: {host}")
        if settings.ENVIRONMENT != "local" and (ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved):
            raise ValueError("Môi trường Cloud không cho phép base_url trỏ vào IP nội bộ/localhost.")
    except ValueError as ip_err:
        if "Base URL" in str(ip_err) or "Môi trường Cloud" in str(ip_err):
            raise

    if settings.ENVIRONMENT != "local":
        if scheme != "https":
            raise ValueError("Môi trường Cloud yêu cầu Base URL dùng giao thức https://.")
        if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local"):
            raise ValueError("Môi trường Cloud không cho phép Base URL trỏ về localhost.")

    return raw.rstrip("/")


PROVIDER_PRESETS: dict[str, dict] = {
    "openrouter": {
        "provider": "openrouter",
        "display_name": "OpenRouter",
        "protocol": "openai_compatible",
        "supports_oauth": True,
        "oauth_mode": "pkce_1click",
        "requires_oauth_client": False,
        "default_base_url": "https://openrouter.ai/api/v1",
        "default_vision_model": "google/gemini-2.5-flash",
        "default_reasoning_model": "google/gemini-2.5-flash",
        "extra_headers": {
            "HTTP-Referer": "https://edumark.local",
            "X-Title": "EduMark AI",
        },
        "description": "Đăng nhập OAuth 1-click không cần tạo app Cloud, dùng chung Gemini / GPT-4o / Qwen / DeepSeek.",
    },
    "gemini": {
        "provider": "gemini",
        "display_name": "Google Gemini",
        "protocol": "gemini",
        "supports_oauth": True,
        "oauth_mode": "google_oauth2",
        "requires_oauth_client": True,
        "default_base_url": "",
        "default_vision_model": "gemini-3.1-flash-lite",
        "default_reasoning_model": "gemini-3-flash-preview",
        "extra_headers": {},
        "description": "Hỗ trợ Google OAuth 2.0 (tự làm mới refresh_token) hoặc dán Gemini API Key (AIza...).",
    },
    "openai": {
        "provider": "openai",
        "display_name": "OpenAI",
        "protocol": "openai_compatible",
        "supports_oauth": False,
        "oauth_mode": "",
        "requires_oauth_client": False,
        "default_base_url": "",
        "default_vision_model": "gpt-4o-mini",
        "default_reasoning_model": "gpt-4o-mini",
        "extra_headers": {},
        "description": "OpenAI Platform API (gpt-4o-mini, gpt-4o) xác thực bằng API Key (sk-...).",
    },
    "groq": {
        "provider": "groq",
        "display_name": "Groq Cloud",
        "protocol": "openai_compatible",
        "supports_oauth": False,
        "oauth_mode": "",
        "requires_oauth_client": False,
        "default_base_url": "https://api.groq.com/openai/v1",
        "default_vision_model": "meta-llama/llama-4-scout-17b-16e-instruct",
        "default_reasoning_model": "llama-3.3-70b-versatile",
        "extra_headers": {},
        "description": "Suy luận tốc độ cao qua endpoint tương thích OpenAI của Groq.",
    },
    "deepseek": {
        "provider": "deepseek",
        "display_name": "DeepSeek",
        "protocol": "openai_compatible",
        "supports_oauth": False,
        "oauth_mode": "",
        "requires_oauth_client": False,
        "default_base_url": "https://api.deepseek.com/v1",
        "default_vision_model": "deepseek-chat",
        "default_reasoning_model": "deepseek-chat",
        "extra_headers": {},
        "description": "API DeepSeek (deepseek-chat / deepseek-reasoner) cho chấm luận lý.",
    },
    "custom": {
        "provider": "custom",
        "display_name": "Custom / Ollama / vLLM",
        "protocol": "openai_compatible",
        "supports_oauth": False,
        "oauth_mode": "",
        "requires_oauth_client": False,
        "default_base_url": "http://localhost:11434/v1",
        "default_vision_model": "qwen2.5vl:7b",
        "default_reasoning_model": "qwen2.5:14b",
        "extra_headers": {},
        "description": "Endpoint tương thích OpenAI bất kỳ (Ollama local, LM Studio, vLLM, Proxy nội bộ).",
    },
}


def mask_secret(secret: str) -> str:
    """Ẩn bí mật để không bao giờ lộ raw API key / token ra API response hoặc log."""
    s = (secret or "").strip()
    if not s:
        return "(none)"
    if len(s) <= 8:
        return "*" * len(s)
    return f"{s[:4]}...{s[-4:]}"


def _decode_secret_json(row: dict) -> dict:
    raw = row.get("secret_json") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def sanitize_account(row: dict) -> dict:
    """Chuyển bản ghi DB thành dữ liệu an toàn trả cho UI/API (tuyệt đối không trả raw secret)."""
    provider = str(row.get("provider") or "custom").lower()
    preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["custom"])
    secret = _decode_secret_json(row)
    raw_secret = str(secret.get("api_key") or secret.get("access_token") or "")
    return {
        "account_id": row.get("account_id", ""),
        "provider": provider,
        "provider_display_name": preset["display_name"],
        "protocol": preset["protocol"],
        "auth_type": row.get("auth_type", "api_key"),
        "label": row.get("label", preset["display_name"]),
        "account_email": row.get("account_email", ""),
        "base_url": row.get("base_url") or preset["default_base_url"],
        "vision_model": row.get("vision_model") or preset["default_vision_model"],
        "reasoning_model": row.get("reasoning_model") or preset["default_reasoning_model"],
        "secret_masked": mask_secret(raw_secret),
        "has_refresh_token": bool(secret.get("refresh_token")),
        "expires_at": secret.get("expires_at", ""),
        "is_active": bool(row.get("is_active")),
        "status": row.get("status", "connected"),
        "last_tested_at": row.get("last_tested_at", ""),
        "last_error": row.get("last_error", ""),
        "created_at": row.get("created_at", ""),
        "updated_at": row.get("updated_at", ""),
    }


def get_provider_catalog() -> list[dict]:
    items = []
    for key, preset in PROVIDER_PRESETS.items():
        item = dict(preset)
        if key == "gemini":
            item["oauth_configured"] = bool(
                settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET
            )
        elif key == "openrouter":
            item["oauth_configured"] = True
        else:
            item["oauth_configured"] = False
        items.append(item)
    return items


def build_openai_client(
    api_key: str,
    base_url: str = "",
    extra_headers: dict | None = None,
    timeout: float = 45.0,
):
    from openai import OpenAI

    kwargs: dict = {
        "api_key": (api_key or "").strip() or "local-no-key",
        "timeout": timeout,
    }
    if (base_url or "").strip():
        kwargs["base_url"] = base_url.strip()
    if extra_headers:
        kwargs["default_headers"] = dict(extra_headers)
    return OpenAI(**kwargs)


def _is_token_expiring(expires_at_iso: str, leeway_seconds: int = 120) -> bool:
    if not expires_at_iso:
        return False
    try:
        dt = datetime.fromisoformat(expires_at_iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) + timedelta(seconds=leeway_seconds) >= dt
    except Exception:
        return False


def refresh_google_oauth_token(account_id: str, oauth_creds: dict) -> dict:
    """Tự động làm mới Google OAuth2 access_token từ refresh_token (thread-safe per account_id)."""
    with _get_refresh_lock(account_id):
        # Double-check sau khi giữ lock xem thread khác vừa refresh sang token mới chưa
        if account_id:
            latest = db.get_provider_account(account_id)
            if latest:
                latest_creds = _decode_secret_json(latest)
                stale_tok = str(oauth_creds.get("access_token") or "")
                fresh_tok = str(latest_creds.get("access_token") or "")
                if (
                    fresh_tok
                    and fresh_tok != stale_tok
                    and not _is_token_expiring(str(latest_creds.get("expires_at") or ""))
                ):
                    return latest_creds

        refresh_token = str(oauth_creds.get("refresh_token") or "").strip()
        client_id = str(oauth_creds.get("client_id") or settings.GOOGLE_OAUTH_CLIENT_ID or "").strip()
        client_secret = str(oauth_creds.get("client_secret") or settings.GOOGLE_OAUTH_CLIENT_SECRET or "").strip()
        if not (refresh_token and client_id and client_secret):
            raise ValueError("Thiếu refresh_token hoặc Google OAuth Client ID/Secret để làm mới phiên.")

        permanent_revoked = False
        try:
            resp = httpx.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=20.0,
            )
            if resp.status_code >= 400:
                body_lower = (resp.text or "").lower()
                if resp.status_code in (400, 401) and any(k in body_lower for k in ("invalid_grant", "invalid_client", "unauthorized")):
                    permanent_revoked = True
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            payload = resp.json()
        except Exception as exc:
            if account_id:
                acct = db.get_provider_account(account_id)
                if acct:
                    acct["status"] = "expired" if permanent_revoked else "error"
                    acct["last_error"] = f"Làm mới token thất bại: {exc}"
                    db.save_provider_account(acct)
            db.log_provider_auth_event(
                provider="gemini",
                event_type="token_refresh_fail",
                status="failed",
                detail=f"Không thể làm mới Google OAuth token cho tài khoản {account_id or 'runtime'}: {exc}",
                account_id=account_id,
            )
            raise

        new_access = str(payload.get("access_token") or oauth_creds.get("access_token") or "")
        expires_in = int(payload.get("expires_in") or 3600)
        new_expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()

        updated_creds = dict(oauth_creds)
        updated_creds["access_token"] = new_access
        updated_creds["expires_at"] = new_expires_at
        if payload.get("refresh_token"):
            updated_creds["refresh_token"] = payload["refresh_token"]
        if payload.get("scope"):
            updated_creds["scope"] = payload["scope"]

        if account_id:
            acct = db.get_provider_account(account_id)
            if acct:
                acct["secret_json"] = json.dumps(updated_creds, ensure_ascii=False)
                acct["status"] = "connected"
                acct["last_error"] = ""
                db.save_provider_account(acct)

        from backend.app.services.agent1_ocr import vision_agent
        from backend.app.services.agent2_grader import reasoning_agent

        with _RUNTIME_SYNC_LOCK:
            if vision_agent.active_account_id == account_id:
                vision_agent.gemini_oauth_credentials = dict(updated_creds)
            if reasoning_agent.active_account_id == account_id:
                reasoning_agent.gemini_oauth_credentials = dict(updated_creds)

        db.log_provider_auth_event(
            provider="gemini",
            event_type="token_refreshed",
            status="success",
            detail=f"Làm mới Google OAuth access_token thành công ({mask_secret(new_access)})",
            account_id=account_id,
        )
        return updated_creds


def build_gemini_client(
    api_key: str = "",
    oauth_credentials: dict | None = None,
    account_id: str = "",
):
    from google import genai

    if oauth_credentials and oauth_credentials.get("access_token"):
        creds_dict = dict(oauth_credentials)
        if _is_token_expiring(str(creds_dict.get("expires_at") or "")) and creds_dict.get("refresh_token"):
            creds_dict = refresh_google_oauth_token(account_id, creds_dict)

        from google.oauth2.credentials import Credentials

        creds = Credentials(
            token=creds_dict.get("access_token"),
            refresh_token=creds_dict.get("refresh_token") or None,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=creds_dict.get("client_id") or settings.GOOGLE_OAUTH_CLIENT_ID or None,
            client_secret=creds_dict.get("client_secret") or settings.GOOGLE_OAUTH_CLIENT_SECRET or None,
            scopes=str(creds_dict.get("scope") or "").split() or None,
        )
        return genai.Client(credentials=creds)

    return genai.Client(api_key=api_key)


def sync_active_provider_to_runtime() -> dict | None:
    """
    Đồng bộ ĐÚNG 1 tài khoản AI đang Active vào vision_agent & reasoning_agent.
    TUYỆT ĐỐI không tự fallback sang provider/account khác khi đã chọn Active Account.
    """
    from backend.app.services.agent1_ocr import vision_agent
    from backend.app.services.agent2_grader import reasoning_agent

    with _RUNTIME_SYNC_LOCK:
        active = db.get_active_provider_account()
        if not active:
            for ag in (vision_agent, reasoning_agent):
                ag.active_provider_name = ""
                ag.active_account_id = ""
                ag.gemini_oauth_credentials = None
                ag.openai_base_url = ""
                ag.openai_extra_headers = {}
                ag.api_key = settings.GEMINI_API_KEY
                ag.openai_api_key = settings.OPENAI_API_KEY
                ag.openai_model = settings.DEFAULT_OPENAI_MODEL
            vision_agent.model_name = settings.DEFAULT_VISION_MODEL
            reasoning_agent.model_name = settings.DEFAULT_REASONING_MODEL
            return None

        provider = str(active.get("provider") or "custom").lower()
        preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["custom"])
        protocol = preset["protocol"]
        secret = _decode_secret_json(active)
        acct_id = active["account_id"]
        v_model = active.get("vision_model") or preset["default_vision_model"]
        r_model = active.get("reasoning_model") or preset["default_reasoning_model"]

        if protocol == "gemini":
            for ag in (vision_agent, reasoning_agent):
                ag.active_provider_name = "gemini"
                ag.active_account_id = acct_id
                ag.openai_api_key = ""
                ag.openai_base_url = ""
                ag.openai_extra_headers = {}
                if active.get("auth_type") == "oauth":
                    ag.api_key = ""
                    ag.gemini_oauth_credentials = dict(secret)
                else:
                    ag.gemini_oauth_credentials = None
                    ag.api_key = str(secret.get("api_key") or "")
            vision_agent.model_name = v_model
            reasoning_agent.model_name = r_model
        else:
            base_url = active.get("base_url") or preset["default_base_url"]
            extra_headers = dict(preset.get("extra_headers") or {})
            api_key = str(secret.get("api_key") or secret.get("access_token") or "").strip() or "local-no-key"
            for ag in (vision_agent, reasoning_agent):
                ag.active_provider_name = provider
                ag.active_account_id = acct_id
                ag.api_key = ""
                ag.gemini_oauth_credentials = None
                ag.openai_api_key = api_key
                ag.openai_base_url = base_url
                ag.openai_extra_headers = extra_headers
            vision_agent.openai_model = v_model
            reasoning_agent.openai_model = r_model

        return sanitize_account(active)


def _run_live_probe(account: dict) -> tuple[bool, str]:
    """Kiểm tra kết nối thực tới provider bằng đúng thông tin của account."""
    provider = str(account.get("provider") or "custom").lower()
    preset = PROVIDER_PRESETS.get(provider, PROVIDER_PRESETS["custom"])
    secret = _decode_secret_json(account)

    try:
        if preset["protocol"] == "gemini":
            if account.get("auth_type") == "oauth":
                client = build_gemini_client(oauth_credentials=secret, account_id=account.get("account_id", ""))
            else:
                client = build_gemini_client(api_key=str(secret.get("api_key") or ""))
            model = account.get("vision_model") or preset["default_vision_model"]
            client.models.generate_content(model=model, contents="ping")
            return True, f"Kết nối thành công tới {preset['display_name']} ({model})"

        api_key = str(secret.get("api_key") or secret.get("access_token") or "")
        base_url = _validate_provider_base_url(account.get("base_url") or preset["default_base_url"], provider)
        client = build_openai_client(
            api_key=api_key,
            base_url=base_url,
            extra_headers=preset.get("extra_headers"),
            timeout=20.0,
        )
        model = account.get("reasoning_model") or account.get("vision_model") or preset["default_reasoning_model"]
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
        )
        return True, f"Kết nối thành công tới {preset['display_name']} ({model})"
    except Exception as exc:
        return False, str(exc)


def create_or_update_api_key_account(
    provider: str,
    api_key: str = "",
    label: str = "",
    base_url: str = "",
    vision_model: str = "",
    reasoning_model: str = "",
    activate: bool = True,
    verify_live: bool = False,
    account_id: str = "",
    client_ip: str = "",
) -> dict:
    prov = (provider or "custom").strip().lower()
    if prov not in PROVIDER_PRESETS:
        raise ValueError(f"Provider không được hỗ trợ: {prov}")
    preset = PROVIDER_PRESETS[prov]
    clean_key = (api_key or "").strip()
    if prov != "custom" and not clean_key:
        raise ValueError(f"API Key cho {preset['display_name']} không được để trống.")

    acct_id = account_id or f"acc_{prov}_{uuid.uuid4().hex[:8]}"
    existing = db.get_provider_account(acct_id) if account_id else None
    final_base_url = _validate_provider_base_url((base_url or "").strip() or preset["default_base_url"], prov)
    final_v_model = (vision_model or "").strip() or preset["default_vision_model"]
    final_r_model = (reasoning_model or "").strip() or preset["default_reasoning_model"]
    final_label = (label or "").strip() or f"{preset['display_name']} ({mask_secret(clean_key)})"

    now = datetime.now(timezone.utc).isoformat()
    row = {
        "account_id": acct_id,
        "provider": prov,
        "auth_type": "api_key",
        "label": final_label,
        "account_email": (existing or {}).get("account_email", ""),
        "base_url": final_base_url,
        "vision_model": final_v_model,
        "reasoning_model": final_r_model,
        "secret_json": json.dumps({"api_key": clean_key}, ensure_ascii=False),
        "is_active": bool(activate),
        "status": "connected",
        "last_tested_at": now if verify_live else (existing or {}).get("last_tested_at", ""),
        "last_error": "",
        "created_at": (existing or {}).get("created_at") or now,
    }

    if verify_live and not clean_key.startswith("mock_"):
        ok, msg = _run_live_probe(row)
        if not ok:
            db.log_provider_auth_event(
                provider=prov,
                event_type="api_key_verify_fail",
                status="failed",
                detail=f"Xác thực {final_label} ({mask_secret(clean_key)}) thất bại: {msg[:180]}",
                account_id=acct_id,
                client_ip=client_ip,
            )
            raise ValueError(f"Không thể xác thực {preset['display_name']}: {msg}")

    saved = db.save_provider_account(row)
    cache.clear()
    sync_active_provider_to_runtime()

    db.log_provider_auth_event(
        provider=prov,
        event_type="api_key_added" if not existing else "api_key_updated",
        status="success",
        detail=f"Lưu tài khoản '{final_label}' ({mask_secret(clean_key)}), active={bool(activate)}",
        account_id=acct_id,
        client_ip=client_ip,
    )
    return sanitize_account(saved)


def activate_account(account_id: str, client_ip: str = "") -> dict | None:
    updated = db.set_active_provider_account(account_id)
    if not updated:
        return None
    cache.clear()
    sync_active_provider_to_runtime()
    db.log_provider_auth_event(
        provider=updated["provider"],
        event_type="activated",
        status="success",
        detail=f"Chuyển tài khoản AI mặc định sang '{updated['label']}' ({updated['vision_model']})",
        account_id=account_id,
        client_ip=client_ip,
    )
    return sanitize_account(updated)


def delete_account(account_id: str, client_ip: str = "") -> bool:
    existing = db.get_provider_account(account_id)
    if not existing:
        return False
    deleted = db.delete_provider_account(account_id)
    if deleted:
        cache.clear()
        sync_active_provider_to_runtime()
        db.log_provider_auth_event(
            provider=existing["provider"],
            event_type="deleted",
            status="success",
            detail=f"Đã xóa tài khoản '{existing['label']}'",
            account_id=account_id,
            client_ip=client_ip,
        )
    return deleted


def test_provider_account(account_id: str, client_ip: str = "") -> dict:
    acct = db.get_provider_account(account_id)
    if not acct:
        raise ValueError(f"Không tìm thấy tài khoản {account_id}")
    ok, msg = _run_live_probe(acct)
    now = datetime.now(timezone.utc).isoformat()
    acct["last_tested_at"] = now
    acct["status"] = "connected" if ok else "error"
    acct["last_error"] = "" if ok else msg[:300]
    saved = db.save_provider_account(acct)
    if acct.get("is_active"):
        sync_active_provider_to_runtime()
    db.log_provider_auth_event(
        provider=acct["provider"],
        event_type="test_ok" if ok else "test_fail",
        status="success" if ok else "failed",
        detail=msg[:240],
        account_id=account_id,
        client_ip=client_ip,
    )
    return {"ok": ok, "message": msg, "account": sanitize_account(saved)}


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def start_oauth_flow(
    provider: str,
    base_origin: str,
    label: str = "",
    vision_model: str = "",
    reasoning_model: str = "",
    client_id: str = "",
    client_secret: str = "",
    client_ip: str = "",
) -> dict:
    prov = (provider or "").strip().lower()
    if prov not in ("openrouter", "gemini"):
        raise ValueError(f"Provider '{prov}' không hỗ trợ OAuth trình duyệt.")

    preset = PROVIDER_PRESETS[prov]
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    origin = (settings.PUBLIC_BASE_URL or base_origin or "").strip().rstrip("/")
    callback_path = f"{settings.API_V1_STR}/auth/providers/{prov}/callback"
    redirect_uri = f"{origin}{callback_path}"
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=15)).isoformat()

    extra: dict = {}
    if prov == "openrouter":
        auth_url = "https://openrouter.ai/auth?" + urlencode(
            {
                "callback_url": f"{redirect_uri}?state={state}",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    else:
        cid = (client_id or settings.GOOGLE_OAUTH_CLIENT_ID or "").strip()
        csec = (client_secret or settings.GOOGLE_OAUTH_CLIENT_SECRET or "").strip()
        if not cid or not csec:
            raise ValueError(
                "Google Gemini OAuth cần GOOGLE_OAUTH_CLIENT_ID và GOOGLE_OAUTH_CLIENT_SECRET "
                f"(điền trong form hoặc file .env). Redirect URI cần khai báo trên Google Cloud Console: {redirect_uri}"
            )
        extra = {"client_id": cid, "client_secret": csec}
        auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": cid,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "openid email https://www.googleapis.com/auth/generative-language.retriever",
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )

    db.save_oauth_state(
        {
            "state": state,
            "provider": prov,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "label": (label or "").strip() or f"{preset['display_name']} (OAuth)",
            "vision_model": (vision_model or "").strip() or preset["default_vision_model"],
            "reasoning_model": (reasoning_model or "").strip() or preset["default_reasoning_model"],
            "extra": extra,
            "created_at": now.isoformat(),
            "expires_at": expires_at,
        }
    )
    db.log_provider_auth_event(
        provider=prov,
        event_type="oauth_start",
        status="pending",
        detail=f"Bắt đầu luồng OAuth PKCE cho {preset['display_name']} (redirect: {redirect_uri})",
        client_ip=client_ip,
    )
    return {
        "provider": prov,
        "auth_url": auth_url,
        "redirect_uri": redirect_uri,
        "state": state,
        "expires_at": expires_at,
    }


def complete_openrouter_oauth(code: str, state: str, client_ip: str = "") -> dict:
    st_row = db.pop_oauth_state(state)
    if not st_row or st_row.get("provider") != "openrouter":
        db.log_provider_auth_event(
            provider="openrouter",
            event_type="oauth_callback_fail",
            status="failed",
            detail="OAuth state không hợp lệ hoặc đã hết hạn.",
            client_ip=client_ip,
        )
        raise ValueError("Phiên đăng nhập OpenRouter không hợp lệ hoặc đã hết hạn. Vui lòng thử lại.")

    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/auth/keys",
            json={
                "code": code,
                "code_verifier": st_row["code_verifier"],
                "code_challenge_method": "S256",
            },
            timeout=25.0,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        api_key = str(data.get("key") or "").strip()
        if not api_key:
            raise RuntimeError("OpenRouter không trả về key.")
    except Exception as exc:
        db.log_provider_auth_event(
            provider="openrouter",
            event_type="oauth_callback_fail",
            status="failed",
            detail=f"Trao đổi mã OpenRouter OAuth thất bại: {exc}",
            client_ip=client_ip,
        )
        raise ValueError(f"Đăng nhập OpenRouter thất bại: {exc}")

    preset = PROVIDER_PRESETS["openrouter"]
    acct_id = f"acc_openrouter_{uuid.uuid4().hex[:8]}"
    raw_label = str(st_row.get("label") or "").strip()
    label = f"OpenRouter ({mask_secret(api_key)})" if (not raw_label or raw_label == "OpenRouter (OAuth)") else raw_label
    now = datetime.now(timezone.utc).isoformat()
    saved = db.save_provider_account(
        {
            "account_id": acct_id,
            "provider": "openrouter",
            "auth_type": "oauth",
            "label": label,
            "account_email": str(data.get("user_id") or ""),
            "base_url": preset["default_base_url"],
            "vision_model": st_row.get("vision_model") or preset["default_vision_model"],
            "reasoning_model": st_row.get("reasoning_model") or preset["default_reasoning_model"],
            "secret_json": json.dumps({"api_key": api_key}, ensure_ascii=False),
            "is_active": True,
            "status": "connected",
            "last_tested_at": now,
            "last_error": "",
            "created_at": now,
        }
    )
    cache.clear()
    sync_active_provider_to_runtime()
    db.log_provider_auth_event(
        provider="openrouter",
        event_type="oauth_callback_ok",
        status="success",
        detail=f"Đăng nhập OpenRouter OAuth thành công ({mask_secret(api_key)}), đã kích hoạt làm mặc định.",
        account_id=acct_id,
        client_ip=client_ip,
    )
    return sanitize_account(saved)


def complete_google_oauth(code: str, state: str, client_ip: str = "") -> dict:
    st_row = db.pop_oauth_state(state)
    if not st_row or st_row.get("provider") != "gemini":
        db.log_provider_auth_event(
            provider="gemini",
            event_type="oauth_callback_fail",
            status="failed",
            detail="Google OAuth state không hợp lệ hoặc đã hết hạn.",
            client_ip=client_ip,
        )
        raise ValueError("Phiên đăng nhập Google Gemini không hợp lệ hoặc đã hết hạn. Vui lòng thử lại.")

    try:
        extra = json.loads(st_row.get("extra_json") or "{}")
    except Exception:
        extra = {}
    client_id = str(extra.get("client_id") or settings.GOOGLE_OAUTH_CLIENT_ID or "").strip()
    client_secret = str(extra.get("client_secret") or settings.GOOGLE_OAUTH_CLIENT_SECRET or "").strip()

    try:
        resp = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": st_row["redirect_uri"],
                "grant_type": "authorization_code",
                "code_verifier": st_row["code_verifier"],
            },
            timeout=25.0,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:220]}")
        token_data = resp.json()
        access_token = str(token_data.get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError("Google OAuth không trả về access_token.")
    except Exception as exc:
        db.log_provider_auth_event(
            provider="gemini",
            event_type="oauth_callback_fail",
            status="failed",
            detail=f"Đổi Google OAuth code thất bại: {exc}",
            client_ip=client_ip,
        )
        raise ValueError(f"Đăng nhập Google OAuth thất bại: {exc}")

    email = ""
    try:
        ui_resp = httpx.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10.0,
        )
        if ui_resp.status_code < 400:
            email = str(ui_resp.json().get("email") or "").strip()
    except Exception:
        pass

    expires_in = int(token_data.get("expires_in") or 3600)
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
    secret_payload = {
        "access_token": access_token,
        "refresh_token": str(token_data.get("refresh_token") or ""),
        "expires_at": expires_at,
        "scope": str(token_data.get("scope") or ""),
        "client_id": client_id,
        "client_secret": client_secret,
    }

    preset = PROVIDER_PRESETS["gemini"]
    acct_id = f"acc_gemini_{uuid.uuid4().hex[:8]}"
    label = st_row.get("label") or (f"Gemini ({email})" if email else "Google Gemini OAuth")
    if email and "(OAuth)" in label:
        label = f"Gemini ({email})"
    now = datetime.now(timezone.utc).isoformat()
    saved = db.save_provider_account(
        {
            "account_id": acct_id,
            "provider": "gemini",
            "auth_type": "oauth",
            "label": label,
            "account_email": email,
            "base_url": "",
            "vision_model": st_row.get("vision_model") or preset["default_vision_model"],
            "reasoning_model": st_row.get("reasoning_model") or preset["default_reasoning_model"],
            "secret_json": json.dumps(secret_payload, ensure_ascii=False),
            "is_active": True,
            "status": "connected",
            "last_tested_at": now,
            "last_error": "",
            "created_at": now,
        }
    )
    cache.clear()
    sync_active_provider_to_runtime()
    db.log_provider_auth_event(
        provider="gemini",
        event_type="oauth_callback_ok",
        status="success",
        detail=f"Đăng nhập Google OAuth thành công ({email or mask_secret(access_token)}), đã kích hoạt làm mặc định.",
        account_id=acct_id,
        client_ip=client_ip,
    )
    return sanitize_account(saved)
