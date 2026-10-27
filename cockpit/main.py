"""FastAPI-Anwendung: Web-Oberfläche, JSON-API, Webhooks, MCP-Mount."""
from __future__ import annotations

import datetime as dt
import hmac
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import markdown as md_lib
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import __version__, jobs
from .analysis import anomalies, experiments as exp_mod, kpis, triage
from .auth import SESSION_COOKIE, SESSION_MAX_AGE, authenticate, check_csrf, csrf_token, current_user, current_user_api, make_session_cookie
from .config import settings
from .db import get_session, init_db
from .llm import chat as chat_mod, choices as chat_choices
from .llm.tools import call as tool_call, experiment_dict, suggestion_dict
from .mcp_server import mcp_asgi_app, mcp_server, mount_path
from .models import Alert, Experiment, JobRun, Lead, Report, Suggestion, utcnow
from .util import eur, get_setting, get_setting_json, num, parse_date, pct, set_setting, today_local, update_env_file, yesterday

log = logging.getLogger(__name__)
BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.filters.update({"eur": eur, "pct": pct, "num": num})
templates.env.globals.update({"version": __version__, "settings": settings})

_mcp_app = mcp_asgi_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    async with mcp_server.session_manager.run():
        yield


app = FastAPI(title="SEA-Cockpit Copilotenschule", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
app.mount(mount_path(), _mcp_app)


# ------------------------------------------------------------------ Hilfen

def render(request: Request, name: str, user: str | None = None, **ctx) -> HTMLResponse:
    ctx.update({"request": request, "user": user, "csrf": csrf_token(request), "demo": settings.demo_mode, "path": request.url.path})
    return templates.TemplateResponse(request, name, ctx)


def _period_from(request: Request) -> tuple[dt.date, dt.date, int, str]:
    q = request.query_params
    days = int(q.get("days") or 28)
    end = parse_date(q.get("end"), yesterday())
    start = parse_date(q.get("start"), end - dt.timedelta(days=days - 1))
    platform = q.get("platform") or "all"
    return start, end, days, platform


def md(text: str) -> str:
    return md_lib.markdown(text or "", extensions=["tables", "sane_lists"])


templates.env.filters["md"] = md
templates.env.filters["strip_choices"] = lambda text: chat_choices.parse(text or "")[0]


# ------------------------------------------------------------------ Login

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return render(request, "login.html", error=None)


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if not authenticate(username, password):
        return render(request, "login.html", error="Benutzername oder Passwort falsch.")
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, make_session_cookie(username.strip().lower()), max_age=SESSION_MAX_AGE, httponly=True, samesite="lax", secure=settings.base_url.startswith("https"))
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/healthz")
def healthz(session: Session = Depends(get_session)):
    last = session.execute(select(func.max(JobRun.started_at))).scalar()
    return {"ok": True, "version": __version__, "last_job": last.isoformat() if last else None}


# ------------------------------------------------------------------ Dashboard

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    start, end, days, platform = _period_from(request)
    cmp = kpis.compare_periods(session, days, platform, end)
    camps = kpis.campaigns(session, start, end, platform)
    open_sugg = session.execute(select(Suggestion).where(Suggestion.status == "offen").order_by(Suggestion.priority, Suggestion.created_at.desc()).limit(5)).scalars().all()
    alerts = session.execute(select(Alert).where(Alert.acknowledged.is_(False)).order_by(Alert.created_at.desc()).limit(6)).scalars().all()
    exps = session.execute(select(Experiment).where(Experiment.status == "laufend").order_by(Experiment.start_date.desc()).limit(3)).scalars().all()
    exp_cards = [exp_mod.compare(session, e) for e in exps]
    leads = kpis.leads_summary(session, start, end)
    last_data = session.execute(select(func.max(kpis.CampaignDaily.date))).scalar()
    wasted = kpis.search_terms(session, start, end, platform, min_cost=10, only_without_conversions=True, limit=5)
    return render(
        request,
        "dashboard.html",
        user,
        cmp=cmp,
        campaigns=camps,
        suggestions=open_sugg,
        alerts=alerts,
        experiments=exp_cards,
        leads=leads,
        days=days,
        platform=platform,
        start=start,
        end=end,
        last_data=last_data,
        wasted=wasted,
        status=jobs.status_summary(session),
    )


@app.get("/api/timeseries")
def api_timeseries(request: Request, user: str = Depends(current_user_api), session: Session = Depends(get_session)):
    q = request.query_params
    days = int(q.get("days") or 90)
    end = parse_date(q.get("end"), yesterday())
    start = end - dt.timedelta(days=days - 1)
    gran = q.get("granularity") or ("week" if days > 120 else "day")
    return kpis.timeseries(session, start, end, q.get("platform") or "all", gran)


# ------------------------------------------------------------------ Vorschläge

@app.get("/vorschlaege", response_class=HTMLResponse)
def suggestions_page(request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    status = request.query_params.get("status") or "offen"
    stmt = select(Suggestion).order_by(Suggestion.priority, Suggestion.created_at.desc())
    if status != "alle":
        stmt = stmt.where(Suggestion.status == status)
    rows = session.execute(stmt.limit(200)).scalars().all()
    counts = dict(session.execute(select(Suggestion.status, func.count(Suggestion.id)).group_by(Suggestion.status)).all())
    return render(request, "suggestions.html", user, suggestions=rows, status=status, counts=counts)


@app.get("/vorschlaege/{sid}", response_class=HTMLResponse)
def suggestion_detail(sid: int, request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    s = session.get(Suggestion, sid)
    if not s:
        raise HTTPException(404)
    exp = session.get(Experiment, s.experiment_id) if s.experiment_id else None
    exp_cmp = exp_mod.compare(session, exp) if exp else None
    report = session.get(Report, s.report_id) if s.report_id else None
    return render(request, "suggestion_detail.html", user, s=s, exp=exp, exp_cmp=exp_cmp, report=report)


@app.post("/vorschlaege/{sid}/status")
def suggestion_status(sid: int, request: Request, status: str = Form(...), note: str = Form(""), implemented_on: str = Form(""), csrf: str = Form(...), user: str = Depends(current_user), session: Session = Depends(get_session)):
    check_csrf(request, csrf)
    tool_call(session, user, "update_suggestion_status", {"suggestion_id": sid, "status": status, "note": note, "implemented_on": implemented_on or None})
    return RedirectResponse(f"/vorschlaege/{sid}", status_code=303)


@app.post("/vorschlaege/neu")
def suggestion_new(request: Request, title: str = Form(...), category: str = Form("sonstiges"), rationale: str = Form(""), expected_effect: str = Form(""), steps: str = Form(""), priority: int = Form(2), csrf: str = Form(...), user: str = Depends(current_user), session: Session = Depends(get_session)):
    check_csrf(request, csrf)
    s = Suggestion(source="manual", title=title[:255], category=category, rationale=rationale, expected_effect=expected_effect, steps=[l.strip() for l in steps.splitlines() if l.strip()], priority=priority, confidence="mittel", status_by=user)
    session.add(s)
    session.flush()
    return RedirectResponse(f"/vorschlaege/{s.id}", status_code=303)


# ------------------------------------------------------------------ Experimente

@app.get("/experimente", response_class=HTMLResponse)
def experiments_page(request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    rows = session.execute(select(Experiment).order_by(Experiment.start_date.desc())).scalars().all()
    cards = [exp_mod.compare(session, e) for e in rows]
    camps = kpis.campaigns(session, yesterday() - dt.timedelta(days=89), yesterday())
    return render(request, "experiments.html", user, cards=cards, campaigns=camps)


@app.post("/experimente/neu")
def experiment_new(request: Request, name: str = Form(...), hypothesis: str = Form(""), start_date: str = Form(...), end_date: str = Form(""), platform: str = Form("google"), campaign_ids: list[str] = Form([]), baseline_days: int = Form(0), csrf: str = Form(...), user: str = Depends(current_user), session: Session = Depends(get_session)):
    check_csrf(request, csrf)
    res = tool_call(session, user, "create_experiment", {"name": name, "hypothesis": hypothesis, "start_date": start_date, "end_date": end_date or None, "platform": platform, "campaign_ids": campaign_ids, "baseline_days": baseline_days})
    return RedirectResponse(f"/experimente/{res['id']}", status_code=303)


@app.get("/experimente/{eid}", response_class=HTMLResponse)
def experiment_detail(eid: int, request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    e = session.get(Experiment, eid)
    if not e:
        raise HTTPException(404)
    cmp = exp_mod.compare(session, e)
    w = exp_mod.windows(e)
    series = kpis.timeseries(session, w["base_start"], w["test_end"], e.platform or "all", "day", (e.scope or {}).get("campaign_ids") or None)
    sugg = session.execute(select(Suggestion).where(Suggestion.experiment_id == eid)).scalars().all()
    return render(request, "experiment_detail.html", user, e=e, cmp=cmp, series_json=json.dumps(series), suggestions=sugg)


@app.post("/experimente/{eid}/status")
def experiment_status(eid: int, request: Request, status: str = Form(...), end_date: str = Form(""), result_note: str = Form(""), csrf: str = Form(...), user: str = Depends(current_user), session: Session = Depends(get_session)):
    check_csrf(request, csrf)
    tool_call(session, user, "update_experiment", {"experiment_id": eid, "status": status, "end_date": end_date or None, "result_note": result_note or None})
    return RedirectResponse(f"/experimente/{eid}", status_code=303)


# ------------------------------------------------------------------ Suchbegriffe / Keywords / Anzeigen

@app.get("/suchbegriffe", response_class=HTMLResponse)
def search_terms_page(request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    start, end, days, platform = _period_from(request)
    q = request.query_params
    only_waste = q.get("waste") == "1"
    only_open = q.get("offen", "1") == "1"
    terms = kpis.search_terms(session, start, end, platform, float(q.get("min_cost") or 0), only_waste, 400, q.get("q") or None)
    decisions = triage.decisions_map(session, platform)
    for t in terms:
        d = decisions.get((t["platform"], t["search_term"]))
        t["decision"] = d.decision if d else ""
        t["decision_applied"] = bool(d and d.applied)
    if only_open:
        terms = [t for t in terms if not t["decision"]]
    terms = terms[:300]
    return render(request, "search_terms.html", user, terms=terms, days=days, platform=platform, start=start, end=end, waste=only_waste, q=q.get("q", ""), min_cost=q.get("min_cost", ""), offen=only_open, triage=triage.summary(session))


@app.post("/api/suchbegriffe/entscheidung")
async def search_term_decide(request: Request, user: str = Depends(current_user_api), session: Session = Depends(get_session)):
    body = await request.json()
    try:
        row = triage.decide(session, user, str(body.get("platform") or "google"), str(body.get("search_term") or ""), str(body.get("decision") or ""), str(body.get("negative_match") or "phrase"), str(body.get("note") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return JSONResponse({"ok": True, "decision": triage.row_dict(row), "summary": triage.summary(session)})


@app.get("/negativliste", response_class=HTMLResponse)
def negative_list_page(request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    platform = request.query_params.get("platform") or "all"
    return render(request, "negative_list.html", user, platform=platform, queue=triage.negative_queue(session, platform, include_applied=False), applied=triage.negative_queue(session, platform, include_applied=True), text=triage.negative_list_text(session, platform), summary=triage.summary(session))


@app.post("/negativliste/angewendet")
async def negative_list_applied(request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    form = await request.form()
    check_csrf(request, form.get("csrf", ""))
    ids = [int(x) for x in form.getlist("id") if str(x).isdigit()]
    triage.mark_applied(session, ids, form.get("undo") != "1")
    return RedirectResponse("/negativliste", status_code=303)


@app.get("/keywords", response_class=HTMLResponse)
def keywords_page(request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    start, end, days, platform = _period_from(request)
    kws = kpis.keywords(session, start, end, platform, request.query_params.get("sort") or "cost", 300)
    return render(request, "keywords.html", user, keywords=kws, days=days, platform=platform, start=start, end=end)


@app.get("/anzeigen", response_class=HTMLResponse)
def ads_page(request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    start, end, days, platform = _period_from(request)
    rows = kpis.ads(session, start, end, platform)
    auction = kpis.auction_insights(session, platform)
    changes = kpis.change_events(session, start, end, platform, 100)
    return render(request, "ads.html", user, ads=rows, auction=auction, changes=changes, days=days, platform=platform, start=start, end=end)


@app.get("/organisch", response_class=HTMLResponse)
def organic_page(request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    start, end, days, platform = _period_from(request)
    q = request.query_params.get("q") or ""
    summary = kpis.organic_summary(session, start, end)
    queries = kpis.organic_queries(session, start, end, 200, q or None)
    overlap = kpis.paid_organic_overlap(session, start, end, platform)
    return render(request, "organic.html", user, summary=summary, queries=queries, overlap=overlap, days=days, platform=platform, start=start, end=end, q=q, gsc_ready=settings.gsc_ready)


# ------------------------------------------------------------------ Leads

@app.get("/leads", response_class=HTMLResponse)
def leads_page(request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    start, end, days, platform = _period_from(request)
    rows = session.execute(select(Lead).where(Lead.created_on >= start, Lead.created_on <= end).order_by(Lead.created_on.desc())).scalars().all()
    summary = kpis.leads_summary(session, start, end)
    ov = kpis.overview(session, start, end, "all")
    return render(request, "leads.html", user, leads=rows, summary=summary, ov=ov, days=days, start=start, end=end)


@app.post("/leads/neu")
def lead_new(request: Request, date: str = Form(""), company: str = Form(...), name: str = Form(""), channel: str = Form("unbekannt"), kind: str = Form("kontaktformular"), status: str = Form("neu"), value: float = Form(0), notes: str = Form(""), csrf: str = Form(...), user: str = Depends(current_user), session: Session = Depends(get_session)):
    check_csrf(request, csrf)
    tool_call(session, user, "add_lead", {"date": date or None, "company": company, "name": name, "channel": channel, "kind": kind, "status": status, "value": value, "notes": notes})
    return RedirectResponse("/leads", status_code=303)


@app.post("/leads/{lid}")
def lead_update(lid: int, request: Request, status: str = Form(...), channel: str = Form("unbekannt"), value: float = Form(0), revenue: float = Form(0), csrf: str = Form(...), user: str = Depends(current_user), session: Session = Depends(get_session)):
    check_csrf(request, csrf)
    lead = session.get(Lead, lid)
    if lead:
        lead.status, lead.channel, lead.value, lead.revenue = status, channel, value, revenue
    return RedirectResponse("/leads", status_code=303)


@app.post("/api/webhooks/booking")
async def booking_webhook(request: Request, session: Session = Depends(get_session)):
    """Power Automate: „Wenn ein Termin erstellt wird“ (Bookings) → HTTP POST hierher.
    Header: X-Webhook-Secret: <BOOKINGS_WEBHOOK_SECRET>
    Body (JSON): {"id": "...", "start": "2026-11-03T10:00:00", "customer_name": "...", "customer_email": "...", "service": "Erstgespräch", "notes": "..."}"""
    secret = request.headers.get("x-webhook-secret", "")
    if not settings.bookings_webhook_secret or not hmac.compare_digest(secret, settings.bookings_webhook_secret):
        raise HTTPException(401, "ungültiges Secret")
    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(400, "JSON erwartet")
    ext = str(payload.get("id") or payload.get("bookingId") or uuid.uuid4())
    when = parse_date(str(payload.get("created") or payload.get("start") or ""), today_local())
    email = payload.get("customer_email") or payload.get("customerEmail") or ""
    company = payload.get("company") or (email.split("@")[-1] if "@" in email else "")
    existing = session.execute(select(Lead).where(Lead.source == "booking", Lead.external_id == ext)).scalar_one_or_none()
    if existing:
        return {"ok": True, "duplicate": True}
    session.add(
        Lead(
            source="booking",
            external_id=ext,
            created_on=when,
            name=payload.get("customer_name") or payload.get("customerName") or "",
            company=company,
            email=email,
            phone=payload.get("customer_phone") or "",
            status="kontaktiert",
            channel=(payload.get("channel") or "unbekannt").lower(),
            kind="erstgespraech",
            notes=f"{payload.get('service', '')} {payload.get('notes', '')}".strip(),
        )
    )
    return {"ok": True}


# ------------------------------------------------------------------ Alerts

@app.get("/alerts", response_class=HTMLResponse)
def alerts_page(request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    rows = session.execute(select(Alert).order_by(Alert.acknowledged, Alert.created_at.desc()).limit(200)).scalars().all()
    return render(request, "alerts.html", user, alerts=rows, thresholds=anomalies.thresholds(session))


@app.post("/alerts/{aid}/ack")
def alert_ack(aid: int, request: Request, csrf: str = Form(...), user: str = Depends(current_user), session: Session = Depends(get_session)):
    check_csrf(request, csrf)
    a = session.get(Alert, aid)
    if a:
        a.acknowledged = True
    return RedirectResponse("/alerts", status_code=303)


# ------------------------------------------------------------------ Reports

@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    rows = session.execute(select(Report).order_by(Report.created_at.desc()).limit(60)).scalars().all()
    return render(request, "reports.html", user, reports=rows)


@app.get("/reports/{rid}", response_class=HTMLResponse)
def report_detail(rid: int, request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    r = session.get(Report, rid)
    if not r:
        raise HTTPException(404)
    sugg = session.execute(select(Suggestion).where(Suggestion.report_id == rid).order_by(Suggestion.priority)).scalars().all()
    return render(request, "report_detail.html", user, r=r, suggestions=sugg)


# ------------------------------------------------------------------ Chat

@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    thread = request.query_params.get("thread") or f"{user}-{today_local().isoformat()}"
    msgs = chat_mod.history(session, thread, 40)
    return render(request, "chat.html", user, thread=thread, messages=msgs, llm_ready=settings.openai_ready)


def _own_thread(user: str, thread: str) -> str:
    """Threads sind nutzergebunden: fremde Threads werden auf den eigenen Standard-Thread umgebogen."""
    thread = (thread or "").strip()[:64]
    if not thread or not thread.startswith(f"{user}-") and not thread.startswith(f"panel-{user}"):
        return f"panel-{user}"
    return thread


@app.post("/api/chat")
async def api_chat(request: Request, user: str = Depends(current_user_api), session: Session = Depends(get_session)):
    body = await request.json()
    question = (body.get("question") or "").strip()
    thread = _own_thread(user, body.get("thread") or f"{user}-{today_local().isoformat()}")
    if not question:
        raise HTTPException(400, "Frage fehlt")
    page = body.get("page") if isinstance(body.get("page"), dict) else None
    if page:
        page = {"path": str(page.get("path") or "/")[:200], "query": {str(k)[:40]: str(v)[:120] for k, v in (page.get("query") or {}).items()} if isinstance(page.get("query"), dict) else {}}
    result = chat_mod.ask(session, user, thread, question, page)
    result["answer_html"] = md(result["answer"])
    return JSONResponse(result)


@app.get("/api/chat/history")
def api_chat_history(request: Request, user: str = Depends(current_user_api), session: Session = Depends(get_session)):
    thread = _own_thread(user, request.query_params.get("thread") or f"panel-{user}")
    msgs = chat_mod.history(session, thread, 40)
    out = []
    for m in msgs:
        if m.role == "assistant":
            clean, cards = chat_choices.parse(m.content)
            out.append({"role": "assistant", "html": md(clean), "choices": cards})
        elif m.role == "user":
            out.append({"role": "user", "text": m.content})
    return JSONResponse({"thread": thread, "messages": out})


# ------------------------------------------------------------------ OAuth Microsoft Advertising

@app.get("/oauth/microsoft/start")
def ms_oauth_start(request: Request, user: str = Depends(current_user)):
    from .connectors import microsoft_ads

    if not settings.ms_client_id:
        raise HTTPException(400, "MS_ADS_CLIENT_ID fehlt in der .env")
    state = csrf_token(request)
    return RedirectResponse(microsoft_ads.auth_url(state=state), status_code=303)


@app.get("/oauth/microsoft/callback", response_class=HTMLResponse)
def ms_oauth_callback(request: Request, user: str = Depends(current_user)):
    """Microsoft leitet nach der Zustimmung hierher; Code wird gegen ein Refresh-Token getauscht und in der .env gespeichert."""
    from .connectors import microsoft_ads

    q = request.query_params
    if q.get("error"):
        return render(request, "oauth_result.html", user, ok=False, message=f"{q.get('error')}: {q.get('error_description', '')}")
    if not q.get("code"):
        raise HTTPException(400, "code fehlt")
    if q.get("state") != csrf_token(request):
        return render(request, "oauth_result.html", user, ok=False, message="Ungültiger state-Parameter – bitte die Verbindung erneut über den Admin-Bereich starten.")
    try:
        token = microsoft_ads.refresh_token_from_redirect(None, None, str(request.url))
    except Exception as exc:
        log.exception("Microsoft-OAuth fehlgeschlagen")
        return render(request, "oauth_result.html", user, ok=False, message=f"Token-Tausch fehlgeschlagen: {str(exc)[:400]}")
    update_env_file({"MS_ADS_REFRESH_TOKEN": token, "MS_ADS_ENABLED": "1"})
    settings.ms_refresh_token, settings.ms_enabled = token, True
    try:
        check = microsoft_ads.test_connection()
    except Exception as exc:
        log.exception("Microsoft-Verbindungstest fehlgeschlagen")
        check = f"Token gespeichert, aber der Verbindungstest schlug fehl: {str(exc)[:400]}"
    return render(request, "oauth_result.html", user, ok=True, message=check)


@app.get("/hilfe", response_class=HTMLResponse)
def help_page(request: Request, user: str = Depends(current_user)):
    text = (BASE.parent / "docs" / "BRIEFING-CHRIS.md").read_text(encoding="utf-8")
    return render(request, "help.html", user, body=md(text))


# ------------------------------------------------------------------ Admin

@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    runs = session.execute(select(JobRun).order_by(JobRun.started_at.desc()).limit(30)).scalars().all()
    checks = {
        "Google Ads": settings.google_ready,
        "Microsoft Ads": settings.ms_ready,
        "Trello": settings.trello_ready,
        "Search Console": settings.gsc_ready,
        f"Sprachmodell ({settings.llm_provider})": settings.openai_ready,
        "Teams-Webhook": bool(settings.teams_webhook_url),
        "SMTP": bool(settings.smtp_host and settings.alert_email_to),
        "Bookings-Webhook": bool(settings.bookings_webhook_secret),
        "MCP-Token": bool(settings.mcp_token),
    }
    return render(request, "admin.html", user, runs=runs, checks=checks, thresholds=anomalies.thresholds(session), mcp_url=f"{settings.base_url}{mount_path()}/mcp")


@app.post("/admin/job/{name}")
def admin_job(name: str, request: Request, csrf: str = Form(...), user: str = Depends(current_user), session: Session = Depends(get_session)):
    check_csrf(request, csrf)
    if name == "nightly":
        jobs.nightly(session)
    elif name == "weekly":
        jobs.weekly(session, send=False)
    elif name == "monthly":
        jobs.monthly(session, send=False)
    elif name == "alerts":
        for p in ("google", "microsoft"):
            anomalies.persist(session, yesterday(), p, anomalies.evaluate(session, yesterday(), p))
    else:
        raise HTTPException(404)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/thresholds")
async def admin_thresholds(request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    form = await request.form()
    check_csrf(request, form.get("csrf", ""))
    current = anomalies.thresholds(session)
    new = {}
    for k, v in current.items():
        raw = form.get(k)
        if raw is None:
            continue
        if isinstance(v, bool):
            new[k] = raw in ("1", "on", "true")
        elif isinstance(v, int):
            new[k] = int(raw)
        else:
            new[k] = float(raw)
    set_setting(session, "alert_thresholds", json.dumps(new))
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/job/{rid}/log", response_class=HTMLResponse)
def admin_job_log(rid: int, request: Request, user: str = Depends(current_user), session: Session = Depends(get_session)):
    run = session.get(JobRun, rid)
    if not run:
        raise HTTPException(404)
    return render(request, "job_log.html", user, run=run)


# ------------------------------------------------------------------ JSON-API (für eigene Skripte)

@app.get("/api/suggestions")
def api_suggestions(request: Request, user: str = Depends(current_user_api), session: Session = Depends(get_session)):
    return tool_call(session, user, "list_suggestions", {"status": request.query_params.get("status") or "offen"})


@app.get("/api/experiments")
def api_experiments(user: str = Depends(current_user_api), session: Session = Depends(get_session)):
    return tool_call(session, user, "list_experiments", {})
