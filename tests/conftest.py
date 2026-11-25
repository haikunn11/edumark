import pytest
from backend.app.services.agent1_ocr import vision_agent
from backend.app.services.agent2_grader import reasoning_agent


@pytest.fixture(autouse=True)
def isolate_offline_ai_agents(monkeypatch):
    """Đảm bảo pytest luôn chạy offline/deterministic, không gọi API trả phí ra ngoài mạng."""
    monkeypatch.setattr(vision_agent, "openai_api_key", "")
    monkeypatch.setattr(vision_agent, "api_key", "")
    monkeypatch.setattr(reasoning_agent, "openai_api_key", "")
    monkeypatch.setattr(reasoning_agent, "api_key", "")
