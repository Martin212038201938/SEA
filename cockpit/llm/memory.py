"""Gedächtnis des Analysten: Gespräche datiert speichern, täglich zusammenfassen, bei Bedarf durchsuchen.

- Rohdaten: chat_messages (jede Frage und Antwort mit Zeitstempel, Nutzer, Thread).
- Tageszusammenfassung: chat_digests, einmal je Nutzer und Tag im Nachtlauf (Haiku), zusätzlich als Markdown-Datei
  unter data/chat-history/<nutzer>/<datum>.md – lesbar, exportierbar, unabhängig von der Datenbank.
- Im Chat (Schalter „Historie“ an): die Zusammenfassungen der letzten Wochen stehen im Systemprompt, das Werkzeug
  search_chat_history durchsucht alle Gespräche im Wortlaut. Schalter aus: nichts davon.
Das Gedächtnis ist gemeinsam für Chris und Martin; jeder Eintrag nennt, wer gesprochen hat.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..config import BASE_DIR
from ..models import ChatDigest, ChatMessage
from ..util import today_local

log = logging.getLogger(__name__)
HISTORY_DIR = BASE_DIR / "data" / "chat-history"
PROMPT_DAYS = 60
PROMPT_MAX_CHARS = 12_000

DIGEST_SYSTEM = """Du fasst ein Arbeitsgespräch zwischen einem SEA-Manager und einem Analysten (Google Ads, Microsoft
Advertising) für ein Gedächtnisprotokoll zusammen. Deutsch, sachlich, ohne Floskeln, ohne Witze.
Halte nur fest, was später wieder wichtig sein kann: Entscheidungen, Umsetzungen mit Datum, Annahmen, offene Fragen,
Erkenntnisse mit Zahl und Zeitraum, Wünsche an das Tool. Keine Wiederholung allgemeiner Erklärungen.
Format: 3 bis 10 Stichpunkte, jeder beginnt mit einer Kategorie in eckigen Klammern:
[Entscheidung] [Umgesetzt] [Erkenntnis] [Offen] [Wunsch]. Wenn nichts davon vorkam: eine Zeile „[Erkenntnis] nur Rückfragen ohne Ergebnis“."""


def _day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, dt.time.min)
    return start, start + dt.timedelta(days=1)


def _transcript(msgs: list[ChatMessage], max_chars: int = 30_000) -> str:
    parts = []
    for m in msgs:
        who = m.user if m.role == "user" else "Analyst"
        parts.append(f"[{m.created_at.strftime('%H:%M')}] {who}: {m.content.strip()}")
    text = "\n\n".join(parts)
    return text[-max_chars:]


def write_markdown(day: dt.date, user: str, msgs: list[ChatMessage], summary: str) -> Path:
    folder = HISTORY_DIR / user
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{day.isoformat()}.md"
    lines = [f"# Gespräche {user} mit dem Analysten – {day.strftime('%d.%m.%Y')}", "", "## Zusammenfassung", "", summary.strip(), "", "## Verlauf", ""]
    for m in msgs:
        who = m.user if m.role == "user" else "Analyst"
        lines += [f"**{m.created_at.strftime('%H:%M')} {who}:**", "", m.content.strip(), ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def build_digests(session: Session, until: dt.date | None = None, summarize=None) -> int:
    """Fehlende Tageszusammenfassungen bis einschließlich `until` (Standard: gestern) erzeugen."""
    from . import client as llm

    summarize = summarize or (lambda text: llm.plain(DIGEST_SYSTEM, text))
    until = until or (today_local() - dt.timedelta(days=1))
    rows = session.execute(select(func.date(ChatMessage.created_at), ChatMessage.user).where(ChatMessage.role == "user").group_by(func.date(ChatMessage.created_at), ChatMessage.user)).all()
    done = {(d.day, d.user) for d in session.execute(select(ChatDigest)).scalars()}
    n = 0
    for day_raw, user in rows:
        day = day_raw if isinstance(day_raw, dt.date) else dt.date.fromisoformat(str(day_raw))
        if day > until or (day, user) in done or not user:
            continue
        start, end = _day_bounds(day)
        threads = select(ChatMessage.thread_id).where(ChatMessage.user == user, ChatMessage.role == "user", ChatMessage.created_at >= start, ChatMessage.created_at < end).distinct()
        msgs = session.execute(select(ChatMessage).where(ChatMessage.thread_id.in_(threads), ChatMessage.created_at >= start, ChatMessage.created_at < end).order_by(ChatMessage.created_at)).scalars().all()
        if not msgs:
            continue
        try:
            summary = summarize(_transcript(msgs)).strip() or "[Erkenntnis] keine Zusammenfassung erzeugt"
        except Exception as exc:  # Zusammenfassung ist Komfort; Rohdaten bleiben ohnehin erhalten
            log.warning("Zusammenfassung %s %s fehlgeschlagen: %s", user, day, exc)
            continue
        session.add(ChatDigest(day=day, user=user, summary=summary, message_count=len(msgs)))
        try:
            write_markdown(day, user, msgs, summary)
        except OSError as exc:
            log.warning("Markdown-Export fehlgeschlagen: %s", exc)
        n += 1
    session.flush()
    return n


def prompt_block(session: Session, days: int = PROMPT_DAYS) -> str:
    since = today_local() - dt.timedelta(days=days)
    digests = session.execute(select(ChatDigest).where(ChatDigest.day >= since).order_by(ChatDigest.day.desc(), ChatDigest.user)).scalars().all()
    if not digests:
        return ""
    parts, size = [], 0
    for d in digests:  # neueste zuerst, damit beim Kürzen das Aktuelle bleibt
        block = f"### {d.day.strftime('%d.%m.%Y')} – {d.user}\n{d.summary.strip()}"
        if size + len(block) > PROMPT_MAX_CHARS:
            break
        parts.append(block)
        size += len(block)
    return (
        "\n\nGEDÄCHTNIS (Zusammenfassungen früherer Gespräche mit Chris und Martin, neueste zuerst). Nutze es, um an "
        "Entscheidungen und Umsetzungen anzuknüpfen, und nenne das Datum, wenn du dich darauf beziehst. Für den genauen "
        "Wortlaut oder ältere Gespräche: Werkzeug search_chat_history.\n" + "\n\n".join(parts)
    )


def search(session: Session, query: str, since: dt.date | None = None, until: dt.date | None = None, limit: int = 20) -> list[dict[str, Any]]:
    words = [w for w in (query or "").lower().split() if len(w) > 2][:6]
    stmt = select(ChatMessage).where(ChatMessage.role.in_(("user", "assistant")))
    for w in words:
        stmt = stmt.where(func.lower(ChatMessage.content).contains(w))
    if since:
        stmt = stmt.where(ChatMessage.created_at >= dt.datetime.combine(since, dt.time.min))
    if until:
        stmt = stmt.where(ChatMessage.created_at < dt.datetime.combine(until + dt.timedelta(days=1), dt.time.min))
    rows = session.execute(stmt.order_by(ChatMessage.created_at.desc()).limit(limit)).scalars().all()
    return [{"when": m.created_at.isoformat(timespec="minutes"), "who": m.user if m.role == "user" else "Analyst", "thread": m.thread_id, "text": m.content[:1200]} for m in rows]


def digests(session: Session, limit: int = 120) -> list[ChatDigest]:
    return session.execute(select(ChatDigest).order_by(ChatDigest.day.desc(), ChatDigest.user).limit(limit)).scalars().all()
