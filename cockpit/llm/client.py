"""Dünne Hülle um die OpenAI Responses API mit Werkzeugschleife."""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from ..config import settings

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    pass


def _client():
    if not settings.openai_api_key:
        raise LLMUnavailable("OPENAI_API_KEY fehlt – Reports und Chat sind ohne Schlüssel nicht verfügbar.")
    from openai import OpenAI

    return OpenAI(api_key=settings.openai_api_key)


def _output_text(resp) -> str:
    text = getattr(resp, "output_text", None)
    if text:
        return text
    parts = []
    for item in getattr(resp, "output", []) or []:
        if getattr(item, "type", "") == "message":
            for c in getattr(item, "content", []) or []:
                if getattr(c, "type", "") in ("output_text", "text"):
                    parts.append(getattr(c, "text", ""))
    return "".join(parts)


def run_with_tools(
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    executor: Callable[[str, dict[str, Any]], Any],
    model: str | None = None,
    max_rounds: int = 8,
    on_tool: Callable[[str, dict[str, Any], Any], None] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Responses API: Modell darf Werkzeuge aufrufen, bis es eine Antwort liefert.

    messages: [{"role": "user"|"assistant", "content": str}, ...]
    Rückgabe: (Antworttext, Liste der Werkzeugaufrufe)
    """
    client = _client()
    model = model or settings.openai_model
    input_items: list[dict[str, Any]] = [{"role": m["role"], "content": m["content"]} for m in messages]
    calls: list[dict[str, Any]] = []
    previous_id = None
    for _ in range(max_rounds):
        kwargs: dict[str, Any] = {
            "model": model,
            "instructions": system,
            "tools": tools,
            "input": input_items,
            "store": False,
            # Ohne Speicherung auf OpenAI-Seite müssen Reasoning-Elemente verschlüsselt mitgeführt werden
            "include": ["reasoning.encrypted_content"],
        }
        if previous_id:
            kwargs["previous_response_id"] = previous_id
        resp = client.responses.create(**kwargs)
        tool_calls = [item for item in (resp.output or []) if getattr(item, "type", "") == "function_call"]
        if not tool_calls:
            return _output_text(resp), calls
        # Ohne store=True gibt es keine previous_response_id-Verkettung; wir reichen Ausgabe + Ergebnisse explizit weiter.
        input_items = input_items + [_item_to_dict(item) for item in resp.output]
        for tc in tool_calls:
            try:
                args = json.loads(tc.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            try:
                result = executor(tc.name, args)
            except Exception as exc:  # Werkzeugfehler ans Modell zurückgeben statt abzubrechen
                log.exception("Werkzeug %s fehlgeschlagen", tc.name)
                result = {"error": str(exc)[:500]}
            calls.append({"name": tc.name, "arguments": args, "result_preview": _preview(result)})
            if on_tool:
                on_tool(tc.name, args, result)
            input_items.append({"type": "function_call_output", "call_id": tc.call_id, "output": json.dumps(result, ensure_ascii=False, default=str)[:60000]})
    return "Die Analyse wurde nach mehreren Werkzeugaufrufen abgebrochen. Bitte Frage eingrenzen.", calls


def _item_to_dict(item) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        d = item.model_dump(exclude_none=True)
    else:  # pragma: no cover
        d = dict(item)
    # Nur die Felder, die die API als Eingabe akzeptiert
    if d.get("type") == "function_call":
        return {"type": "function_call", "call_id": d.get("call_id"), "name": d.get("name"), "arguments": d.get("arguments", "{}")}
    if d.get("type") == "message":
        content = []
        for c in d.get("content", []) or []:
            if c.get("type") in ("output_text", "text"):
                content.append({"type": "output_text", "text": c.get("text", "")})
        return {"type": "message", "role": d.get("role", "assistant"), "content": content}
    if d.get("type") == "reasoning":
        out = {"type": "reasoning", "id": d.get("id"), "summary": d.get("summary", [])}
        if d.get("encrypted_content"):
            out["encrypted_content"] = d["encrypted_content"]
        return out
    return d


def _preview(result: Any, n: int = 300) -> str:
    try:
        s = json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        s = str(result)
    return s[:n]


def structured(system: str, user_content: str, schema: dict[str, Any], name: str = "result", model: str | None = None) -> dict[str, Any]:
    """Eine Antwort nach JSON-Schema (ohne Werkzeuge)."""
    client = _client()
    resp = client.responses.create(
        model=model or settings.openai_model,
        instructions=system,
        input=[{"role": "user", "content": user_content}],
        text={"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
        store=False,
    )
    raw = _output_text(resp)
    return json.loads(raw)


def plain(system: str, user_content: str, model: str | None = None) -> str:
    client = _client()
    resp = client.responses.create(model=model or settings.openai_model_cheap, instructions=system, input=[{"role": "user", "content": user_content}], store=False)
    return _output_text(resp)
