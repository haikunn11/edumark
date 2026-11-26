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
    monkeypatch.setattr(vision_agent, "openai_api_key", "")
    monkeypatch.setattr(vision_agent, "api_key", "")
    monkeypatch.setattr(reasoning_agent, "openai_api_key", "")
    monkeypatch.setattr(reasoning_agent, "api_key", "")
