"""Werkzeugschleife mit einem Fake-Client prüfen (ohne OpenAI-Schlüssel)."""
import json
from types import SimpleNamespace

from cockpit.llm import client as llm


class FakeResponses:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        assert kwargs["store"] is False
        if self.calls == 1:
            fc = SimpleNamespace(type="function_call", call_id="c1", name="get_overview", arguments=json.dumps({"days": 7}))
            fc.model_dump = lambda exclude_none=True: {"type": "function_call", "call_id": "c1", "name": "get_overview", "arguments": json.dumps({"days": 7})}
            return SimpleNamespace(output=[fc], output_text="")
        # Zweite Runde: Ergebnis der Werkzeugausgabe muss im Input stecken
        inputs = kwargs["input"]
        assert any(i.get("type") == "function_call_output" and i.get("call_id") == "c1" for i in inputs)
        return SimpleNamespace(output=[], output_text="Antwort mit Zahlen.")


def test_tool_loop(monkeypatch):
    fake = SimpleNamespace(responses=FakeResponses())
    monkeypatch.setattr(llm, "_client", lambda: fake)
    seen = []

    def executor(name, args):
        seen.append((name, args))
        return {"cost": 1.0}

    answer, calls = llm.run_with_tools("sys", [{"role": "user", "content": "Wie lief es?"}], [], executor)
    assert answer == "Antwort mit Zahlen."
    assert seen == [("get_overview", {"days": 7})]
    assert calls[0]["name"] == "get_overview"


def test_unavailable_without_key():
    import pytest

    with pytest.raises(llm.LLMUnavailable):
        llm.plain("a", "b")
