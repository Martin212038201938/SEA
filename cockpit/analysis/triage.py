"""Suchbegriffs-Triage: Entscheidungen speichern, Negativliste erzeugen, Wirkung prüfen."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import SearchTermDaily, SearchTermDecision, utcnow

DECISIONS = ("ausschliessen", "behalten", "beobachten")


def decide(session: Session, user: str, platform: str, search_term: str, decision: str, negative_match: str = "phrase", note: str = "", campaign_id: str = "") -> SearchTermDecision:
    if decision not in DECISIONS:
        raise ValueError(f"decision muss eine von {DECISIONS} sein")
    if negative_match not in ("exact", "phrase"):
        negative_match = "phrase"
    term = (search_term or "").strip()
    row = session.execute(select(SearchTermDecision).where(SearchTermDecision.platform == platform, SearchTermDecision.search_term == term)).scalar_one_or_none()
    if row is None:
        row = SearchTermDecision(platform=platform, search_term=term)
        session.add(row)
    changed = row.decision != decision or row.negative_match != negative_match
    row.decision, row.negative_match, row.note, row.decided_by, row.decided_at = decision, negative_match, note or row.note, user, utcnow()
    row.campaign_id = campaign_id or row.campaign_id or ""
    if changed:
        row.applied, row.applied_at = False, None
    session.flush()
    return row


def decisions_map(session: Session, platform: str | None = None) -> dict[tuple[str, str], SearchTermDecision]:
    stmt = select(SearchTermDecision)
    if platform and platform != "all":
        stmt = stmt.where(SearchTermDecision.platform == platform)
    return {(r.platform, r.search_term): r for r in session.execute(stmt).scalars().all()}


def row_dict(r: SearchTermDecision) -> dict[str, Any]:
    return {"id": r.id, "platform": r.platform, "search_term": r.search_term, "decision": r.decision, "negative_match": r.negative_match, "scope": r.scope, "campaign_id": r.campaign_id, "note": r.note, "decided_by": r.decided_by, "decided_at": r.decided_at.isoformat(timespec="minutes") if r.decided_at else None, "applied": r.applied, "applied_at": r.applied_at.isoformat(timespec="minutes") if r.applied_at else None}


def negative_queue(session: Session, platform: str = "all", include_applied: bool = False) -> list[dict[str, Any]]:
    stmt = select(SearchTermDecision).where(SearchTermDecision.decision == "ausschliessen").order_by(SearchTermDecision.applied, SearchTermDecision.decided_at.desc())
    if platform != "all":
        stmt = stmt.where(SearchTermDecision.platform == platform)
    if not include_applied:
        stmt = stmt.where(SearchTermDecision.applied.is_(False))
    out = []
    for r in session.execute(stmt).scalars().all():
        d = row_dict(r)
        d["formatted"] = format_negative(r.search_term, r.negative_match)
        d["still_active_since_applied"] = _still_active(session, r) if r.applied and r.applied_at else None
        out.append(d)
    return out


def format_negative(term: str, match: str) -> str:
    return f"[{term}]" if match == "exact" else f'"{term}"'


def negative_list_text(session: Session, platform: str = "all") -> str:
    """Eine Zeile je Begriff im Format, das Google Ads und Microsoft Advertising beim Einfügen verstehen."""
    return "\n".join(d["formatted"] for d in negative_queue(session, platform))


def mark_applied(session: Session, ids: list[int], applied: bool = True) -> int:
    n = 0
    for r in session.execute(select(SearchTermDecision).where(SearchTermDecision.id.in_(ids))).scalars().all():
        r.applied, r.applied_at = applied, (utcnow() if applied else None)
        n += 1
    return n


def _still_active(session: Session, r: SearchTermDecision) -> dict[str, Any] | None:
    """Liefert der Begriff nach dem Eintragen weiter Impressionen? (Phrase-Ausschluss trifft auch Varianten, geprüft wird der exakte Begriff.)"""
    since = (r.applied_at + dt.timedelta(days=1)).date()
    row = session.execute(
        select(func.sum(SearchTermDaily.impressions), func.sum(SearchTermDaily.clicks), func.sum(SearchTermDaily.cost), func.max(SearchTermDaily.date))
        .where(SearchTermDaily.platform == r.platform, SearchTermDaily.search_term == r.search_term, SearchTermDaily.date >= since)
    ).one()
    if not row[0]:
        return None
    return {"impressions": int(row[0] or 0), "clicks": int(row[1] or 0), "cost": float(row[2] or 0), "last_seen": row[3].isoformat() if row[3] else None}


def summary(session: Session) -> dict[str, int]:
    rows = session.execute(select(SearchTermDecision.decision, SearchTermDecision.applied, func.count()).group_by(SearchTermDecision.decision, SearchTermDecision.applied)).all()
    out = {"ausschliessen_offen": 0, "ausschliessen_angewendet": 0, "behalten": 0, "beobachten": 0}
    for decision, applied, n in rows:
        if decision == "ausschliessen":
            out["ausschliessen_angewendet" if applied else "ausschliessen_offen"] += n
        else:
            out[decision] = out.get(decision, 0) + n
    return out
