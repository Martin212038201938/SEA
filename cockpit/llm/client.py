"""Dünne Hülle um das Sprachmodell: Anthropic (Messages API) oder OpenAI (Responses API), umschaltbar per LLM_PROVIDER.

Drei Funktionen, die der Rest des Cockpits nutzt:
  run_with_tools(system, messages, tools, executor)  – Werkzeugschleife für den Chat
  structured(system, user_content, schema)            – eine Antwort nach JSON-Schema (Reports)
  plain(system, user_content)                         – kurze Textantwort (Alert-Einordnung)
`tools` kommen im OpenAI-Funktionsformat ({"type":"function","name","description","parameters"}) und werden
für Anthropic übersetzt.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from ..config import settings

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    pass


def provider() -> str:
    return (settings.llm_provider or "openai").lower()


def ready() -> bool:
    return bool(settings.anthropic_api_key) if provider() == "anthropic" else bool(settings.openai_api_key)


def model_name(cheap: bool = False) -> str:
    if provider() == "anthropic":
        return settings.anthropic_model_cheap if cheap else settings.anthropic_model
    return settings.openai_model_cheap if cheap else settings.openai_model


def _preview(result: Any, n: int = 300) -> str:
    try:
        s = json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        s = str(result)
    return s[:n]


def _run_tool(executor, name: str, args: dict[str, Any], calls: list, on_tool) -> Any:
    try:
        result = executor(name, args)
    except Exception as exc:  # Werkzeugfehler ans Modell zurückgeben statt abzubrechen
        log.exception("Werkzeug %s fehlgeschlagen", name)
        result = {"error": str(exc)[:500]}
    calls.append({"name": name, "arguments": args, "result_preview": _preview(result)})
    if on_tool:
        on_tool(name, args, result)
    return result


# ====================================================================== Anthropic

def _anthropic():
    if not settings.anthropic_api_key:
        raise LLMUnavailable("ANTHROPIC_API_KEY fehlt – Reports und Chat sind ohne Schlüssel nicht verfügbar.")
    from anthropic import Anthropic

    headers = {"anthropic-workspace-id": settings.anthropic_workspace_id} if settings.anthropic_workspace_id else None
    return Anthropic(api_key=settings.anthropic_api_key, default_headers=headers)


def _to_anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for t in tools:
        if t.get("type") == "function":
            out.append({"name": t["name"], "description": t.get("description", ""), "input_schema": t.get("parameters") or {"type": "object", "properties": {}}})
    return out


def _anthropic_text(resp) -> str:
    return "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")


def _anthropic_run_with_tools(system, messages, tools, executor, model, max_rounds, on_tool):
    client = _anthropic()
    convo: list[dict[str, Any]] = [{"role": m["role"], "content": m["content"]} for m in messages]
    calls: list[dict[str, Any]] = []
    a_tools = _to_anthropic_tools(tools)
    for _ in range(max_rounds):
        resp = client.messages.create(model=model, max_tokens=4000, system=system, messages=convo, tools=a_tools or None)
        tool_uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
        if not tool_uses or resp.stop_reason != "tool_use":
            return _anthropic_text(resp), calls
        convo.append({"role": "assistant", "content": [b.model_dump() if hasattr(b, "model_dump") else b for b in resp.content]})
        results = []
        for tu in tool_uses:
            result = _run_tool(executor, tu.name, dict(tu.input or {}), calls, on_tool)
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": json.dumps(result, ensure_ascii=False, default=str)[:60000]})
        convo.append({"role": "user", "content": results})
    return "Die Analyse wurde nach mehreren Werkzeugaufrufen abgebrochen. Bitte Frage eingrenzen.", calls


def _anthropic_structured(system, user_content, schema, name, model) -> dict[str, Any]:
    client = _anthropic()
    tool = {"name": name, "description": "Liefert das Ergebnis im vorgegebenen Format. Genau einmal aufrufen.", "input_schema": schema}
    instruction = (
        f"\n\nAntworte ausschließlich durch einen Aufruf des Werkzeugs `{name}` mit vollständig gefüllten Feldern. "
        "Kein Text außerhalb des Werkzeugaufrufs."
    )
    kwargs: dict[str, Any] = dict(model=model, max_tokens=8000, system=system + instruction, messages=[{"role": "user", "content": user_content}], tools=[tool])
    try:
        resp = client.messages.create(tool_choice={"type": "tool", "name": name}, **kwargs)
    except Exception as exc:  # manche Modelle erlauben kein erzwungenes Werkzeug
        if "tool_choice" not in str(exc):
            raise
        resp = client.messages.create(tool_choice={"type": "auto"}, **kwargs)
    for b in resp.content:
        if getattr(b, "type", "") == "tool_use" and b.name == name:
            return dict(b.input)
    # Fallback: JSON im Text
    text = _anthropic_text(resp).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    raise RuntimeError("Modell hat kein strukturiertes Ergebnis geliefert")


def _anthropic_plain(system, user_content, model) -> str:
    client = _anthropic()
    resp = client.messages.create(model=model, max_tokens=800, system=system, messages=[{"role": "user", "content": user_content}])
    return _anthropic_text(resp)


# ====================================================================== OpenAI

def _openai():
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


def _item_to_dict(item) -> dict[str, Any]:
    d = item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else dict(item)
    if d.get("type") == "function_call":
        return {"type": "function_call", "call_id": d.get("call_id"), "name": d.get("name"), "arguments": d.get("arguments", "{}")}
    if d.get("type") == "message":
        content = [{"type": "output_text", "text": c.get("text", "")} for c in d.get("content", []) or [] if c.get("type") in ("output_text", "text")]
        return {"type": "message", "role": d.get("role", "assistant"), "content": content}
    if d.get("type") == "reasoning":
        out = {"type": "reasoning", "id": d.get("id"), "summary": d.get("summary", [])}
        if d.get("encrypted_content"):
            out["encrypted_content"] = d["encrypted_content"]
        return out
    return d


def _openai_run_with_tools(system, messages, tools, executor, model, max_rounds, on_tool):
    client = _openai()
    input_items: list[dict[str, Any]] = [{"role": m["role"], "content": m["content"]} for m in messages]
    calls: list[dict[str, Any]] = []
    for _ in range(max_rounds):
        resp = client.responses.create(model=model, instructions=system, tools=tools, input=input_items, store=False, include=["reasoning.encrypted_content"])
        tool_calls = [item for item in (resp.output or []) if getattr(item, "type", "") == "function_call"]
        if not tool_calls:
            return _output_text(resp), calls
        input_items = input_items + [_item_to_dict(item) for item in resp.output]
        for tc in tool_calls:
            try:
                args = json.loads(tc.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = _run_tool(executor, tc.name, args, calls, on_tool)
            input_items.append({"type": "function_call_output", "call_id": tc.call_id, "output": json.dumps(result, ensure_ascii=False, default=str)[:60000]})
    return "Die Analyse wurde nach mehreren Werkzeugaufrufen abgebrochen. Bitte Frage eingrenzen.", calls


def _openai_structured(system, user_content, schema, name, model) -> dict[str, Any]:
    client = _openai()
    resp = client.responses.create(
        model=model,
        instructions=system,
        input=[{"role": "user", "content": user_content}],
        text={"format": {"type": "json_schema", "name": name, "schema": schema, "strict": True}},
        store=False,
    )
    return json.loads(_output_text(resp))


def _openai_plain(system, user_content, model) -> str:
    client = _openai()
    resp = client.responses.create(model=model, instructions=system, input=[{"role": "user", "content": user_content}], store=False)
    return _output_text(resp)


# ====================================================================== öffentliche Schnittstelle

def run_with_tools(
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    executor: Callable[[str, dict[str, Any]], Any],
    model: str | None = None,
    max_rounds: int = 8,
    on_tool: Callable[[str, dict[str, Any], Any], None] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Modell darf Werkzeuge aufrufen, bis es eine Antwort liefert. Rückgabe: (Antworttext, Werkzeugaufrufe)."""
    model = model or model_name()
    if provider() == "anthropic":
        return _anthropic_run_with_tools(system, messages, tools, executor, model, max_rounds, on_tool)
    return _openai_run_with_tools(system, messages, tools, executor, model, max_rounds, on_tool)


def structured(system: str, user_content: str, schema: dict[str, Any], name: str = "result", model: str | None = None) -> dict[str, Any]:
    model = model or model_name()
    if provider() == "anthropic":
        return _anthropic_structured(system, user_content, schema, name, model)
    return _openai_structured(system, user_content, schema, name, model)


def plain(system: str, user_content: str, model: str | None = None) -> str:
    model = model or model_name(cheap=True)
    if provider() == "anthropic":
        return _anthropic_plain(system, user_content, model)
    return _openai_plain(system, user_content, model)
