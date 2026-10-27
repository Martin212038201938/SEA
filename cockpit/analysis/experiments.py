"""Vorher/Nachher-Vergleich für Experimente mit Störfaktor-Erkennung über den Änderungsverlauf."""
from __future__ import annotations

import datetime as dt
import math
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..models import ChangeEvent, Experiment
from ..util import pct_change, today_local
from . import kpis


def wilson_interval(successes: float, trials: int, z: float = 1.96) -> tuple[float, float] | None:
    if trials <= 0:
        return None
    p = successes / trials
    denom = 1 + z * z / trials
    centre = p + z * z / (2 * trials)
    margin = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))
    return (max(0.0, (centre - margin) / denom), min(1.0, (centre + margin) / denom))


def windows(exp: Experiment, today: dt.date | None = None) -> dict[str, dt.date]:
    today = today or today_local()
    test_start = exp.start_date
    test_end = exp.end_date or (today - dt.timedelta(days=1))
    if test_end < test_start:
        test_end = test_start
    length = (test_end - test_start).days + 1
    base_len = exp.baseline_days or length
    base_end = test_start - dt.timedelta(days=1)
    base_start = base_end - dt.timedelta(days=base_len - 1)
    return {"base_start": base_start, "base_end": base_end, "test_start": test_start, "test_end": test_end}


def compare(session: Session, exp: Experiment, today: dt.date | None = None) -> dict[str, Any]:
    w = windows(exp, today)
    campaign_ids = (exp.scope or {}).get("campaign_ids") or None
    platform = exp.platform or "all"
    before = kpis.overview(session, w["base_start"], w["base_end"], platform, campaign_ids)
    after = kpis.overview(session, w["test_start"], w["test_end"], platform, campaign_ids)
    metrics = ["impressions", "clicks", "cost", "conversions", "ctr", "avg_cpc", "cvr", "cpl", "search_impression_share"]
    deltas = {m: pct_change(after.get(m), before.get(m)) for m in metrics}

    ci_before = wilson_interval(before["conversions"], before["clicks"])
    ci_after = wilson_interval(after["conversions"], after["clicks"])
    overlap = None
    if ci_before and ci_after:
        overlap = not (ci_after[0] > ci_before[1] or ci_after[1] < ci_before[0])

    # Störfaktoren: Änderungen im Testfenster, die nicht am Starttag passiert sind
    conds = [
        ChangeEvent.change_date_time >= dt.datetime.combine(w["test_start"], dt.time.min),
        ChangeEvent.change_date_time <= dt.datetime.combine(w["test_end"], dt.time.max),
    ]
    if platform != "all":
        conds.append(ChangeEvent.platform == platform)
    events = session.execute(select(ChangeEvent).where(and_(*conds)).order_by(ChangeEvent.change_date_time)).scalars().all()
    confounders = [
        {
            "when": e.change_date_time.isoformat(timespec="minutes"),
            "user": e.user_email,
            "resource_type": e.resource_type,
            "change_type": e.change_type,
            "summary": e.summary or ", ".join(e.changed_fields or []),
        }
        for e in events
        if e.change_date_time.date() != w["test_start"]
    ]

    verdict = _verdict(before, after, ci_before, ci_after, overlap, confounders)
    return {
        "experiment": {
            "id": exp.id,
            "name": exp.name,
            "hypothesis": exp.hypothesis,
            "status": exp.status,
            "platform": exp.platform,
            "scope": exp.scope,
            "tags": exp.tags,
        },
        "windows": {k: v.isoformat() for k, v in w.items()},
        "before": before,
        "after": after,
        "deltas": deltas,
        "cvr_ci_before": ci_before,
        "cvr_ci_after": ci_after,
        "cvr_ci_overlap": overlap,
        "confounders": confounders,
        "verdict": verdict,
    }


def _verdict(before, after, ci_b, ci_a, overlap, confounders) -> str:
    parts = []
    total_conv = before["conversions"] + after["conversions"]
    if total_conv < 10:
        parts.append(f"Nur {total_conv:.0f} Conversions in beiden Fenstern zusammen: Unterschiede in CVR und CPL sind statistisch nicht belastbar, nur Richtung ablesbar.")
    if after["cpl"] is not None and before["cpl"] is not None:
        d = (after["cpl"] - before["cpl"]) / before["cpl"]
        parts.append(f"CPL {'gesunken' if d < 0 else 'gestiegen'} um {abs(d) * 100:.0f} %.")
    elif after["cpl"] is None and after["clicks"]:
        parts.append("Im Testfenster keine Conversions erfasst.")
    if overlap is False:
        parts.append("Die 95-%-Intervalle der Conversion-Rate überlappen nicht, der Unterschied ist auffällig.")
    elif overlap is True:
        parts.append("Die 95-%-Intervalle der Conversion-Rate überlappen, der Unterschied kann Zufall sein.")
    if confounders:
        parts.append(f"{len(confounders)} weitere Änderung(en) im Testfenster, die den Effekt verfälschen können.")
    if not parts:
        parts.append("Zu wenig Daten für eine Aussage.")
    return " ".join(parts)
