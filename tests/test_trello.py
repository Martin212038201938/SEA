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
    monkeypatch.setattr(config.settings, "trello_list_channel_map", {"Leads aus Smart Lead Kampagne": "outbound"})
    f = trello.parse_description(DESC)
    assert f["email"] == "jaspers@example.de" and f["name"] == "Pia Jaspers" and f["unternehmen"] == "take-e-way"
    assert trello._received_date(f) == dt.date(2026, 10, 15)
    assert trello._kind_for(f, DESC) == "kontaktformular"
    assert trello._status_for_list("Downloads") == trello.IGNORE
    assert trello._status_for_list("Erledigt") == "gewonnen"
    assert trello._status_for_list("Irgendwas mit Gewonnen") == "gewonnen"  # Standardzuordnung per Teilstring
    assert trello._channel_for(f, [], "Leads aus Smart Lead Kampagne", DESC) == "outbound"
    assert trello._channel_for(f, ["google"], "x", DESC) == "google"
    assert trello._channel_for({"kanal": "Bing"}, [], "x", "") == "microsoft"
    assert trello._channel_for({}, [], "x", "gclid=abc") == "google"
    assert trello._channel_for({}, [], "x", "") == "unbekannt"
