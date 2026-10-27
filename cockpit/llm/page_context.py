"""Seitenkontext für das Chat-Panel: Welche Daten sieht der Nutzer gerade?

Das Panel schickt bei jeder Frage den Pfad und die Query-Parameter der offenen Seite mit. Daraus wird
dasselbe Datenpaket gebaut, das auch die Seite rendert (gleiche Funktionen, gleiche Filter), kompakt
als JSON. So antwortet das Modell auf die konkreten Zahlen vor dem Nutzer statt generisch – und kann
mit den Werkzeugen trotzdem tiefer graben.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import jobs
from ..analysis import anomalies, experiments as exp_mod, kpis
from ..models import Alert, Experiment, Lead, Report, Suggestion
from ..util import json_dumps, parse_date, yesterday
from .tools import experiment_dict, suggestion_dict

MAX_CHARS = 28_000


def _period(query: dict[str, str]) -> tuple[dt.date, dt.date, int, str]:
    days = int(query.get("days") or 28)
    end = parse_date(query.get("end"), yesterday())
    start = parse_date(query.get("start"), end - dt.timedelta(days=days - 1))
    return start, end, days, query.get("platform") or "all"


def _lead_row(l: Lead) -> dict[str, Any]:
    return {"id": l.id, "date": l.created_on.isoformat(), "source": l.source, "kind": l.kind, "status": l.status, "channel": l.channel, "company": l.company, "name": l.name, "value": l.value, "revenue": l.revenue, "notes": (l.notes or "")[:120]}


def _alert_row(a: Alert) -> dict[str, Any]:
    return {"id": a.id, "date": a.date.isoformat(), "severity": a.severity, "rule": a.rule, "title": a.title, "message": a.message, "acknowledged": a.acknowledged}


def build(session: Session, path: str, query: dict[str, str] | None = None) -> dict[str, Any]:
    """Gibt {"title", "description", "data"} zurück; data ist JSON-fähig und wird bei Bedarf gekürzt."""
    query = query or {}
    path = (path or "/").rstrip("/") or "/"
    start, end, days, platform = _period(query)
    period = {"start": start.isoformat(), "end": end.isoformat(), "days": days, "platform": platform}
    title, desc, data = path, "", {}

    if path == "/":
        title, desc = "Übersicht", f"Dashboard für {start}–{end} ({days} Tage, Plattform {platform}) mit Vergleich zur Vorperiode."
        data = {
            "period": period,
            "kpis_vs_previous": kpis.compare_periods(session, days, platform, end),
            "campaigns": kpis.campaigns(session, start, end, platform),
            "open_suggestions": [{"id": s.id, "title": s.title, "priority": s.priority, "category": s.category} for s in session.execute(select(Suggestion).where(Suggestion.status == "offen").order_by(Suggestion.priority).limit(8)).scalars()],
            "open_alerts": [_alert_row(a) for a in session.execute(select(Alert).where(Alert.acknowledged.is_(False)).order_by(Alert.created_at.desc()).limit(8)).scalars()],
            "running_experiments": [exp_mod.compare(session, e) for e in session.execute(select(Experiment).where(Experiment.status == "laufend").limit(3)).scalars()],
            "leads": kpis.leads_summary(session, start, end) | {"leads": "(Liste auf /leads)"},
            "wasted_search_terms": kpis.search_terms(session, start, end, platform, min_cost=10, only_without_conversions=True, limit=8),
            "jobs": jobs.status_summary(session),
        }
    elif path == "/vorschlaege":
        status = query.get("status") or "offen"
        stmt = select(Suggestion).order_by(Suggestion.priority, Suggestion.created_at.desc())
        if status != "alle":
            stmt = stmt.where(Suggestion.status == status)
        rows = session.execute(stmt.limit(60)).scalars().all()
        title, desc = "Vorschläge", f"Liste der Vorschläge mit Status „{status}“."
        data = {"status_filter": status, "suggestions": [suggestion_dict(s) for s in rows]}
    elif m := re.fullmatch(r"/vorschlaege/(\d+)", path):
        s = session.get(Suggestion, int(m.group(1)))
        if s:
            title, desc = f"Vorschlag #{s.id}: {s.title}", "Detailseite eines Vorschlags inkl. Begründung, Schritten, Status und Verifikation."
            data = {"suggestion": suggestion_dict(s)}
            if s.experiment_id and (e := session.get(Experiment, s.experiment_id)):
                data["experiment"] = exp_mod.compare(session, e)
            if s.report_id and (r := session.get(Report, s.report_id)):
                data["report"] = {"id": r.id, "title": r.title, "period": f"{r.period_start}–{r.period_end}"}
    elif path == "/experimente":
        rows = session.execute(select(Experiment).order_by(Experiment.start_date.desc()).limit(12)).scalars().all()
        title, desc = "Experimente", "Alle Experimente mit Vorher/Nachher-Vergleich und Störfaktoren."
        data = {"experiments": [exp_mod.compare(session, e) for e in rows]}
    elif m := re.fullmatch(r"/experimente/(\d+)", path):
        e = session.get(Experiment, int(m.group(1)))
        if e:
            w = exp_mod.windows(e)
            title, desc = f"Experiment #{e.id}: {e.name}", "Detailseite eines Experiments: Hypothese, Zeitfenster, Vergleich, Tagesverlauf, Störfaktoren."
            data = {
                "experiment": experiment_dict(e),
                "comparison": exp_mod.compare(session, e),
                "daily_series": kpis.timeseries(session, w["base_start"], w["test_end"], e.platform or "all", "day", (e.scope or {}).get("campaign_ids") or None),
                "linked_suggestions": [suggestion_dict(s) for s in session.execute(select(Suggestion).where(Suggestion.experiment_id == e.id)).scalars()],
            }
    elif path == "/suchbegriffe":
        only_waste = query.get("waste") == "1"
        terms = kpis.search_terms(session, start, end, platform, float(query.get("min_cost") or 0), only_waste, 150, query.get("q") or None)
        title, desc = "Suchbegriffe", f"Suchbegriffe {start}–{end}" + (", nur Begriffe mit Kosten ohne Conversion" if only_waste else "") + (f", Filter „{query['q']}“" if query.get("q") else "") + ". Liste auf 150 Zeilen gekürzt, sortiert nach Kosten."
        data = {"period": period, "filters": {"waste_only": only_waste, "q": query.get("q", ""), "min_cost": query.get("min_cost", "")}, "search_terms": terms}
    elif path == "/keywords":
        kws = kpis.keywords(session, start, end, platform, query.get("sort") or "cost", 150)
        title, desc = "Keywords", f"Keywords {start}–{end}, sortiert nach {query.get('sort') or 'cost'}, auf 150 Zeilen gekürzt."
        data = {"period": period, "keywords": kws}
    elif path == "/anzeigen":
        title, desc = "Anzeigen", f"Anzeigen (RSA) mit Leistung {start}–{end}, Auktionsdaten und Änderungsverlauf."
        data = {"period": period, "ads": kpis.ads(session, start, end, platform), "auction_insights": kpis.auction_insights(session, platform), "change_events": kpis.change_events(session, start, end, platform, 60)}
    elif path == "/leads":
        rows = session.execute(select(Lead).where(Lead.created_on >= start, Lead.created_on <= end).order_by(Lead.created_on.desc()).limit(120)).scalars().all()
        summary = kpis.leads_summary(session, start, end)
        summary.pop("leads", None)
        title, desc = "Leads", f"Leads {start}–{end} aus Trello (inkl. Bookings-Termine als Art „erstgespraech“) und manuell, mit Ads-Kennzahlen desselben Zeitraums. Kanal „unbekannt“ heißt: Quelle nicht zuordenbar."
        data = {"period": period, "summary": summary, "ads_same_period": kpis.overview(session, start, end, "all"), "leads": [_lead_row(l) for l in rows]}
    elif path == "/alerts":
        rows = session.execute(select(Alert).order_by(Alert.acknowledged, Alert.created_at.desc()).limit(60)).scalars().all()
        title, desc = "Alerts", "Auffälligkeiten aus dem Nachtlauf mit Schwellenwerten."
        data = {"alerts": [_alert_row(a) for a in rows], "thresholds": anomalies.thresholds(session)}
    elif path == "/reports":
        rows = session.execute(select(Report).order_by(Report.created_at.desc()).limit(30)).scalars().all()
        title, desc = "Reports", "Liste der Wochen- und Monatsreports."
        data = {"reports": [{"id": r.id, "type": r.type, "period": f"{r.period_start}–{r.period_end}", "title": r.title, "created_at": r.created_at.isoformat(timespec="minutes"), "model": r.model} for r in rows]}
    elif m := re.fullmatch(r"/reports/(\d+)", path):
        r = session.get(Report, int(m.group(1)))
        if r:
            title, desc = r.title, f"Report ({r.type}) für {r.period_start}–{r.period_end}, erzeugt mit {r.model}. Der volle Text steht in report_md, die abgeleiteten Vorschläge darunter."
            data = {"report_md": r.content_md, "suggestions": [suggestion_dict(s) for s in session.execute(select(Suggestion).where(Suggestion.report_id == r.id).order_by(Suggestion.priority)).scalars()]}
    elif path == "/admin":
        title, desc = "Admin", "Systemstatus: Jobs, Anbindungen."
        data = {"jobs": jobs.status_summary(session)}
    elif path == "/chat":
        title, desc = "Chat", "Die große Chat-Seite; kein spezieller Seitenkontext."
        data = {"period": period, "kpis_vs_previous": kpis.compare_periods(session, days, platform, end)}
    else:
        title, desc = path, "Unbekannte Seite; nur Grundkennzahlen."
        data = {"period": period, "kpis_vs_previous": kpis.compare_periods(session, days, platform, end)}

    data_json = json_dumps(data)
    truncated = False
    if len(data_json) > MAX_CHARS:
        data_json, truncated = data_json[:MAX_CHARS] + " …", True
    return {"path": path, "title": title, "description": desc, "data_json": data_json, "truncated": truncated}


def as_prompt(ctx: dict[str, Any]) -> str:
    note = " Das JSON wurde gekürzt; für Details die Werkzeuge nutzen." if ctx.get("truncated") else ""
    return (
        f"\n\nAKTUELLE SEITE DES NUTZERS: {ctx['title']} ({ctx['path']})\n{ctx['description']}{note}\n"
        "Beziehe dich zuerst auf diese sichtbaren Daten; sie sind das, worüber der Nutzer gerade spricht. "
        "Nutze die Werkzeuge, wenn du Vergleichszeiträume, Ursachen oder Details brauchst, die hier fehlen.\n"
        f"SICHTBARE DATEN (JSON):\n{ctx['data_json']}"
    )
