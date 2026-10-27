"""Chat über die Cockpit-Daten (Web-Oberfläche)."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ChatMessage
from . import client as llm
from .prompts import CHAT_SYSTEM
from .tools import call, openai_tools

log = logging.getLogger(__name__)
HISTORY_LIMIT = 20


def history(session: Session, thread_id: str, limit: int = HISTORY_LIMIT) -> list[ChatMessage]:
    rows = session.execute(select(ChatMessage).where(ChatMessage.thread_id == thread_id).order_by(ChatMessage.created_at.desc()).limit(limit)).scalars().all()
    return list(reversed(rows))


def ask(session: Session, user: str, thread_id: str, question: str) -> dict[str, Any]:
    session.add(ChatMessage(thread_id=thread_id, user=user, role="user", content=question))
    session.flush()
    msgs = [{"role": m.role, "content": m.content} for m in history(session, thread_id) if m.role in ("user", "assistant")]
    if not msgs or msgs[-1]["content"] != question:
        msgs.append({"role": "user", "content": question})

    def executor(name: str, args: dict[str, Any]):
        return call(session, user, name, args)

    try:
        answer, calls = llm.run_with_tools(CHAT_SYSTEM, msgs, openai_tools(True), executor)
    except llm.LLMUnavailable as exc:
        answer, calls = f"Chat nicht verfügbar: {exc}", []
    except Exception as exc:
        log.exception("Chat fehlgeschlagen")
        answer, calls = f"Fehler bei der Anfrage an das Modell: {str(exc)[:300]}", []
    session.add(ChatMessage(thread_id=thread_id, user=user, role="assistant", content=answer))
    session.flush()
    return {"answer": answer, "tool_calls": calls}
