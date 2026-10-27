"""Chat über die Cockpit-Daten (große Chat-Seite und Seitenpanel).

Das Panel schickt bei jeder Frage die gerade geöffnete Seite mit; daraus entsteht ein Seitenkontext
(page_context), der dem Systemprompt angehängt wird – nicht der Historie, damit immer die aktuellen
Zahlen gelten. Die Werkzeuge bleiben verfügbar, um tiefer zu graben.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ChatMessage
from . import client as llm, page_context
from .prompts import CHAT_SYSTEM
from .tools import call, openai_tools

log = logging.getLogger(__name__)
HISTORY_LIMIT = 24


def history(session: Session, thread_id: str, limit: int = HISTORY_LIMIT) -> list[ChatMessage]:
    rows = session.execute(select(ChatMessage).where(ChatMessage.thread_id == thread_id).order_by(ChatMessage.created_at.desc()).limit(limit)).scalars().all()
    return list(reversed(rows))


def _is_model_missing(exc: Exception) -> bool:
    s = str(exc).lower()
    return "model" in s and ("not_found" in s or "not found" in s or "does not exist" in s)


def ask(session: Session, user: str, thread_id: str, question: str, page: dict[str, Any] | None = None) -> dict[str, Any]:
    """page = {"path": "/suchbegriffe", "query": {"days": "28", "waste": "1"}} (vom Panel) oder None (große Chat-Seite)."""
    session.add(ChatMessage(thread_id=thread_id, user=user, role="user", content=question))
    session.flush()
    msgs = [{"role": m.role, "content": m.content} for m in history(session, thread_id) if m.role in ("user", "assistant")]
    if not msgs or msgs[-1]["content"] != question:
        msgs.append({"role": "user", "content": question})
    # Anthropic verlangt abwechselnde Rollen und einen Nutzer-Start
    cleaned: list[dict[str, Any]] = []
    for m in msgs:
        if cleaned and cleaned[-1]["role"] == m["role"]:
            cleaned[-1]["content"] += "\n\n" + m["content"]
        else:
            cleaned.append(dict(m))
    while cleaned and cleaned[0]["role"] != "user":
        cleaned.pop(0)
    msgs = cleaned

    system = CHAT_SYSTEM
    context_title = None
    if page and page.get("path"):
        try:
            ctx = page_context.build(session, page["path"], page.get("query") or {})
            system += page_context.as_prompt(ctx)
            context_title = ctx["title"]
        except Exception:
            log.exception("Seitenkontext fehlgeschlagen")

    def executor(name: str, args: dict[str, Any]):
        return call(session, user, name, args)

    model = llm.model_name(chat=True)
    try:
        try:
            answer, calls = llm.run_with_tools(system, msgs, openai_tools(True), executor, model=model)
        except Exception as exc:
            if not _is_model_missing(exc) or model == llm.model_name():
                raise
            log.warning("Chat-Modell %s nicht verfügbar, Rückfall auf %s", model, llm.model_name())
            model = llm.model_name()
            answer, calls = llm.run_with_tools(system, msgs, openai_tools(True), executor, model=model)
    except llm.LLMUnavailable as exc:
        answer, calls = f"Chat nicht verfügbar: {exc}", []
    except Exception as exc:
        log.exception("Chat fehlgeschlagen")
        answer, calls = f"Fehler bei der Anfrage an das Modell: {str(exc)[:300]}", []
    session.add(ChatMessage(thread_id=thread_id, user=user, role="assistant", content=answer))
    session.flush()
    return {"answer": answer, "tool_calls": calls, "model": model, "context": context_title}
