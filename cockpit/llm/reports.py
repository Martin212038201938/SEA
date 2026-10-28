"""Wochen- und Monatsreport: Datenpaket bauen, Modell schreiben lassen, Vorschläge speichern."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analysis import experiments as exp_mod
from ..analysis import kpis
from ..config import settings
from ..models import Alert, Experiment, Report, Suggestion
from ..util import json_dumps
from . import client as llm
from .prompts import REPORT_SYSTEM, SUGGESTION_SCHEMA
from .tools import KNOWLEDGE_DIR, business_context, suggestion_dict

log = logging.getLogger(__name__)


def _knowledge_digest(max_chars: int = 12000) -> str:
    if not KNOWLEDGE_DIR.exists():
        return ""
    chunks = []
    budget = max_chars
    for f in sorted(KNOWLEDGE_DIR.glob("*.md")):
        txt = f.read_text(encoding="utf-8", errors="ignore").strip()
        take = txt[: max(0, min(len(txt), budget))]
        if not take:
            break
        chunks.append(f"### {f.name}\n{take}")
        budget -= len(take)
    return "\n\n".join(chunks)


def build_pack(session: Session, kind: str, end: dt.date, platform: str = "all") -> dict[str, Any]:
    if kind == "weekly":
        pack = kpis.weekly_pack(session, end, platform)
    else:
        start = end.replace(day=1)
        prev_end = start - dt.timedelta(days=1)
        prev_start = prev_end.replace(day=1)
        pack = {
            "period": {"start": start.isoformat(), "end": end.isoformat()},
            "month": kpis.overview(session, start, end, platform),
            "previous_month": kpis.overview(session, prev_start, prev_end, platform),
            "weekly_series_26w": kpis.timeseries(session, end - dt.timedelta(days=181), end, platform, "week"),
            "campaigns": kpis.campaigns(session, start, end, platform),
            "ad_groups": kpis.ad_groups(session, start, end, platform),
            "top_keywords": kpis.keywords(session, start, end, platform, "cost", 40),
            "wasted_search_terms": kpis.search_terms(session, start, end, platform, min_cost=5.0, only_without_conversions=True, limit=40),
            "converting_search_terms": [t for t in kpis.search_terms(session, start - dt.timedelta(days=60), end, platform, limit=300) if t["conversions"] > 0][:40],
            "ads": kpis.ads(session, start, end, platform),
            "change_events": kpis.change_events(session, start, end, platform, 200),
            "auction_insights": kpis.auction_insights(session, platform),
            "leads": kpis.leads_summary(session, start, end),
        }
    # Experimente mit Vergleich
    exps = session.execute(select(Experiment).where(Experiment.status.in_(("laufend", "abgeschlossen"))).order_by(Experiment.start_date.desc()).limit(8)).scalars().all()
    pack["experiments"] = [exp_mod.compare(session, e, end + dt.timedelta(days=1)) for e in exps]
    # Vorschläge
    open_s = session.execute(select(Suggestion).where(Suggestion.status == "offen").order_by(Suggestion.priority).limit(30)).scalars().all()
    done_s = session.execute(select(Suggestion).where(Suggestion.status == "umgesetzt").order_by(Suggestion.status_changed_at.desc()).limit(15)).scalars().all()
    pack["open_suggestions"] = [{"id": s.id, "title": s.title, "category": s.category, "priority": s.priority, "created_at": s.created_at.isoformat()} for s in open_s]
    pack["implemented_suggestions"] = [{**suggestion_dict(s)} for s in done_s]
    rejected = session.execute(select(Suggestion).where(Suggestion.status.in_(("verworfen", "geloescht"))).order_by(Suggestion.status_changed_at.desc()).limit(40)).scalars().all()
    pack["rejected_suggestions"] = [{"title": s.title, "category": s.category, "reason": s.status_note, "when": s.status_changed_at.isoformat() if s.status_changed_at else None} for s in rejected]
    # Alerts der Periode
    since = dt.date.fromisoformat(pack["period"]["start"])
    alerts = session.execute(select(Alert).where(Alert.date >= since, Alert.date <= end).order_by(Alert.created_at.desc()).limit(40)).scalars().all()
    pack["alerts"] = [{"date": a.date.isoformat(), "rule": a.rule, "severity": a.severity, "title": a.title} for a in alerts]
    pack["business"] = business_context()
    return pack


def generate(session: Session, kind: str = "weekly", end: dt.date | None = None, platform: str = "all") -> Report:
    end = end or (dt.date.today() - dt.timedelta(days=1))
    pack = build_pack(session, kind, end, platform)
    label = "Wochenreport" if kind == "weekly" else "Monatsreport"
    title = f"{label} {pack['period']['start']} bis {pack['period']['end']}"
    system = REPORT_SYSTEM.replace("{kind}", label)
    user_content = "WISSENSBASIS (Auszug):\n" + _knowledge_digest() + "\n\nDATENPAKET (JSON):\n" + json_dumps(pack)
    try:
        result = llm.structured(system, user_content, SUGGESTION_SCHEMA, name="sea_report")
        model = llm.model_name()
        content = result.get("report_md", "")
        suggestions = result.get("suggestions", [])
    except llm.LLMUnavailable as exc:
        model = "ohne-modell"
        content = _fallback_report(pack, str(exc))
        suggestions = []
    report = Report(type=kind, period_start=dt.date.fromisoformat(pack["period"]["start"]), period_end=end, title=title, content_md=content, data=_slim(pack), model=model)
    session.add(report)
    session.flush()
    for s in suggestions:
        session.add(
            Suggestion(
                source=kind,
                platform=platform if platform != "all" else "google",
                category=s.get("category", ""),
                title=s.get("title", "")[:255],
                rationale=s.get("rationale", ""),
                expected_effect=s.get("expected_effect", ""),
                steps=s.get("steps", []),
                priority=int(s.get("priority") or 2),
                confidence=s.get("confidence", "mittel"),
                effort=s.get("effort", ""),
                evidence={"text": s.get("evidence", ""), "campaign_ids": s.get("campaign_ids", [])},
                report_id=report.id,
            )
        )
    session.flush()
    return report


def _slim(pack: dict[str, Any]) -> dict[str, Any]:
    """Nur die kompakten Teile im Report speichern (Nachvollziehbarkeit, ohne die DB aufzublähen)."""
    keep = {k: v for k, v in pack.items() if k in ("period", "compare_7d", "compare_28d", "month", "previous_month", "campaigns", "alerts", "open_suggestions", "leads_28d", "leads")}
    keep["experiments"] = [{"name": e["experiment"]["name"], "verdict": e["verdict"], "deltas": e["deltas"]} for e in pack.get("experiments", [])]
    return keep


def _fallback_report(pack: dict[str, Any], reason: str) -> str:
    cur = pack.get("compare_7d", {}).get("current") or pack.get("month") or {}
    lines = [
        f"_Automatischer Kurzreport ohne Sprachmodell ({reason})_",
        "",
        "## Zahlen",
        f"- Zeitraum: {pack['period']['start']} bis {pack['period']['end']}",
        f"- Kosten: {cur.get('cost', 0):.2f} €, Klicks: {cur.get('clicks', 0)}, Impressionen: {cur.get('impressions', 0)}",
        f"- Conversions: {cur.get('conversions', 0):.1f}, CPL: {cur.get('cpl') or '–'}",
        "",
        "## Auffälligkeiten",
    ]
    for a in pack.get("alerts", [])[:10]:
        lines.append(f"- {a['date']}: {a['title']}")
    return "\n".join(lines)


def alert_digest(alerts: list[Alert]) -> str:
    """Kurze KI-Einordnung für die Alert-Nachricht; ohne Schlüssel nur die Rohliste."""
    if not alerts:
        return ""
    raw = "\n".join(f"- [{a.severity}] {a.title}: {a.message}" for a in alerts)
    try:
        from .prompts import ALERT_SYSTEM

        return llm.plain(ALERT_SYSTEM, raw)
    except llm.LLMUnavailable:
        return raw
    except Exception as exc:  # pragma: no cover
        log.warning("Alert-Einordnung fehlgeschlagen: %s", exc)
        return raw
