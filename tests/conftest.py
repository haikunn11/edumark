import pytest
from backend.app.api import routes
from backend.app.core.database import db
from backend.app.services.agent1_ocr import vision_agent
from backend.app.services.agent2_grader import reasoning_agent


@pytest.fixture(scope="session", autouse=True)
def isolate_test_database(tmp_path_factory):
    """Cô lập toàn bộ pytest vào file SQLite tạm, không bao giờ ghi rác vào backend/data/edumark.db thật."""
    orig_path = db.db_path
    test_db = str(tmp_path_factory.mktemp("edumark_test_db") / "test_edumark.db")
    db.db_path = test_db
    db._init_schema()
    routes._seed_default_exams()
    yield test_db
    db.db_path = orig_path


@pytest.fixture(autouse=True)
def isolate_offline_ai_agents(monkeypatch):
    """Đảm bảo pytest luôn chạy offline/deterministic, không gọi API trả phí ra ngoài mạng."""
    for ag in (vision_agent, reasoning_agent):
        monkeypatch.setattr(ag, "openai_api_key", "")
        monkeypatch.setattr(ag, "api_key", "")
        monkeypatch.setattr(ag, "active_provider_name", "")
        monkeypatch.setattr(ag, "active_account_id", "")
        monkeypatch.setattr(ag, "gemini_oauth_credentials", None)
        monkeypatch.setattr(ag, "openai_base_url", "")
        monkeypatch.setattr(ag, "openai_extra_headers", {})
    db.deactivate_all_provider_accounts()
