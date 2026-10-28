"""Zeitgesteuerte Aufgaben: nightly (Abzug + Alerts), weekly (Report), monthly (Report mit Experiment-Bilanz)."""
from __future__ import annotations

import datetime as dt
import logging
import traceback
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import notify
from .analysis import anomalies
from .config import settings
from .llm import reports as report_mod
from .llm.tools import verify_against_changes
from .models import JobRun, Suggestion, utcnow
from .util import get_setting, set_setting, today_local, yesterday

log = logging.getLogger(__name__)


class JobLogger:
    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, msg: str) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        self.lines.append(f"{stamp} {msg}")
        log.info(msg)

    def text(self) -> str:
        return "\n".join(self.lines)


def _run(session: Session, name: str, fn: Callable[[Session, JobLogger], dict]) -> JobRun:
    run = JobRun(job=name)
    session.add(run)
    session.flush()
    jl = JobLogger()
    try:
        stats = fn(session, jl) or {}
        run.status = "ok"
        run.stats = stats
    except Exception as exc:
        jl(f"FEHLER: {exc}")
        jl(traceback.format_exc()[-3000:])
        run.status = "fehler"
        session.rollback()
        session.add(run)
    finally:
        run.finished_at = utcnow()
        run.log = jl.text()
        session.flush()
    return run


# ---------------------------------------------------------------- nightly

def nightly(session: Session, days_back: int = 7) -> JobRun:
    def body(session: Session, jl: JobLogger) -> dict:
        stats: dict = {}
        end = yesterday()
        start = end - dt.timedelta(days=days_back - 1)
        if settings.demo_mode:
            jl("DEMO_MODE aktiv: kein API-Abzug.")
        else:
            if settings.google_ready:
                from .connectors import google_ads

                jl(f"Google Ads: Abzug {start} bis {end}")
                stats["google"] = google_ads.pull_all(session, start, end, jl)
                session.commit()
            else:
                jl("Google Ads nicht konfiguriert (GOOGLE_ADS_ENABLED/Zugangsdaten fehlen).")
            if settings.ms_ready:
                from .connectors import microsoft_ads

                jl(f"Microsoft Ads: Abzug {start} bis {end}")
                stats["microsoft"] = microsoft_ads.pull_all(session, start, end, jl)
                session.commit()
            else:
                jl("Microsoft Ads nicht aktiv.")
            if settings.trello_ready:
                from .connectors import trello

                stats["trello"] = trello.pull(session)
                jl(f"Trello: {stats['trello']} Karten")
                session.commit()
            else:
                jl("Trello nicht konfiguriert.")
            if settings.gsc_ready:
                from .connectors import search_console

                try:
                    stats["gsc"] = search_console.pull(session, end - dt.timedelta(days=9), end, jl)
                    session.commit()
                except Exception as exc:  # organische Daten sind Zusatz, kein Grund den Lauf abzubrechen
                    jl(f"Search Console fehlgeschlagen: {str(exc)[:200]}")
            else:
                jl("Search Console nicht konfiguriert.")

        # Gedächtnis: Gespräche der Vortage zusammenfassen
        try:
            from .llm import memory as memory_mod

            jl(f"Gedächtnis: {memory_mod.build_digests(session)} Tageszusammenfassung(en) erstellt")
            session.commit()
        except Exception as exc:
            jl(f"Gedächtnis fehlgeschlagen: {str(exc)[:200]}")

        # Umgesetzte Vorschläge erneut gegen den Änderungsverlauf prüfen (bis 7 Tage nach Umsetzung)
        checked = 0
        for s in session.execute(select(Suggestion).where(Suggestion.status == "umgesetzt")).scalars().all():
            if s.status_changed_at and (utcnow() - s.status_changed_at).days <= 7 and not (s.verification or {}).get("found"):
                s.verification = verify_against_changes(session, s, s.status_changed_at.date())
                checked += 1
        jl(f"Umsetzungs-Abgleich: {checked} Vorschläge geprüft")

        # Alerts: nur hier (nicht im Wochenreport) und mit Datenreife – ausgewertet wird vorgestern, nicht gestern
        created_total = []
        alert_day = anomalies.evaluation_day(session)
        jl(f"Alerts für {alert_day} (Datenreife {anomalies.thresholds(session)['alert_lag_days']} Tage)")
        for platform in ("google", "microsoft"):
            cands = anomalies.evaluate(session, alert_day, platform)
            created = anomalies.persist(session, alert_day, platform, cands)
            created_total.extend(created)
            jl(f"Alerts {platform}: {len(cands)} erkannt, {len(created)} neu")
        stats["alerts_new"] = len(created_total)
        if created_total:
            digest = report_mod.alert_digest(created_total)
            lines = [f"- **[{'Microsoft' if a.platform == 'microsoft' else 'Google'}]** [{a.severity}] {a.title}: {a.message}" for a in created_total]
            text = (digest + "\n\n" if digest and digest != "\n".join(lines) else "") + "\n".join(lines)
            sent = notify.broadcast(f"SEA-Cockpit: {len(created_total)} Auffälligkeit(en) am {alert_day.strftime('%d.%m.%Y')}", text, "/alerts")
            jl(f"Benachrichtigung: {sent}")
            if any(sent.values()):
                for a in created_total:
                    a.sent_at = utcnow()
        set_setting(session, "last_nightly", utcnow().isoformat(timespec="seconds"))
        return stats

    return _run(session, "nightly", body)


# ---------------------------------------------------------------- weekly / monthly

def weekly(session: Session, end: dt.date | None = None, send: bool = True) -> JobRun:
    def body(session: Session, jl: JobLogger) -> dict:
        e = end or _last_sunday()
        jl(f"Wochenreport bis {e}")
        rep = report_mod.generate(session, "weekly", e)
        jl(f"Report #{rep.id} erstellt ({rep.model}), Vorschläge: {_count_suggestions(session, rep.id)}")
        if send:
            text = rep.content_md + _suggestion_block(session, rep.id)
            sent = notify.broadcast(rep.title, text, f"/reports/{rep.id}")
            jl(f"Versand: {sent}")
            if any(sent.values()):
                rep.sent_at = utcnow()
        return {"report_id": rep.id}

    return _run(session, "weekly", body)


def monthly(session: Session, end: dt.date | None = None, send: bool = True) -> JobRun:
    def body(session: Session, jl: JobLogger) -> dict:
        e = end or (today_local().replace(day=1) - dt.timedelta(days=1))
        jl(f"Monatsreport bis {e}")
        rep = report_mod.generate(session, "monthly", e)
        jl(f"Report #{rep.id} erstellt ({rep.model})")
        if send:
            text = rep.content_md + _suggestion_block(session, rep.id)
            sent = notify.broadcast(rep.title, text, f"/reports/{rep.id}")
            jl(f"Versand: {sent}")
            if any(sent.values()):
                rep.sent_at = utcnow()
        return {"report_id": rep.id}

    return _run(session, "monthly", body)


def _last_sunday() -> dt.date:
    t = today_local()
    return t - dt.timedelta(days=t.weekday() + 1)


def _count_suggestions(session: Session, report_id: int) -> int:
    return len(session.execute(select(Suggestion.id).where(Suggestion.report_id == report_id)).all())


def _suggestion_block(session: Session, report_id: int) -> str:
    rows = session.execute(select(Suggestion).where(Suggestion.report_id == report_id).order_by(Suggestion.priority)).scalars().all()
    if not rows:
        return ""
    out = ["", "## Vorschläge", ""]
    for s in rows:
        out.append(f"**#{s.id} (Prio {s.priority}, {s.effort or 'Aufwand offen'}): {s.title}**")
        out.append(f"Warum: {s.rationale}")
        out.append(f"Erwarteter Effekt: {s.expected_effect}")
        for i, step in enumerate(s.steps or [], 1):
            out.append(f"{i}. {step}")
        out.append("")
    return "\n".join(out)


def status_summary(session: Session) -> dict:
    last = {}
    for job in ("nightly", "weekly", "monthly"):
        run = session.execute(select(JobRun).where(JobRun.job == job).order_by(JobRun.started_at.desc()).limit(1)).scalar_one_or_none()
        last[job] = {"when": run.started_at.isoformat(timespec="minutes") if run else None, "status": run.status if run else "nie"}
    return {"last": last, "last_nightly_setting": get_setting(session, "last_nightly", "")}
