"""Werkzeugschleife und strukturierte Antwort mit Fake-Anthropic-Client prüfen – inkl. der Eigenheiten von
Sonnet/Haiku 5.5 (kein erzwungenes Werkzeug, Thinking nur per `between_tools` abschaltbar)."""
from types import SimpleNamespace

from cockpit import config
from cockpit.llm import client as llm


class Block(SimpleNamespace):
    def model_dump(self, exclude_none=False):
        return dict(self.__dict__)


class FakeMessages:
    def __init__(self, strict_model: bool):
        self.calls = 0
        self.strict_model = strict_model
        self.seen_thinking = []

    def create(self, **kwargs):
        assert kwargs["system"]
        self.seen_thinking.append(kwargs.get("thinking"))
        if self.strict_model and kwargs.get("thinking") == {"type": "disabled"}:
            raise RuntimeError('To turn thinking off on this model, send "thinking": {"type": "between_tools"}')
        if not self.strict_model and kwargs.get("thinking"):
            raise RuntimeError("thinking: Extra inputs are not permitted")
        self.calls += 1
        tc = kwargs.get("tool_choice")
        if tc:
            if self.strict_model and tc["type"] in ("tool", "any"):
                raise RuntimeError('tool_choice: type "tool" and "any" are not supported for this model.')
            name = kwargs["tools"][0]["name"]
            return SimpleNamespace(stop_reason="tool_use", content=[Block(type="text", text="Kurze Einordnung."), Block(type="tool_use", id="t9", name=name, input={"report_md": "# ok", "suggestions": []})])
        if self.calls == 1:
            return SimpleNamespace(stop_reason="tool_use", content=[Block(type="tool_use", id="t1", name="get_overview", input={"days": 7})])
        msgs = kwargs["messages"]
        assert msgs[-1]["role"] == "user" and msgs[-1]["content"][0]["type"] == "tool_result" and msgs[-1]["content"][0]["tool_use_id"] == "t1"
        return SimpleNamespace(stop_reason="end_turn", content=[Block(type="text", text="Antwort mit Zahlen.")])


def _run(monkeypatch, strict_model: bool):
    monkeypatch.setattr(config.settings, "llm_provider", "anthropic")
    monkeypatch.setattr(config.settings, "anthropic_api_key", "test")
    fake = SimpleNamespace(messages=FakeMessages(strict_model))
    monkeypatch.setattr(llm, "_anthropic", lambda: fake)
    seen = []
    answer, calls = llm.run_with_tools("sys", [{"role": "user", "content": "Wie lief es?"}], [{"type": "function", "name": "get_overview", "description": "d", "parameters": {"type": "object", "properties": {}}}], lambda n, a: seen.append((n, a)) or {"cost": 1})
    assert answer == "Antwort mit Zahlen." and seen == [("get_overview", {"days": 7})] and calls[0]["name"] == "get_overview"
    out = llm.structured("sys", "data", {"type": "object"}, name="sea_report")
    assert out["report_md"] == "# ok"
    return fake.messages


def test_anthropic_new_model(monkeypatch):
    """Sonnet 5.5: between_tools wird akzeptiert, erzwungenes Werkzeug nicht -> Fallback auf auto."""
    fm = _run(monkeypatch, strict_model=True)
    assert all(t == {"type": "between_tools"} for t in fm.seen_thinking)
    assert llm.model_name() == "claude-sonnet-5-5" and llm.model_name(cheap=True) == "claude-haiku-5-5"


def test_anthropic_legacy_model(monkeypatch):
    """Älteres Modell: kennt den thinking-Parameter nicht -> Aufruf ohne ihn, erzwungenes Werkzeug klappt."""
    fm = _run(monkeypatch, strict_model=False)
    assert None in fm.seen_thinking
