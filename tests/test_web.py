import re

import pytest
from fastapi.testclient import TestClient

from cockpit.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _login(client):
    page = client.get("/login")
    m = re.search(r'name="login_csrf" value="([^"]+)"', page.text)
    r = client.post("/login", data={"username": "chris", "password": "pw", "login_csrf": m.group(1)}, follow_redirects=False)
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
    for path in ["/", "/hilfe", "/organisch", "/negativliste", "/vorschlaege", "/experimente", "/suchbegriffe", "/keywords", "/anzeigen", "/leads", "/alerts", "/reports", "/chat", "/admin", "/api/timeseries?days=30"]:
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
        for path, query in [("/", {"days": "28"}), ("/vorschlaege", {"status": "alle"}), ("/vorschlaege/1", {}), ("/experimente", {}), ("/experimente/1", {}), ("/suchbegriffe", {"waste": "1", "q": "copilot"}), ("/organisch", {"days": "28"}), ("/keywords", {"sort": "cost"}), ("/anzeigen", {}), ("/leads", {"days": "90"}), ("/alerts", {}), ("/reports", {}), ("/reports/1", {}), ("/admin", {}), ("/gibtsnicht", {})]:
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


def test_banter_quota(client, monkeypatch):
    """Chris bekommt den Tonzusatz; der Mutterspruch wird gezählt und am selben Tag nicht mehr freigegeben."""
    from cockpit.llm import client as llm

    _login(client)
    seen = []
    monkeypatch.setattr(llm, "run_with_tools", lambda system, msgs, tools, executor, model=None, **kw: (seen.append(system) or ("Dickerchen, Budget rauf heißt nicht Leads rauf. [[MUTTERWITZ]]", [])))
    d = client.post("/api/chat", json={"question": "Und?", "thread": "panel-chris-b"}).json()
    assert "Dickerchen" in seen[0] and "noch kein Mutterspruch" in seen[0]
    assert "[[MUTTERWITZ]]" not in d["answer"]
    client.post("/api/chat", json={"question": "Nochmal?", "thread": "panel-chris-b"})
    assert "bereits verbraucht" in seen[1]


def test_search_term_triage(client):
    from cockpit.db import session_scope
    from cockpit.analysis import triage

    _login(client)
    r = client.post("/api/suchbegriffe/entscheidung", json={"platform": "google", "search_term": "ccna kurs", "decision": "ausschliessen", "negative_match": "phrase"})
    assert r.status_code == 200 and r.json()["decision"]["decision"] == "ausschliessen"
    r = client.post("/api/suchbegriffe/entscheidung", json={"platform": "google", "search_term": "copilot kurs", "decision": "behalten"})
    assert r.status_code == 200
    assert client.post("/api/suchbegriffe/entscheidung", json={"platform": "google", "search_term": "x", "decision": "quatsch"}).status_code == 400
    html = client.get("/negativliste").text
    assert "ccna kurs" in html and "copilot kurs" not in html
    with session_scope() as s:
        assert triage.negative_list_text(s) == '"ccna kurs"'
        ids = [d["id"] for d in triage.negative_queue(s)]
    csrf = _csrf(client, "/negativliste")
    r = client.post("/negativliste/angewendet", data={"csrf": csrf, "id": [str(i) for i in ids]}, follow_redirects=False)
    assert r.status_code == 303
    with session_scope() as s:
        assert triage.negative_queue(s) == [] and triage.summary(s)["ausschliessen_angewendet"] == 1
    assert client.get("/suchbegriffe?offen=0").status_code == 200



def test_edge_cases(client):
    """Login ohne Token, kaputtes JSON, unsinnige Zeiträume, ungültiges Experimentdatum."""
    r = client.post("/login", data={"username": "chris", "password": "pw"}, follow_redirects=False)
    assert r.status_code == 200 and "abgelaufen" in r.text
    _login(client)
    assert client.post("/api/chat", content="{nicht json", headers={"Content-Type": "application/json"}).status_code == 400
    assert client.post("/api/suchbegriffe/entscheidung", content="[1,2]", headers={"Content-Type": "application/json"}).status_code == 400
    for p in ["/?days=abc", "/?days=-5", "/?days=99999", "/suchbegriffe?days=0&end=kein-datum", "/api/timeseries?days=x"]:
        assert client.get(p).status_code == 200, p
    csrf = _csrf(client)
    assert client.post("/experimente/neu", data={"csrf": csrf, "name": "x", "start_date": "31.02.2026", "platform": "google"}).status_code == 400
    from cockpit.llm import page_context
    from cockpit.db import session_scope

    with session_scope() as s:
        assert page_context.build(s, "/gibtsnicht", {"days": "abc"})["title"] == "/gibtsnicht"


def test_memory_digest_toggle_and_search(client, monkeypatch, tmp_path):
    import datetime as dt
    from cockpit.db import session_scope
    from cockpit.llm import client as llm, memory
    from cockpit.models import ChatMessage, ChatDigest

    monkeypatch.setattr(memory, "HISTORY_DIR", tmp_path)
    with session_scope() as s:
        old = dt.datetime(2026, 10, 20, 10, 0)
        s.add(ChatMessage(thread_id="panel-chris", user="chris", role="user", content="Ich habe heute das Conversion-Ziel auf primär umgestellt.", created_at=old))
        s.add(ChatMessage(thread_id="panel-chris", user="chris", role="assistant", content="Notiert.", created_at=old + dt.timedelta(minutes=1)))
        s.flush()
        n = memory.build_digests(s, until=dt.date(2026, 10, 27), summarize=lambda text: "[Umgesetzt] Conversion-Ziel auf primär umgestellt (20.10.)")
        assert n >= 1
        assert (tmp_path / "chris" / "2026-10-20.md").is_file()
        assert memory.build_digests(s, until=dt.date(2026, 10, 27), summarize=lambda t: "x") == 0  # nicht doppelt
        hits = memory.search(s, "conversion-ziel primär")
        assert hits and hits[0]["who"] == "chris"
        s.query(ChatDigest).filter(ChatDigest.day == dt.date(2026, 10, 20)).update({"day": dt.date.today() - dt.timedelta(days=3)})

    _login(client)
    seen = []
    monkeypatch.setattr(llm, "run_with_tools", lambda system, msgs, tools, executor, model=None, **kw: (seen.append((system, [t["name"] for t in tools])) or ("ok", [])))
    client.post("/api/chat", json={"question": "Wann war das?", "thread": "panel-chris-m"})
    assert "GEDÄCHTNIS (" in seen[-1][0] and "search_chat_history" in seen[-1][1]
    client.post("/api/chat", json={"question": "Frisch bitte", "thread": "panel-chris-m", "memory": False})
    assert "GEDÄCHTNIS AUS" in seen[-1][0] and "search_chat_history" not in seen[-1][1]
    assert "Conversion-Ziel" in client.get("/gedaechtnis").text


def test_suggestion_discard_and_platform_badge(client):
    from cockpit.db import session_scope
    from cockpit.models import Suggestion

    with session_scope() as s:
        sg = Suggestion(source="manual", platform="microsoft", title="Testvorschlag Verwerfen", priority=2)
        s.add(sg)
        s.flush()
        sid = sg.id
    _login(client)
    csrf = _csrf(client)
    assert 'class="pf pf-microsoft"' in client.get("/vorschlaege").text
    r = client.post(f"/vorschlaege/{sid}/verwerfen", data={"csrf": csrf, "reason": "passt nicht zu Inhouse"}, follow_redirects=False)
    assert r.status_code == 303
    assert "passt nicht zu Inhouse" in client.get("/vorschlaege?status=verworfen").text
    client.post(f"/vorschlaege/{sid}/verwerfen", data={"csrf": csrf, "reason": "", "delete": "1"})
    assert f"/vorschlaege/{sid}\"" not in client.get("/vorschlaege?status=alle").text
    html = client.get("/").text
    assert 'class="pf pf-google"' in html and "/static/logo.svg" in html


def test_custom_range_platform_filter_and_campaign_rename(client):
    import datetime as dt

    from cockpit.connectors import names
    from cockpit.db import SessionLocal
    from cockpit.models import Alert, CampaignDaily

    _login(client)
    # frei gewählter Zeitraum: Datumsfelder vorbelegt, Plattformlinks behalten den Zeitraum
    html = client.get("/?start=2026-09-01&end=2026-09-30&platform=all").text
    assert 'name="start" value="2026-09-01"' in html and 'name="end" value="2026-09-30"' in html
    assert "/?start=2026-09-01&amp;end=2026-09-30&platform=microsoft" in html
    assert "Verlauf 01.09.–30.09.2026" in html
    ts = client.get("/api/timeseries?start=2026-09-01&end=2026-09-30").json()
    assert all("2026-09-01" <= d["date"] <= "2026-09-30" for d in ts)
    # vertauschte Daten werden korrigiert
    assert client.get("/?start=2026-09-30&end=2026-09-01").status_code == 200

    # Plattformfilter für Auffälligkeiten
    with SessionLocal() as s:
        s.add(Alert(date=dt.date(2026, 10, 27), platform="microsoft", severity="warnung", rule="t", title="Nur-Microsoft-Testalarm", message="x"))
        s.commit()
    assert "Nur-Microsoft-Testalarm" in client.get("/?platform=microsoft").text
    assert "Nur-Microsoft-Testalarm" not in client.get("/?platform=google").text
    assert "Nur-Microsoft-Testalarm" in client.get("/?platform=all").text

    # Umbenennung wird auf die Historie übertragen, auch ohne neue Leistungszeilen
    with SessionLocal() as s:
        s.add(CampaignDaily(date=dt.date(2026, 10, 10), platform="microsoft", campaign_id="ms-1", campaign_name="Website traffic-Search-1", status="Active"))
        s.commit()
        assert names.sync_campaigns(s, "microsoft", {"ms-1": {"name": "MS Ads Bing_Search-1", "status": "Paused"}}) == 1
        s.commit()
        row = s.query(CampaignDaily).filter_by(platform="microsoft", campaign_id="ms-1").one()
        assert (row.campaign_name, row.status) == ("MS Ads Bing_Search-1", "Paused")
        assert names.sync_campaigns(s, "microsoft", {"ms-1": {"name": "MS Ads Bing_Search-1", "status": "Paused"}}) == 0
