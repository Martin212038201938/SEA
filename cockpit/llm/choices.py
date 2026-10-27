"""Auswahlkarten im Chat: Das Modell markiert Fragen mit mehreren möglichen Antworten oder Vorschläge mit
Alternativen in einem festen Block; die Oberfläche macht daraus klickbare Optionen (Stil AskUserQuestion).

Format im Antworttext:

```auswahl
Frage: Welche Variante willst du zuerst testen?
- Ziel-CPA 150 € | Smart Bidding bleibt, bekommt aber eine Leitplanke
- Manueller CPC 7 € | volle Kontrolle, mehr Pflegeaufwand
- Erst Conversion-Ziele bereinigen | ändert die Gebotsgrundlage, bevor wir an Gebote gehen
```

Mehrere Blöcke pro Antwort sind erlaubt. „Label | Erklärung“, die Erklärung ist optional.
"""
from __future__ import annotations

import re
from typing import Any

_BLOCK = re.compile(r"```(?:auswahl|optionen|choices)\s*\n(.*?)```", re.S | re.I)
_OPTION = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+?)\s*$")

PROMPT_RULES = """
AUSWAHLKARTEN: Wenn du eine Frage stellst, auf die es wenige klar benennbare Antworten gibt, oder wenn du Alternativen
vorschlägst, zwischen denen der Nutzer wählen soll, schreibe sie NICHT als Fließtext, sondern als Block:
```auswahl
Frage: <eine konkrete Frage>
- <Option, 2–6 Wörter> | <ein Satz, was dafür spricht oder was es bedeutet>
- <Option> | <Erklärung>
```
2 bis 4 Optionen, die sich gegenseitig ausschließen; keine Option „Sonstiges“ (freie Antworten kann der Nutzer immer
tippen). Der Block steht am Ende deiner Antwort, davor dein normaler Text mit der Einordnung. Höchstens zwei Blöcke
pro Antwort. Wenn es nichts auszuwählen gibt, keinen Block.
"""


def parse(answer: str) -> tuple[str, list[dict[str, Any]]]:
    """Auswahlblöcke aus der Antwort lösen. Rückgabe: (Antwort ohne Blöcke, Liste von Karten)."""
    cards: list[dict[str, Any]] = []

    def _take(m: re.Match) -> str:
        question = ""
        options: list[dict[str, str]] = []
        for line in m.group(1).splitlines():
            if not line.strip():
                continue
            low = line.strip().lower()
            if low.startswith(("frage:", "question:")) and not question:
                question = line.split(":", 1)[1].strip()
                continue
            om = _OPTION.match(line)
            if om:
                label, _, desc = om.group(1).partition("|")
                label = label.strip().strip("*_").strip()
                if label:
                    options.append({"label": label[:80], "description": desc.strip()[:200]})
            elif not question:
                question = line.strip()
        if len(options) >= 2:
            cards.append({"question": question, "options": options[:6]})
            return ""
        return m.group(0)  # unbrauchbarer Block bleibt als Text stehen

    cleaned = _BLOCK.sub(_take, answer or "").rstrip()
    return cleaned, cards
