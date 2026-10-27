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
    for path in ["/", "/hilfe", "/vorschlaege", "/experimente", "/suchbegriffe", "/keywords", "/anzeigen", "/leads", "/alerts", "/reports", "/chat", "/admin", "/api/timeseries?days=30"]:
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


def test_page_context_all_routes(client):
    """Seitenkontext für jede Route baubar und als Prompt formatierbar (gegen die Demo-Datenbank)."""
    from cockpit.db import session_scope
    from cockpit.llm import page_context

    with session_scope() as s:
        for path, query in [("/", {"days": "28"}), ("/vorschlaege", {"status": "alle"}), ("/vorschlaege/1", {}), ("/experimente", {}), ("/experimente/1", {}), ("/suchbegriffe", {"waste": "1", "q": "copilot"}), ("/keywords", {"sort": "cost"}), ("/anzeigen", {}), ("/leads", {"days": "90"}), ("/alerts", {}), ("/reports", {}), ("/reports/1", {}), ("/admin", {}), ("/gibtsnicht", {})]:
            ctx = page_context.build(s, path, query)
            assert ctx["title"] and ctx["data_json"].startswith("{"), path
            prompt = page_context.as_prompt(ctx)
            assert "AKTUELLE SEITE" in prompt and len(prompt) < page_context.MAX_CHARS + 2000, path


def test_chat_panel_api(client, monkeypatch):
    """Panel-Endpunkte: Verlauf laden, Frage mit Seitenkontext stellen (Modell gemockt), fremder Thread wird umgebogen."""
    from cockpit.llm import chat as chat_mod, client as llm

    _login(client)
    seen = {}

    def fake_run(system, msgs, tools, executor, model=None, **kw):
        seen["system"], seen["model"] = system, model
        return "Antwort zur Seite.", []

    monkeypatch.setattr(llm, "run_with_tools", fake_run)
    monkeypatch.setattr(llm, "model_name", lambda cheap=False, chat=False: "chat-modell" if chat else "standard")
    r = client.post("/api/chat", json={"question": "Was fällt auf?", "thread": "panel-chris", "page": {"path": "/suchbegriffe", "query": {"waste": "1"}}})
    assert r.status_code == 200
    d = r.json()
    assert d["answer"] == "Antwort zur Seite." and d["model"] == "chat-modell" and d["context"] == "Suchbegriffe"
    assert "AKTUELLE SEITE DES NUTZERS: Suchbegriffe" in seen["system"] and "search_terms" in seen["system"]
    h = client.get("/api/chat/history?thread=panel-chris").json()
    assert h["thread"] == "panel-chris" and h["messages"][-1]["role"] == "assistant" and "Antwort zur Seite" in h["messages"][-1]["html"]
    h2 = client.get("/api/chat/history?thread=panel-martin").json()
    assert h2["thread"] == "panel-chris"
    html = client.get("/suchbegriffe").text
    assert 'id="sea-panel"' in html and "/api/chat/history" in client.get("/static/app.js").text


def test_ms_oauth_routes_and_env_writer(client, tmp_path):
    from cockpit.util import update_env_file

    _login(client)
    assert client.get("/oauth/microsoft/start", follow_redirects=False).status_code == 400  # keine Client-ID in Tests
    r = client.get("/oauth/microsoft/callback?error=access_denied&error_description=abgelehnt")
    assert r.status_code == 200 and "access_denied" in r.text
    r = client.get("/oauth/microsoft/callback?code=abc&state=falsch")
    assert r.status_code == 200 and "state" in r.text
    env = tmp_path / ".env"
    env.write_text("A=1\nMS_ADS_REFRESH_TOKEN=alt\n")
    update_env_file({"MS_ADS_REFRESH_TOKEN": "neu", "MS_ADS_ENABLED": "1"}, env)
    assert env.read_text() == "A=1\nMS_ADS_REFRESH_TOKEN=neu\nMS_ADS_ENABLED=1\n"
