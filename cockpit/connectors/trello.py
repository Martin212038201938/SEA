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

_FIELD = re.compile(r"^\s*(vorname|nachname|name|e-?mail|unternehmen|firma|telefon|kanal|auftragswert|wert|erlös|erloes|art|empfangsdatum)\s*:\s*(.+?)\s*$", re.I | re.M)

# Status „ignorieren“ = Liste enthält keine Leads (Ablagen, Verträge, Downloads); solche Karten werden übersprungen.
IGNORE = "ignorieren"


def _get(path: str, **params) -> Any:
    params.update({"key": settings.trello_key, "token": settings.trello_token})
    r = httpx.get(f"{API}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def parse_description(desc: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _FIELD.finditer(desc or ""):
        key = m.group(1).lower().replace("-", "")
        out[key] = m.group(2).strip()
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
        if l in ("google", "microsoft", "bing", "organisch", "empfehlung", "linkedin", "seminarmarkt", "outbound"):
            return "microsoft" if l == "bing" else l
    custom = {k.lower(): v for k, v in settings.trello_list_channel_map.items()}
    ln = (list_name or "").strip().lower()
    if ln in custom:
        return custom[ln]
    if "gclid" in (desc or "").lower() or "google ads" in (desc or "").lower():
        return "google"
    return "unbekannt"


def _kind_for(fields: dict[str, str], desc: str) -> str:
    if fields.get("art"):
        return fields["art"].lower()
    d = (desc or "").lower()
    if "kontaktformular" in d:
        return "kontaktformular"
    if "angebotsanfrage" in d:
        return "angebotsanfrage"
    if "erstgespräch" in d or "bookings" in d:
        return "erstgespraech"
    return "unbekannt"


def pull(session: Session) -> int:
    lists = {l["id"]: l["name"] for l in _get(f"/boards/{settings.trello_board_id}/lists", fields="name")}
    cards = _get(f"/boards/{settings.trello_board_id}/cards", fields="name,desc,idList,shortUrl,dateLastActivity,labels,closed", filter="all")
    n = skipped = 0
    for c in cards:
        if c.get("closed"):
            continue
        list_name = lists.get(c["idList"], "")
        status = _status_for_list(list_name)
        if status == IGNORE:
            skipped += 1
            continue
        desc = c.get("desc", "")
        fields = parse_description(desc)
        name = " ".join(x for x in (fields.get("vorname"), fields.get("nachname")) if x) or fields.get("name") or c.get("name", "")
        labels = [l.get("name", "").lower() for l in c.get("labels", [])]
        upsert(
            session,
            Lead,
            {"source": "trello", "external_id": c["id"]},
            {
                "created_on": _received_date(fields) or _card_created(c["id"]),
                "name": name,
                "company": fields.get("unternehmen") or fields.get("firma") or "",
                "email": fields.get("email", ""),
                "phone": fields.get("telefon", ""),
                "status": status,
                "value": _money(fields.get("auftragswert") or fields.get("wert")),
                "revenue": _money(fields.get("erlös") or fields.get("erloes")),
                "channel": _channel_for(fields, labels, list_name, desc),
                "kind": _kind_for(fields, desc),
                "url": c.get("shortUrl", ""),
                "notes": f"{c.get('name', '')} [Liste: {list_name}]",
            },
        )
        n += 1
    log.info("trello: %s Karten verarbeitet, %s ignoriert", n, skipped)
    return n


def test_connection() -> str:
    b = _get(f"/boards/{settings.trello_board_id}", fields="name,url")
    return f"Verbunden: Board „{b['name']}“ ({b['url']})"
