import re

import pytest
from fastapi.testclient import TestClient

from cockpit.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _login(client):
    r = client.post("/login", data={"username": "chris", "password": "pw"}, follow_redirects=False)
    assert r.status_code == 303


def _csrf(client, path="/vorschlaege/1"):
    html = client.get(path).text
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m, "kein CSRF-Token"
    return m.group(1)


def test_login_required(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_pages_render(client):
    _login(client)
    for path in ["/", "/vorschlaege", "/experimente", "/suchbegriffe", "/keywords", "/anzeigen", "/leads", "/alerts", "/reports", "/chat", "/admin", "/api/timeseries?days=30"]:
        r = client.get(path)
        assert r.status_code == 200, path


def test_suggestion_status_creates_experiment(client):
    _login(client)
    csrf = _csrf(client)
    r = client.post("/vorschlaege/1/status", data={"csrf": csrf, "status": "umgesetzt", "note": "test", "implemented_on": ""}, follow_redirects=False)
    assert r.status_code == 303
    data = client.get("/api/suggestions?status=umgesetzt").json()
    assert any(s["id"] == 1 and s["experiment_id"] for s in data)


def test_csrf_rejected(client):
    _login(client)
    r = client.post("/vorschlaege/1/status", data={"csrf": "falsch", "status": "offen"})
    assert r.status_code == 400


def test_booking_webhook(client):
    r = client.post("/api/webhooks/booking", json={"id": "b1", "start": "2026-10-20T10:00:00", "customer_name": "Test", "customer_email": "t@firma.de"})
    assert r.status_code == 401
    r = client.post("/api/webhooks/booking", json={"id": "b1", "start": "2026-10-20T10:00:00", "customer_name": "Test", "customer_email": "t@firma.de"}, headers={"X-Webhook-Secret": "hook-test"})
    assert r.status_code == 200 and r.json()["ok"]
    r = client.post("/api/webhooks/booking", json={"id": "b1"}, headers={"X-Webhook-Secret": "hook-test"})
    assert r.json().get("duplicate") is True


def test_mcp_tools_list(client):
    r = client.post("/mcp/mcp-test/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 200
    names = [t["name"] for t in r.json()["result"]["tools"]]
    assert "get_overview" in names and "update_suggestion_status" in names


def test_mcp_bearer_wrong(client):
    r = client.post("/mcp/mcp-test/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, headers={"Accept": "application/json, text/event-stream", "Authorization": "Bearer nope"})
    # Pfad-Geheimnis stimmt, also erlaubt – Bearer ist nur die zweite Möglichkeit
    assert r.status_code == 200
