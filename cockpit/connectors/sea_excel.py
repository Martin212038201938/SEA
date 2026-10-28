"""Excel „Conversions SEA“ (SharePoint) lesen: je Spalte ein Lead aus der Anzeigenwerbung mit Nettoumsatz.

Aufbau (spaltenweise ab B): Zeile 2 Datum, 6 Vorname, 8 Nachname, 10 E-Mail, 12 Unternehmen, 18 Trello-Link,
Zeile mit Beschriftung „Status“ in Spalte A = Nettoumsatz, Zeile „Erlös“ = Marge (Formel). Die Zeilen für Status und
Erlös werden über die Beschriftung in Spalte A gesucht, damit eingefügte Zeilen nichts kaputt machen.
Zugriff: Microsoft Graph, App-only (Client Credentials), Berechtigung Sites.Selected mit Leserecht auf die eine Site.
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import re
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import SeaRevenue, utcnow

log = logging.getLogger(__name__)
GRAPH = "https://graph.microsoft.com/v1.0"
FIELD_ROWS = {"date": 2, "first": 6, "last": 8, "email": 10, "company": 12, "trello": 18}


def _token() -> str:
    r = httpx.post(
        f"https://login.microsoftonline.com/{settings.graph_tenant_id}/oauth2/v2.0/token",
        data={"grant_type": "client_credentials", "client_id": settings.graph_client_id, "client_secret": settings.graph_client_secret, "scope": "https://graph.microsoft.com/.default"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _get(client: httpx.Client, url: str) -> httpx.Response:
    r = client.get(url, timeout=60, follow_redirects=True)
    r.raise_for_status()
    return r


def download() -> bytes:
    with httpx.Client(headers={"Authorization": f"Bearer {_token()}"}) as c:
        site = _get(c, f"{GRAPH}/sites/{settings.sea_excel_site}").json()
        item = f"{GRAPH}/sites/{site['id']}/drive/root:/{quote(settings.sea_excel_path)}:/content"
        return _get(c, item).content


def _strip_label(v: Any) -> str:
    s = "" if v is None else str(v).strip()
    return re.sub(r"^(vorname|nachname|e-?mail|unternehmen|telefon)\s*:\s*", "", s, flags=re.I).strip()


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if not s:
        return None
    if "," in s:  # deutsches Format 7.600,50
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):  # 7.600
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _date(v: Any) -> dt.date | None:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    s = str(v or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse(content: bytes) -> list[dict[str, Any]]:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    ws = load_workbook(io.BytesIO(content), data_only=True).worksheets[0]
    label_rows = {str(ws.cell(row=r, column=1).value or "").strip().lower(): r for r in range(1, ws.max_row + 1)}
    status_row = label_rows.get("status")
    margin_row = label_rows.get("erlös") or label_rows.get("erloes")
    out = []
    for col in range(2, ws.max_column + 1):
        email = _strip_label(ws.cell(row=FIELD_ROWS["email"], column=col).value).lower()
        if "@" not in email:
            continue
        name = " ".join(x for x in (_strip_label(ws.cell(row=FIELD_ROWS["first"], column=col).value), _strip_label(ws.cell(row=FIELD_ROWS["last"], column=col).value)) if x)
        out.append(
            {
                "column": get_column_letter(col),
                "date": _date(ws.cell(row=FIELD_ROWS["date"], column=col).value),
                "name": name,
                "email": email,
                "company": _strip_label(ws.cell(row=FIELD_ROWS["company"], column=col).value),
                "trello_url": _strip_label(ws.cell(row=FIELD_ROWS["trello"], column=col).value),
                "net_revenue": _num(ws.cell(row=status_row, column=col).value) if status_row else None,
                "margin": _num(ws.cell(row=margin_row, column=col).value) if margin_row else None,
            }
        )
    return out


def store(session: Session, rows: list[dict[str, Any]]) -> int:
    existing = {r.column: r for r in session.execute(select(SeaRevenue)).scalars()}
    seen = set()
    for row in rows:
        obj = existing.get(row["column"]) or SeaRevenue(column=row["column"])
        for k, v in row.items():
            setattr(obj, k, v)
        obj.synced_at = utcnow()
        session.add(obj)
        seen.add(row["column"])
    for col, obj in existing.items():
        if col not in seen:
            session.delete(obj)
    session.flush()
    return len(rows)


def pull(session: Session, log_fn=None) -> int:
    log_fn = log_fn or (lambda s: log.info(s))
    rows = parse(download())
    n = store(session, rows)
    log_fn(f"Excel Conversions SEA: {n} Leads, Umsatz gesamt {sum(r['net_revenue'] or 0 for r in rows):.0f} €")
    return n


def test_connection() -> str:
    rows = parse(download())
    return f"Gelesen: {len(rows)} Leads, davon {sum(1 for r in rows if r['net_revenue'])} mit Umsatz"
