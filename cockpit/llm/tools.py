"""Werkzeuge über den Datenbestand. Werden vom Chat (OpenAI) und vom MCP-Endpunkt (ChatGPT) gleichermaßen genutzt.

Alle Werkzeuge sind lesend, bis auf: create_experiment, update_experiment, update_suggestion_status,
add_lead, add_note. Keines greift schreibend auf Google Ads oder Microsoft Advertising zu.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..analysis import experiments as exp_mod
from ..analysis import kpis
from ..config import BASE_DIR, settings
from ..models import Alert, Experiment, Lead, Report, Suggestion, utcnow
from ..util import parse_date, today_local

KNOWLEDGE_DIR = BASE_DIR / "knowledge"


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]
    writes: bool = False
    tags: list[str] = field(default_factory=list)


REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, description: str, parameters: dict[str, Any] | None = None, writes: bool = False):
    def deco(fn):
        REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            parameters=parameters or {"type": "object", "properties": {}, "additionalProperties": False},
            fn=fn,
            writes=writes,
        )
        return fn

    return deco


def _period(args: dict[str, Any], default_days: int = 28) -> tuple[dt.date, dt.date]:
    end = parse_date(args.get("end"), today_local() - dt.timedelta(days=1))
    if args.get("start"):
        start = parse_date(args["start"], end - dt.timedelta(days=default_days - 1))
    else:
        days = int(args.get("days") or default_days)
        start = end - dt.timedelta(days=days - 1)
    return start, end


PERIOD_PROPS = {
    "days": {"type": "integer", "description": "Anzahl Tage bis gestern (Standard 28). Wird ignoriert, wenn start gesetzt ist."},
    "start": {"type": "string", "description": "Startdatum YYYY-MM-DD"},
    "end": {"type": "string", "description": "Enddatum YYYY-MM-DD (Standard: gestern)"},
    "platform": {"type": "string", "enum": ["all", "google", "microsoft"], "description": "Plattform (Standard all)"},
}


def _platform(args) -> str:
    return args.get("platform") or "all"


# ------------------------------------------------------------------ lesend

@tool(
    "get_overview",
    "Kennzahlen (Kosten, Klicks, Impressionen, Conversions, CTR, CPC, CVR, CPL, Impression Share) für einen Zeitraum, mit Vergleich zum gleich langen Vorzeitraum.",
    {"type": "object", "properties": PERIOD_PROPS, "additionalProperties": False},
)
def get_overview(session: Session, user: str, **args):
    start, end = _period(args)
    days = (end - start).days + 1
    return kpis.compare_periods(session, days, _platform(args), end)


@tool(
    "get_timeseries",
    "Zeitreihe der Kennzahlen je Tag oder Woche, z. B. um CPC-Entwicklung oder Saisonalität zu prüfen.",
    {"type": "object", "properties": {**PERIOD_PROPS, "granularity": {"type": "string", "enum": ["day", "week"]}}, "additionalProperties": False},
)
def get_timeseries(session: Session, user: str, **args):
    start, end = _period(args, 90)
    return kpis.timeseries(session, start, end, _platform(args), args.get("granularity") or "day")


@tool(
    "get_organic_queries",
    "Organische Suchanfragen aus der Google Search Console (Klicks, Impressionen, Position) für copilotenschule.de; Suchfilter optional.",
    {"type": "object", "properties": {**PERIOD_PROPS, "query": {"type": "string", "description": "Teilstring-Filter"}, "limit": {"type": "integer"}}, "additionalProperties": False},
)
def get_organic_queries(session: Session, user: str, **args):
    start, end = _period(args)
    return {"summary": kpis.organic_summary(session, start, end), "queries": kpis.organic_queries(session, start, end, int(args.get("limit") or 100), args.get("query") or None)}


@tool(
    "get_paid_organic_overlap",
    "Abgleich bezahlte Suchbegriffe gegen organische Rankings: Kannibalisierung (organisch Top 3 + bezahlte Klicks), organisch nah (Position 4–20 mit Nachfrage, kein Paid) und nur bezahlt mit Conversions (Content-Lücke).",
    {"type": "object", "properties": PERIOD_PROPS, "additionalProperties": False},
)
def get_paid_organic_overlap(session: Session, user: str, **args):
    start, end = _period(args)
    return kpis.paid_organic_overlap(session, start, end, _platform(args))


@tool(
    "get_sea_revenue",
    "Echte Ads-Leads aus Chris' Excel „Conversions SEA“ mit Nettoumsatz (Lexoffice) gegen die Werbekosten: Kosten je echtem Lead, ROAS, Abschlussquote. Umsatz kommt mit Wochen Verzug, daher lange Zeiträume (Standard 180 Tage).",
    {"type": "object", "properties": PERIOD_PROPS, "additionalProperties": False},
)
def get_sea_revenue(session: Session, user: str, **args):
    start, end = _period(args, 180)
    return kpis.sea_revenue(session, start, end, _platform(args))


@tool("get_campaigns", "Kampagnen mit Status, Gebotsstrategie, Budget, Enddatum und Kennzahlen im Zeitraum.", {"type": "object", "properties": PERIOD_PROPS, "additionalProperties": False})
def get_campaigns(session: Session, user: str, **args):
    start, end = _period(args)
    return kpis.campaigns(session, start, end, _platform(args))


@tool("get_ad_groups", "Anzeigengruppen mit Kennzahlen im Zeitraum.", {"type": "object", "properties": PERIOD_PROPS, "additionalProperties": False})
def get_ad_groups(session: Session, user: str, **args):
    start, end = _period(args)
    return kpis.ad_groups(session, start, end, _platform(args))


@tool(
    "get_keywords",
    "Keywords mit Matchtyp, Status, Auslieferungsstatus (z. B. geringes Suchvolumen), Qualitätsfaktor und Kennzahlen.",
    {"type": "object", "properties": {**PERIOD_PROPS, "sort": {"type": "string", "enum": ["cost", "clicks", "conversions", "impressions"]}, "limit": {"type": "integer"}, "only_active": {"type": "boolean"}}, "additionalProperties": False},
)
def get_keywords(session: Session, user: str, **args):
    start, end = _period(args)
    return kpis.keywords(session, start, end, _platform(args), args.get("sort") or "cost", int(args.get("limit") or 100), bool(args.get("only_active")))


@tool(
    "get_search_terms",
    "Suchbegriffsbericht: tatsächliche Suchanfragen mit Kosten, Klicks, Conversions und Status (ADDED/EXCLUDED/NONE). Für Negativ-Keyword-Vorschläge nach Kosten ohne Conversion filtern.",
    {"type": "object", "properties": {**PERIOD_PROPS, "min_cost": {"type": "number"}, "only_without_conversions": {"type": "boolean"}, "query": {"type": "string", "description": "Textfilter (enthält)"}, "limit": {"type": "integer"}}, "additionalProperties": False},
)
def get_search_terms(session: Session, user: str, **args):
    start, end = _period(args)
    return kpis.search_terms(session, start, end, _platform(args), float(args.get("min_cost") or 0), bool(args.get("only_without_conversions")), int(args.get("limit") or 100), args.get("query"))


@tool("get_ads", "Anzeigen (RSA) mit Anzeigenstärke, Ziel-URL, Titeln, Beschreibungen und Kennzahlen.", {"type": "object", "properties": PERIOD_PROPS, "additionalProperties": False})
def get_ads(session: Session, user: str, **args):
    start, end = _period(args)
    return kpis.ads(session, start, end, _platform(args))


@tool(
    "get_change_events",
    "Änderungsverlauf des Kontos: wer hat wann was geändert (Budget, Gebotsstrategie, Keywords, Anzeigen). Wichtig, um Effekte und Experimente zuzuordnen.",
    {"type": "object", "properties": {**PERIOD_PROPS, "limit": {"type": "integer"}}, "additionalProperties": False},
)
def get_change_events(session: Session, user: str, **args):
    start, end = _period(args, 30)
    return kpis.change_events(session, start, end, _platform(args), int(args.get("limit") or 200))


@tool("get_auction_insights", "Auktionsdaten der letzten erfassten Woche: Wettbewerber-Domains, Impression Share, Überschneidung, Outranking.", {"type": "object", "properties": {"platform": PERIOD_PROPS["platform"]}, "additionalProperties": False})
def get_auction_insights(session: Session, user: str, **args):
    return kpis.auction_insights(session, _platform(args))


@tool(
    "get_leads",
    "Echte Leads (Kontaktformular aus Trello, Erstgespräche aus Bookings, manuelle Einträge) mit Status, Kanal, Auftragswert und Erlös. Das ist die Wahrheitsebene neben den Ads-Conversions.",
    {"type": "object", "properties": {"days": PERIOD_PROPS["days"], "start": PERIOD_PROPS["start"], "end": PERIOD_PROPS["end"]}, "additionalProperties": False},
)
def get_leads(session: Session, user: str, **args):
    start, end = _period(args, 90)
    return kpis.leads_summary(session, start, end)


@tool(
    "get_alerts",
    "Zuletzt erkannte Auffälligkeiten (CPC-Sprung, keine Impressionen, Klicks ohne Conversion, teure Suchbegriffe, Enddatum, Conversion-Qualität).",
    {"type": "object", "properties": {"days": {"type": "integer"}, "only_open": {"type": "boolean"}}, "additionalProperties": False},
)
def get_alerts(session: Session, user: str, **args):
    since = today_local() - dt.timedelta(days=int(args.get("days") or 14))
    stmt = select(Alert).where(Alert.date >= since).order_by(Alert.created_at.desc())
    if args.get("only_open"):
        stmt = stmt.where(Alert.acknowledged.is_(False))
    return [
        {"id": a.id, "date": a.date.isoformat(), "rule": a.rule, "severity": a.severity, "title": a.title, "message": a.message, "acknowledged": a.acknowledged}
        for a in session.execute(stmt).scalars().all()
    ]


@tool(
    "list_suggestions",
    "Vorschläge des Cockpits mit Status (offen/umgesetzt/verworfen/später), Priorität, Begründung, erwartetem Effekt und Schritten.",
    {"type": "object", "properties": {"status": {"type": "string", "enum": ["alle", "offen", "umgesetzt", "verworfen", "später"]}, "limit": {"type": "integer"}}, "additionalProperties": False},
)
def list_suggestions(session: Session, user: str, **args):
    stmt = select(Suggestion).order_by(Suggestion.priority, Suggestion.created_at.desc())
    st = args.get("status") or "offen"
    if st != "alle":
        stmt = stmt.where(Suggestion.status == st)
    stmt = stmt.limit(int(args.get("limit") or 50))
    return [suggestion_dict(s) for s in session.execute(stmt).scalars().all()]


def suggestion_dict(s: Suggestion) -> dict[str, Any]:
    return {
        "id": s.id,
        "created_at": s.created_at.isoformat(timespec="minutes"),
        "source": s.source,
        "platform": s.platform,
        "category": s.category,
        "title": s.title,
        "rationale": s.rationale,
        "expected_effect": s.expected_effect,
        "steps": s.steps,
        "priority": s.priority,
        "confidence": s.confidence,
        "effort": s.effort,
        "status": s.status,
        "status_note": s.status_note,
        "status_changed_at": s.status_changed_at.isoformat(timespec="minutes") if s.status_changed_at else None,
        "verification": s.verification,
        "experiment_id": s.experiment_id,
    }


@tool("list_experiments", "Alle Experimente mit Hypothese, Zeitraum, Umfang und Status.", {"type": "object", "properties": {"status": {"type": "string"}}, "additionalProperties": False})
def list_experiments(session: Session, user: str, **args):
    stmt = select(Experiment).order_by(Experiment.start_date.desc())
    if args.get("status"):
        stmt = stmt.where(Experiment.status == args["status"])
    return [experiment_dict(e) for e in session.execute(stmt).scalars().all()]


def experiment_dict(e: Experiment) -> dict[str, Any]:
    return {
        "id": e.id,
        "name": e.name,
        "hypothesis": e.hypothesis,
        "platform": e.platform,
        "scope": e.scope,
        "start_date": e.start_date.isoformat(),
        "end_date": e.end_date.isoformat() if e.end_date else None,
        "baseline_days": e.baseline_days,
        "status": e.status,
        "created_by": e.created_by,
        "result_note": e.result_note,
        "tags": e.tags,
    }


@tool(
    "compare_experiment",
    "Vorher/Nachher-Vergleich eines Experiments: Kennzahlen Basis- vs. Testfenster, Konfidenzintervalle der Conversion-Rate, Störfaktoren aus dem Änderungsverlauf, Einordnung.",
    {"type": "object", "properties": {"experiment_id": {"type": "integer"}}, "required": ["experiment_id"], "additionalProperties": False},
)
def compare_experiment(session: Session, user: str, **args):
    e = session.get(Experiment, int(args["experiment_id"]))
    if not e:
        return {"error": "Experiment nicht gefunden"}
    return exp_mod.compare(session, e)


@tool(
    "compare_periods_custom",
    "Zwei beliebige Zeiträume gegeneinander vergleichen (z. B. Klickoptimierung vs. Conversion-Optimierung), optional auf Kampagnen eingeschränkt.",
    {
        "type": "object",
        "properties": {
            "a_start": {"type": "string"},
            "a_end": {"type": "string"},
            "b_start": {"type": "string"},
            "b_end": {"type": "string"},
            "platform": PERIOD_PROPS["platform"],
            "campaign_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["a_start", "a_end", "b_start", "b_end"],
        "additionalProperties": False,
    },
)
def compare_periods_custom(session: Session, user: str, **args):
    a = kpis.overview(session, parse_date(args["a_start"]), parse_date(args["a_end"]), _platform(args), args.get("campaign_ids"))
    b = kpis.overview(session, parse_date(args["b_start"]), parse_date(args["b_end"]), _platform(args), args.get("campaign_ids"))
    from ..util import pct_change

    return {"a": a, "b": b, "deltas_b_vs_a": {k: pct_change(b.get(k), a.get(k)) for k in ("impressions", "clicks", "cost", "conversions", "ctr", "avg_cpc", "cvr", "cpl")}}


@tool("list_reports", "Gespeicherte Wochen- und Monatsreports (Titel, Zeitraum).", {"type": "object", "properties": {"limit": {"type": "integer"}}, "additionalProperties": False})
def list_reports(session: Session, user: str, **args):
    rows = session.execute(select(Report).order_by(Report.created_at.desc()).limit(int(args.get("limit") or 12))).scalars().all()
    return [{"id": r.id, "type": r.type, "title": r.title, "period": f"{r.period_start} bis {r.period_end}", "created_at": r.created_at.isoformat(timespec="minutes")} for r in rows]


@tool("get_report", "Volltext eines Reports (Markdown).", {"type": "object", "properties": {"report_id": {"type": "integer"}}, "required": ["report_id"], "additionalProperties": False})
def get_report(session: Session, user: str, **args):
    r = session.get(Report, int(args["report_id"]))
    return {"id": r.id, "title": r.title, "content_md": r.content_md} if r else {"error": "nicht gefunden"}


@tool(
    "get_knowledge",
    "Wissensbasis des Cockpits: bisherige Analysen, Entscheidungen, Rahmenbedingungen (Ziel-CPL, Angebot, Zielgruppe). Ohne topic: Liste der Dokumente.",
    {"type": "object", "properties": {"topic": {"type": "string", "description": "Dateiname oder Stichwort"}}, "additionalProperties": False},
)
def get_knowledge(session: Session, user: str, **args):
    files = sorted(KNOWLEDGE_DIR.glob("*.md")) if KNOWLEDGE_DIR.exists() else []
    topic = (args.get("topic") or "").lower()
    if not topic:
        return {"documents": [f.name for f in files], "business": business_context()}
    out = []
    for f in files:
        txt = f.read_text(encoding="utf-8", errors="ignore")
        if topic in f.name.lower() or topic in txt.lower():
            out.append({"file": f.name, "content": txt[:20000]})
    return out or {"hint": "Kein Dokument zum Stichwort", "documents": [f.name for f in files]}


def business_context() -> dict[str, Any]:
    return {
        "unternehmen": "Copilotenschule (Marke der Yellow-Boat Consulting, Köln): Inhouse-Schulungen zu Microsoft Copilot, M365 und KI für Unternehmen im DACH-Raum.",
        "zielgruppe": "Entscheider: Geschäftsführung, HR/L&D, Team- und Abteilungsleitung.",
        "primaeres_ziel": "Qualifizierte Erstgespräche (Microsoft Bookings) und Kontaktanfragen für Inhouse-Firmentrainings.",
        "ziel_cpl_eur": settings.target_cpl,
        "abbruch_cpl_eur": settings.max_cpl,
        "durchschnittlicher_auftragswert_eur": settings.avg_deal_value,
        "abschlussquote": settings.close_rate,
        "max_wert_pro_lead_eur": round(settings.avg_deal_value * settings.close_rate, 0),
        "nicht_massnahmen": ["Performance Max", "AI Max", "Displaynetzwerk", "breite Keyword-Ausweitung ohne Negativliste"],
    }


@tool(
    "run_sql",
    "Nur-Lese-SQL (SELECT) auf der Cockpit-Datenbank für Fragen, die kein anderes Werkzeug beantwortet. Tabellen: campaign_daily, adgroup_daily, keyword_daily, search_term_daily, ad_daily, asset_daily, auction_insights, change_events, leads, experiments, suggestions, alerts, reports, conversion_actions. Kosten in EUR.",
    {"type": "object", "properties": {"sql": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["sql"], "additionalProperties": False},
)
def run_sql(session: Session, user: str, **args):
    sql = (args.get("sql") or "").strip().rstrip(";")
    if not re.match(r"^\s*(select|with)\b", sql, re.I) or re.search(r"\b(insert|update|delete|drop|alter|create|attach|pragma|replace)\b", sql, re.I):
        return {"error": "Nur SELECT-Abfragen erlaubt"}
    limit = min(int(args.get("limit") or 200), 1000)
    if not re.search(r"\blimit\b", sql, re.I):
        sql = f"{sql} LIMIT {limit}"
    try:
        result = session.execute(text(sql))
        cols = list(result.keys())
        rows = [dict(zip(cols, r)) for r in result.fetchall()]
        return {"columns": cols, "rows": rows, "count": len(rows)}
    except Exception as exc:
        return {"error": str(exc)[:500]}


# ---------------------------------------------------------------- schreibend (nur Cockpit-DB)

@tool(
    "create_experiment",
    "Experiment anlegen (Name, Hypothese, Start, optional Ende, Kampagnen-IDs, Basiszeitraum in Tagen). Verändert nichts in Google Ads.",
    {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "hypothesis": {"type": "string"},
            "start_date": {"type": "string"},
            "end_date": {"type": "string"},
            "platform": {"type": "string", "enum": ["google", "microsoft", "all"]},
            "campaign_ids": {"type": "array", "items": {"type": "string"}},
            "baseline_days": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["name", "hypothesis", "start_date"],
        "additionalProperties": False,
    },
    writes=True,
)
def create_experiment(session: Session, user: str, **args):
    e = Experiment(
        name=args["name"],
        hypothesis=args.get("hypothesis", ""),
        platform=args.get("platform") or "google",
        scope={"campaign_ids": args.get("campaign_ids") or []},
        start_date=parse_date(args["start_date"]),
        end_date=parse_date(args.get("end_date")),
        baseline_days=int(args.get("baseline_days") or 0),
        status="laufend" if parse_date(args["start_date"]) <= today_local() else "geplant",
        created_by=user,
        tags=args.get("tags") or [],
    )
    session.add(e)
    session.flush()
    return experiment_dict(e)


@tool(
    "update_experiment",
    "Experiment beenden, abbrechen oder Ergebnisnotiz setzen.",
    {"type": "object", "properties": {"experiment_id": {"type": "integer"}, "status": {"type": "string", "enum": ["geplant", "laufend", "abgeschlossen", "abgebrochen"]}, "end_date": {"type": "string"}, "result_note": {"type": "string"}}, "required": ["experiment_id"], "additionalProperties": False},
    writes=True,
)
def update_experiment(session: Session, user: str, **args):
    e = session.get(Experiment, int(args["experiment_id"]))
    if not e:
        return {"error": "nicht gefunden"}
    if args.get("status"):
        e.status = args["status"]
    if args.get("end_date"):
        e.end_date = parse_date(args["end_date"])
    if args.get("result_note"):
        e.result_note = args["result_note"]
    session.flush()
    return experiment_dict(e)


@tool(
    "update_suggestion_status",
    "Status eines Vorschlags setzen: umgesetzt (mit Datum), verworfen, später, offen. Bei 'umgesetzt' wird der Änderungsverlauf der folgenden Tage auf eine passende Änderung geprüft.",
    {"type": "object", "properties": {"suggestion_id": {"type": "integer"}, "status": {"type": "string", "enum": ["offen", "umgesetzt", "verworfen", "später", "geloescht"]}, "note": {"type": "string", "description": "Bei verworfen/geloescht: Begründung, damit der Vorschlag nicht wiederkommt"}, "implemented_on": {"type": "string", "description": "Datum der Umsetzung YYYY-MM-DD (Standard heute)"}}, "required": ["suggestion_id", "status"], "additionalProperties": False},
    writes=True,
)
def update_suggestion_status(session: Session, user: str, **args):
    s = session.get(Suggestion, int(args["suggestion_id"]))
    if not s:
        return {"error": "nicht gefunden"}
    s.status = args["status"]
    s.status_by = user
    s.status_note = args.get("note") or s.status_note
    when = parse_date(args.get("implemented_on"), today_local())
    s.status_changed_at = dt.datetime.combine(when, dt.time(12, 0)) if args["status"] == "umgesetzt" else utcnow()
    if args["status"] == "umgesetzt":
        s.verification = verify_against_changes(session, s, when)
        # Automatisch ein Experiment anlegen, damit die Wirkung später vergleichbar ist
        if not s.experiment_id:
            e = Experiment(
                name=f"Vorschlag #{s.id}: {s.title[:80]}",
                hypothesis=s.expected_effect or s.rationale,
                platform=s.platform or "google",
                scope={"campaign_ids": (s.evidence or {}).get("campaign_ids", [])},
                start_date=when,
                status="laufend",
                created_by=user,
                tags=["aus-vorschlag"],
            )
            session.add(e)
            session.flush()
            s.experiment_id = e.id
    session.flush()
    return suggestion_dict(s)


def verify_against_changes(session: Session, s: Suggestion, when: dt.date) -> dict[str, Any]:
    from ..models import ChangeEvent

    rows = session.execute(
        select(ChangeEvent).where(
            ChangeEvent.change_date_time >= dt.datetime.combine(when - dt.timedelta(days=1), dt.time.min),
            ChangeEvent.change_date_time <= dt.datetime.combine(when + dt.timedelta(days=3), dt.time.max),
        )
    ).scalars().all()
    wanted = {
        "negativ": ("CAMPAIGN_CRITERION", "AD_GROUP_CRITERION", "SHARED_SET", "SHARED_CRITERION"),
        "keyword": ("AD_GROUP_CRITERION",),
        "anzeige": ("AD_GROUP_AD", "AD", "ASSET"),
        "budget": ("CAMPAIGN_BUDGET", "CAMPAIGN"),
        "gebot": ("CAMPAIGN", "BIDDING_STRATEGY"),
        "landingpage": ("AD_GROUP_AD", "AD"),
        "conversion": ("CONVERSION_ACTION", "CUSTOMER_CONVERSION_GOAL", "CAMPAIGN_CONVERSION_GOAL"),
        "zeitplan": ("CAMPAIGN_CRITERION",),
        "standort": ("CAMPAIGN_CRITERION",),
    }
    cat = (s.category or "").lower()
    types = next((v for k, v in wanted.items() if k in cat), None)
    matches = [e for e in rows if not types or e.resource_type in types]
    return {
        "checked_window": f"{(when - dt.timedelta(days=1)).isoformat()} bis {(when + dt.timedelta(days=3)).isoformat()}",
        "matching_changes": [{"when": e.change_date_time.isoformat(timespec="minutes"), "summary": e.summary, "user": e.user_email} for e in matches[:10]],
        "found": bool(matches),
        "note": "Änderungsverlauf wird nächtlich nachgeladen; die Prüfung wiederholt sich beim nächsten Job." if not matches else "",
    }


@tool(
    "add_lead",
    "Lead manuell erfassen (z. B. Erstgespräch oder Anruf), mit Kanal und optionalem Auftragswert.",
    {"type": "object", "properties": {"date": {"type": "string"}, "company": {"type": "string"}, "name": {"type": "string"}, "channel": {"type": "string"}, "kind": {"type": "string", "enum": ["kontaktformular", "erstgespraech", "anruf", "sonstiges"]}, "status": {"type": "string"}, "value": {"type": "number"}, "notes": {"type": "string"}}, "required": ["company"], "additionalProperties": False},
    writes=True,
)
def add_lead(session: Session, user: str, **args):
    lead = Lead(
        source="manual",
        external_id=f"{user}-{utcnow().strftime('%Y%m%d%H%M%S%f')}",
        created_on=parse_date(args.get("date"), today_local()),
        company=args.get("company", ""),
        name=args.get("name", ""),
        channel=(args.get("channel") or "unbekannt").lower(),
        kind=args.get("kind") or "sonstiges",
        status=args.get("status") or "neu",
        value=float(args.get("value") or 0),
        notes=args.get("notes", ""),
    )
    session.add(lead)
    session.flush()
    return {"id": lead.id, "ok": True}


@tool(
    "list_search_term_decisions",
    "Triage-Stand der Suchbegriffe: offene und eingetragene Negativ-Kandidaten (Negativliste), behaltene und beobachtete Begriffe.",
    {"type": "object", "properties": {"platform": {"type": "string", "enum": ["google", "microsoft", "all"]}, "include_applied": {"type": "boolean"}}, "additionalProperties": False},
)
def list_search_term_decisions(session: Session, user: str, **args):
    from ..analysis import triage

    return {"summary": triage.summary(session), "negative_queue": triage.negative_queue(session, args.get("platform") or "all", bool(args.get("include_applied")))}


@tool(
    "decide_search_term",
    "Triage-Entscheidung für einen Suchbegriff speichern: ausschliessen (kommt auf die Negativliste), behalten oder beobachten. Nur nach ausdrücklicher Aussage des Nutzers, nie auf eigene Initiative.",
    {"type": "object", "properties": {"search_term": {"type": "string"}, "decision": {"type": "string", "enum": ["ausschliessen", "behalten", "beobachten"]}, "platform": {"type": "string", "enum": ["google", "microsoft"]}, "negative_match": {"type": "string", "enum": ["phrase", "exact"]}, "note": {"type": "string"}}, "required": ["search_term", "decision"], "additionalProperties": False},
    writes=True,
)
def decide_search_term(session: Session, user: str, **args):
    from ..analysis import triage

    row = triage.decide(session, user, args.get("platform") or "google", args["search_term"], args["decision"], args.get("negative_match") or "phrase", args.get("note") or "")
    return triage.row_dict(row)


@tool(
    "search_chat_history",
    "Frühere Gespräche mit Chris und Martin im Wortlaut durchsuchen (Gedächtnis), z. B. „Wann haben wir das Conversion-Ziel umgestellt?“. Suchwörter, optional Zeitraum.",
    {"type": "object", "properties": {"query": {"type": "string"}, "since": {"type": "string", "description": "YYYY-MM-DD"}, "until": {"type": "string", "description": "YYYY-MM-DD"}, "limit": {"type": "integer"}}, "required": ["query"], "additionalProperties": False},
)
def search_chat_history(session: Session, user: str, **args):
    from . import memory

    return memory.search(session, args["query"], parse_date(args.get("since")), parse_date(args.get("until")), int(args.get("limit") or 20))


MEMORY_TOOLS = {"search_chat_history"}


def openai_tools(include_writes: bool = True, memory: bool = True) -> list[dict[str, Any]]:
    return [
        {"type": "function", "name": t.name, "description": t.description, "parameters": t.parameters}
        for t in REGISTRY.values()
        if (include_writes or not t.writes) and (memory or t.name not in MEMORY_TOOLS)
    ]


def call(session: Session, user: str, name: str, args: dict[str, Any]) -> Any:
    spec = REGISTRY.get(name)
    if not spec:
        return {"error": f"Unbekanntes Werkzeug {name}"}
    return spec.fn(session, user, **(args or {}))
