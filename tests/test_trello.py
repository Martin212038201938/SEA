"""Trello-Connector: Beschreibungen, Status- und Kanalzuordnung ohne Netz prüfen."""
import datetime as dt

from cockpit import config
from cockpit.connectors import trello

DESC = """E-Mail: jaspers@example.de
Name: Pia Jaspers
Telefon: +49401234567
Unternehmen: take-e-way
Empfangsdatum: 2026-10-15 09:48 UTC (Copilotenschule Kontaktformular)
"""


def test_parse_and_derive(monkeypatch):
    monkeypatch.setattr(config.settings, "trello_list_status_map", {"Leads aus Smart Lead Kampagne": "neu", "Downloads": "ignorieren", "Erledigt": "gewonnen"})
    monkeypatch.setattr(config.settings, "trello_list_channel_map", {"Leads aus Smart Lead Kampagne": "google"})
    f = trello.parse_description(DESC)
    assert f["email"] == "jaspers@example.de" and f["name"] == "Pia Jaspers" and f["unternehmen"] == "take-e-way"
    assert trello._received_date(f) == dt.date(2026, 10, 15)
    assert trello._kind_for(f, DESC) == "kontaktformular"
    assert trello._status_for_list("Downloads") == trello.IGNORE
    assert trello._status_for_list("Erledigt") == "gewonnen"
    assert trello._status_for_list("Irgendwas mit Gewonnen") == "gewonnen"  # Standardzuordnung per Teilstring
    assert trello._channel_for(f, [], "Leads aus Smart Lead Kampagne", DESC) == "google"
    assert trello._channel_for(f, ["google"], "x", DESC) == "google"
    assert trello._channel_for({"kanal": "Bing"}, [], "x", "") == "microsoft"
    assert trello._channel_for({}, [], "x", "gclid=abc") == "google"
    assert trello._channel_for({}, [], "x", "") == "unbekannt"


BOOKING = """**Name:** Frank Augustin
**Unternehmen:** —
**E-Mail:** frank@example.de
**Telefon:** 931
**Termin:** 14.10.2026 14:15 Uhr
**Service:** Erstgespräch Copilotenschule

---
*Buchungs-ID: AAMkADM0Y2Ri*
"""


def test_booking_card_and_outbound_reply():
    f = trello.parse_description(BOOKING)
    assert f["name"] == "Frank Augustin" and f["email"] == "frank@example.de" and "unternehmen" not in f
    assert f["termin"].startswith("14.10.2026") and f["buchungsid"] == "AAMkADM0Y2Ri"
    assert trello._kind_for(f, BOOKING, "[BOOKING] Frank Augustin — Stadtwerke") == "erstgespraech"
    assert trello._kind_for({}, "Sehr geehrte Damen und Herren, gern ein Erstgespräch", "Reply: Agnes") == "unbekannt"
    assert trello._kind_for({}, "Empfangsdatum: 2026-10-15 (Copilotenschule Kontaktformular)", "x") == "kontaktformular"


def test_merge_duplicates():
    import datetime as dt

    base = {"company": "", "email": "", "phone": "", "value": 0.0, "revenue": 0.0, "channel": "unbekannt", "url": "", "notes": "x"}
    cands = [
        {**base, "card_id": "b2", "created_on": dt.date(2026, 10, 6), "name": "Jörg S.", "email": "js@x.de", "status": "angebot", "kind": "unbekannt", "url": "u2", "value": 7600.0},
        {**base, "card_id": "a1", "created_on": dt.date(2026, 10, 5), "name": "Jörg Schäfers", "email": "js@x.de", "status": "neu", "kind": "kontaktformular", "url": "u1"},
        {**base, "card_id": "c3", "created_on": dt.date(2026, 10, 6), "name": "", "email": "js@x.de", "status": "neu", "kind": "erstgespraech", "channel": "google"},
        {**base, "card_id": "d4", "created_on": dt.date(2026, 10, 7), "name": "Firma ohne Mail", "status": "neu", "kind": "unbekannt"},
        {**base, "card_id": "e5", "created_on": dt.date(2026, 10, 7), "name": "Andere Firma ohne Mail", "status": "neu", "kind": "unbekannt"},
    ]
    out = {l["card_id"]: l for l in trello.merge_duplicates(cands)}
    assert set(out) == {"a1", "d4", "e5"}  # drei Karten mit gleicher Mail → ein Lead unter der ältesten Karte
    m = out["a1"]
    assert m["created_on"] == dt.date(2026, 10, 5) and m["status"] == "angebot" and m["url"] == "u2"
    assert m["kind"] == "erstgespraech" and m["channel"] == "google" and m["value"] == 7600.0 and m["name"] == "Jörg Schäfers"
    assert "+2 weitere Karten" in m["notes"]


def test_click_id_lines():
    desc = "E-Mail: a@b.de\nName: A B\nKanal: google\nKlick-ID: gclid=Cj0KCQ\nKampagne: copilot-schulung\nLandingpage: /copilot-schulung\n"
    f = trello.parse_description(desc)
    assert trello._click_id(f) == ("gclid", "Cj0KCQ")
    assert trello._channel_for(f, [], "x", desc) == "google"
    f2 = trello.parse_description("Klick-ID: msclkid=ABC\n")
    assert trello._channel_for(f2, [], "x", "") == "microsoft"
    f3 = trello.parse_description("Kanal: direkt\n")
    assert trello._channel_for(f3, [], "x", "") == "direkt"
