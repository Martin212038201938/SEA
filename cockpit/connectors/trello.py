"""Trello als Lead-Wahrheit: Karten des Lead-Boards einlesen, Status aus Listenname ableiten.

Kartenbeschreibung im Format des Kontaktformulars:
    Vorname: ... / Nachname: ... / E-Mail: ... / Unternehmen: ... / Telefon: ...
Optionale Zeilen in der Beschreibung, die das Cockpit auswertet:
    Kanal: google|microsoft|organisch|empfehlung|...   (Standard: unbekannt)
    Auftragswert: 7600       Erlös: 1520       Art: erstgespraech|kontaktformular
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Lead
from ..util import upsert

log = logging.getLogger(__name__)
API = "https://api.trello.com/1"

DEFAULT_STATUS_MAP = {
    "eingang": "neu",
    "neu": "neu",
    "neue anfragen": "neu",
    "kontaktiert": "kontaktiert",
    "erstgespräch": "kontaktiert",
    "angebot": "angebot",
    "angebot gesendet": "angebot",
    "gewonnen": "gewonnen",
    "auftrag": "gewonnen",
    "verloren": "verloren",
    "abgelehnt": "verloren",
}

_FIELD = re.compile(r"^\s*(vorname|nachname|name|e-?mail|unternehmen|firma|telefon|kanal|auftragswert|wert|erlös|erloes|art|empfangsdatum|termin|service|buchungs-?id)\s*:\s*(.+?)\s*$", re.I | re.M)

# Status „ignorieren“ = Liste enthält keine Leads (Ablagen, Verträge, Downloads); solche Karten werden übersprungen.
IGNORE = "ignorieren"


def _get(path: str, **params) -> Any:
    params.update({"key": settings.trello_key, "token": settings.trello_token})
    r = httpx.get(f"{API}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def parse_description(desc: str) -> dict[str, str]:
    """Feldzeilen „Name: Wert“ lesen; Markdown-Fett (**Name:** Wert) und Platzhalter „—“ werden toleriert."""
    out: dict[str, str] = {}
    cleaned = re.sub(r"(?m)^\s*[*_]+\s*", "", (desc or "").replace("**", "").replace("__", ""))
    for m in _FIELD.finditer(cleaned):
        key = m.group(1).lower().replace("-", "")
        val = m.group(2).strip().rstrip("*").strip()
        if val in ("—", "-", "–", ""):
            continue
        out[key] = val
    return out


def _status_for_list(list_name: str) -> str:
    custom = {k.lower(): v for k, v in settings.trello_list_status_map.items()}
    ln = (list_name or "").strip().lower()
    if ln in custom:
        return custom[ln]
    for k, v in DEFAULT_STATUS_MAP.items():
        if k in ln:
            return v
    return "neu"


def _money(v: str | None) -> float:
    if not v:
        return 0.0
    v = re.sub(r"[^\d,\.]", "", v)
    if "," in v and "." in v:
        v = v.replace(".", "").replace(",", ".")
    elif "," in v:
        v = v.replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return 0.0


def _card_created(card_id: str) -> dt.date:
    # Die ersten 8 Hex-Zeichen einer Trello-ID sind ein Unix-Zeitstempel
    return dt.datetime.utcfromtimestamp(int(card_id[:8], 16)).date()


def _received_date(fields: dict[str, str]) -> dt.date | None:
    """„Empfangsdatum: 2026-10-15 09:48 UTC“ aus Kontaktformular-Karten."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", fields.get("empfangsdatum", ""))
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _channel_for(fields: dict[str, str], labels: list[str], list_name: str, desc: str) -> str:
    explicit = fields.get("kanal", "").lower()
    if explicit:
        return "microsoft" if explicit == "bing" else explicit
    for l in labels:
        if l in ("google", "microsoft", "bing", "organisch", "empfehlung", "linkedin", "seminarmarkt"):
            return "microsoft" if l == "bing" else l
    custom = {k.lower(): v for k, v in settings.trello_list_channel_map.items()}
    ln = (list_name or "").strip().lower()
    if ln in custom:
        return custom[ln]
    if "gclid" in (desc or "").lower() or "google ads" in (desc or "").lower():
        return "google"
    return "unbekannt"


def _kind_for(fields: dict[str, str], desc: str, card_name: str = "", channel: str = "") -> str:
    """Art des Leads: Bookings-Termin, Kontaktformular, Angebotsanfrage, Antwort auf Outbound-Mail."""
    if fields.get("art"):
        return fields["art"].lower()
    if "[booking]" in (card_name or "").lower() or fields.get("buchungsid") or fields.get("service"):
        return "erstgespraech"
    d = (desc or "").lower()
    if "kontaktformular" in d:
        return "kontaktformular"
    if "angebotsanfrage" in d:
        return "angebotsanfrage"
    return "unbekannt"


STATUS_RANK = {"gewonnen": 5, "angebot": 4, "kontaktiert": 3, "wiedervorlage": 2, "verloren": 2, "neu": 1}
KIND_RANK = {"erstgespraech": 4, "angebotsanfrage": 3, "kontaktformular": 2, "unbekannt": 0}


def _candidate(card: dict[str, Any], list_name: str, status: str) -> dict[str, Any]:
    desc = card.get("desc", "")
    fields = parse_description(desc)
    name = " ".join(x for x in (fields.get("vorname"), fields.get("nachname")) if x) or fields.get("name") or card.get("name", "")
    labels = [l.get("name", "").lower() for l in card.get("labels", [])]
    channel = _channel_for(fields, labels, list_name, desc)
    return {
        "card_id": card["id"],
        "created_on": _received_date(fields) or _card_created(card["id"]),
        "name": name,
        "company": fields.get("unternehmen") or fields.get("firma") or "",
        "email": (fields.get("email") or "").strip().lower(),
        "phone": fields.get("telefon", ""),
        "status": status,
        "value": _money(fields.get("auftragswert") or fields.get("wert")),
        "revenue": _money(fields.get("erlös") or fields.get("erloes")),
        "channel": channel,
        "kind": _kind_for(fields, desc, card.get("name", ""), channel),
        "url": card.get("shortUrl", ""),
        "notes": " ".join(x for x in (card.get("name", ""), f"[Liste: {list_name}]", f"[Termin: {fields['termin']}]" if fields.get("termin") else "") if x),
    }


def merge_duplicates(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Karten derselben Person (gleiche E-Mail) zu einem Lead zusammenfassen: frühestes Datum, am weitesten
    fortgeschrittener Status, konkreteste Art, höchste Werte; die Karte mit dem höchsten Status liefert Link und Liste.
    Karten ohne E-Mail bleiben einzeln (Firmennamen sind nicht sicher zuzuordnen)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for c in cands:
        key = f"mail:{c['email']}" if c["email"] else f"card:{c['card_id']}"
        groups.setdefault(key, []).append(c)
    out: list[dict[str, Any]] = []
    for members in groups.values():
        members.sort(key=lambda c: (c["created_on"], c["card_id"]))
        first = members[0]
        lead = {**max(members, key=lambda c: (STATUS_RANK.get(c["status"], 0), c["created_on"]))}
        lead["card_id"] = first["card_id"]  # stabile Kennung: die älteste Karte
        lead["created_on"] = first["created_on"]
        lead["kind"] = max(members, key=lambda c: KIND_RANK.get(c["kind"], 1))["kind"]
        lead["channel"] = next((c["channel"] for c in members if c["channel"] != "unbekannt"), "unbekannt")
        lead["value"] = max(c["value"] for c in members)
        lead["revenue"] = max(c["revenue"] for c in members)
        lead["name"] = next((c["name"] for c in members if c["name"]), first["name"])
        lead["company"] = next((c["company"] for c in members if c["company"]), "")
        lead["phone"] = next((c["phone"] for c in members if c["phone"]), "")
        if len(members) > 1:
            lead["notes"] = f"{lead['notes']} [+{len(members) - 1} weitere Karten]"
        out.append(lead)
    return out


def pull(session: Session) -> int:
    lists = {l["id"]: l["name"] for l in _get(f"/boards/{settings.trello_board_id}/lists", fields="name")}
    cards = _get(f"/boards/{settings.trello_board_id}/cards", fields="name,desc,idList,shortUrl,dateLastActivity,labels,closed", filter="all")
    cands: list[dict[str, Any]] = []
    skipped = 0
    for c in cards:
        if c.get("closed"):
            continue
        list_name = lists.get(c["idList"], "")
        status = _status_for_list(list_name)
        if status == IGNORE:
            skipped += 1
            continue
        cands.append(_candidate(c, list_name, status))
    leads = merge_duplicates(cands)
    keep: set[str] = set()
    for lead in leads:
        card_id = lead.pop("card_id")
        keep.add(card_id)
        upsert(session, Lead, {"source": "trello", "external_id": card_id}, lead)
    # Karten, die nicht mehr geführt werden (zusammengefasst, archiviert, in ignorierte Liste verschoben), entfernen
    removed = 0
    for old in session.execute(select(Lead).where(Lead.source == "trello")).scalars().all():
        if old.external_id not in keep:
            session.delete(old)
            removed += 1
    log.info("trello: %s Karten → %s Leads (%s Doubletten zusammengefasst, %s ignoriert, %s alte Einträge entfernt)", len(cands), len(leads), len(cands) - len(leads), skipped, removed)
    return len(leads)


def test_connection() -> str:
    b = _get(f"/boards/{settings.trello_board_id}", fields="name,url")
    return f"Verbunden: Board „{b['name']}“ ({b['url']})"
