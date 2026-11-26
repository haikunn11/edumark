from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_mobile_access_endpoint_returns_lan_urls():
    res = client.get("/api/v1/system/mobile-access")
    assert res.status_code == 200
    data = res.json()
    assert "lan_ip" in data
    assert data["http_url"].endswith(":8000/app")
    assert data["https_url"].startswith("https://")
    assert data["https_url"].endswith(":8443/app")
    assert isinstance(data["cert_ready"], bool)


def test_mobile_pwa_contains_v05_continuous_class_scan():
    res = client.get("/app")
    assert res.status_code == 200
    html = res.text
    assert "v0.6" in html
    assert "grade-folder-smart-upload" in html
    assert "mobile-class-bar" in html
    assert "chk-continuous" in html
    assert "mob-gb-select" in html


def test_desktop_dashboard_contains_mobile_qr_modal():
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    assert "v0.6" in html
    assert "openMobileQrModal" in html
    assert "mobile-qr-modal" in html
    assert "openGradebookModal" in html
    assert "gradebook-modal" in html
