"""Auswahlblöcke aus Chat-Antworten lösen."""
from cockpit.llm import choices

ANSWER = """Der CPC ist seit dem 16.10. um 38 % gestiegen, das passt zur Budgeterhöhung bei Smart Bidding.

```auswahl
Frage: Welche Leitplanke willst du zuerst setzen?
- Ziel-CPA 150 € | Smart Bidding bleibt, bekommt aber eine Grenze
- Manueller CPC 7 € | volle Kontrolle, mehr Pflege
3. Erst Conversion-Ziele bereinigen | ändert die Gebotsgrundlage
```
"""


def test_parse_block():
    clean, cards = choices.parse(ANSWER)
    assert "```" not in clean and clean.startswith("Der CPC")
    assert len(cards) == 1 and cards[0]["question"].startswith("Welche Leitplanke")
    assert [o["label"] for o in cards[0]["options"]] == ["Ziel-CPA 150 €", "Manueller CPC 7 €", "Erst Conversion-Ziele bereinigen"]
    assert cards[0]["options"][0]["description"] == "Smart Bidding bleibt, bekommt aber eine Grenze"


def test_no_block_and_broken_block():
    assert choices.parse("Nur Text.") == ("Nur Text.", [])
    clean, cards = choices.parse("Text\n```auswahl\nFrage: nur eine Option?\n- Einzige\n```")
    assert cards == [] and "Einzige" in clean  # unbrauchbarer Block bleibt lesbar stehen
