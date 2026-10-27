"""Werkzeugschleife und strukturierte Antwort mit Fake-Anthropic-Client prüfen."""
import json
from types import SimpleNamespace

from cockpit import config
from cockpit.llm import client as llm


class Block(SimpleNamespace):
    def model_dump(self):
        return dict(self.__dict__)


class FakeMessages:
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        assert kwargs["system"]
        if kwargs.get("tool_choice"):
            # strukturierte Antwort: Tool erzwungen
            return SimpleNamespace(stop_reason="tool_use", content=[Block(type="tool_use", id="t9", name=kwargs["tool_choice"]["name"], input={"report_md": "# ok", "suggestions": []})])
        if self.calls == 1:
            return SimpleNamespace(stop_reason="tool_use", content=[Block(type="tool_use", id="t1", name="get_overview", input={"days": 7})])
        msgs = kwargs["messages"]
        assert msgs[-1]["role"] == "user" and msgs[-1]["content"][0]["type"] == "tool_result" and msgs[-1]["content"][0]["tool_use_id"] == "t1"
        return SimpleNamespace(stop_reason="end_turn", content=[Block(type="text", text="Antwort mit Zahlen.")])


def test_anthropic_loop_and_structured(monkeypatch):
    monkeypatch.setattr(config.settings, "llm_provider", "anthropic")
    monkeypatch.setattr(config.settings, "anthropic_api_key", "test")
    fake = SimpleNamespace(messages=FakeMessages())
    monkeypatch.setattr(llm, "_anthropic", lambda: fake)
    seen = []
    answer, calls = llm.run_with_tools("sys", [{"role": "user", "content": "Wie lief es?"}], [{"type": "function", "name": "get_overview", "description": "d", "parameters": {"type": "object", "properties": {}}}], lambda n, a: seen.append((n, a)) or {"cost": 1})
    assert answer == "Antwort mit Zahlen." and seen == [("get_overview", {"days": 7})] and calls[0]["name"] == "get_overview"
    out = llm.structured("sys", "data", {"type": "object"}, name="sea_report")
    assert out["report_md"] == "# ok"
    assert llm.model_name() == "claude-sonnet-5-5" and llm.model_name(cheap=True) == "claude-haiku-5-5"
