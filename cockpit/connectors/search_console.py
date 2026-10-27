"""Google Search Console: organische Suchanfragen (Search Analytics API, nur lesend).

Nutzt dasselbe Dienstkonto wie Google Ads; es muss in der Search Console als Nutzer der Property eingetragen sein
(„Eingeschränkt“ reicht). Daten liegen mit 2–3 Tagen Verzug vor; der Nachtlauf holt deshalb die letzten 10 Tage neu.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy.orm import Session

from ..config import settings
from ..models import OrganicQueryDaily
from ..util import upsert

log = logging.getLogger(__name__)
API = "https://www.googleapis.com/webmasters/v3"
LAG_DAYS = 3


def _session():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(settings.google_service_account_file, scopes=["https://www.googleapis.com/auth/webmasters.readonly"])
    return AuthorizedSession(creds)


def _site_path() -> str:
    from urllib.parse import quote

    return quote(settings.gsc_site, safe="")


def query(session_http, start: dt.date, end: dt.date, dimensions: list[str], row_limit: int = 5000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start_row = 0
    while True:
        body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": dimensions, "rowLimit": row_limit, "startRow": start_row, "dataState": "final"}
        r = session_http.post(f"{API}/sites/{_site_path()}/searchAnalytics/query", json=body, timeout=60)
        r.raise_for_status()
        chunk = r.json().get("rows", [])
        rows.extend(chunk)
        if len(chunk) < row_limit:
            return rows
        start_row += row_limit


def pull(session: Session, start: dt.date, end: dt.date, log_fn=None) -> int:
    """Tageswerte je (Datum, Suchanfrage, Seite) laden."""
    log_fn = log_fn or (lambda s: log.info(s))
    http = _session()
    latest = dt.date.today() - dt.timedelta(days=LAG_DAYS)
    end = min(end, latest)
    if start > end:
        log_fn("Search Console: noch keine finalen Daten für den Zeitraum")
        return 0
    n = 0
    # In Wochenblöcken abfragen, damit die Zeilenlimits nicht greifen
    cur = start
    while cur <= end:
        block_end = min(cur + dt.timedelta(days=6), end)
        for row in query(http, cur, block_end, ["date", "query", "page"]):
            d, q, page = row["keys"]
            upsert(
                session,
                OrganicQueryDaily,
                {"date": dt.date.fromisoformat(d), "query": q[:500], "page": page[:500]},
                {"clicks": int(row.get("clicks", 0)), "impressions": int(row.get("impressions", 0)), "ctr": float(row.get("ctr", 0.0)), "position": float(row.get("position", 0.0))},
            )
            n += 1
        session.commit()
        cur = block_end + dt.timedelta(days=1)
    log_fn(f"Search Console: {n} Zeilen {start}–{end}")
    return n


def test_connection() -> str:
    http = _session()
    r = http.get(f"{API}/sites", timeout=30)
    r.raise_for_status()
    sites = {s["siteUrl"]: s["permissionLevel"] for s in r.json().get("siteEntry", [])}
    if settings.gsc_site not in sites:
        raise RuntimeError(f"Property {settings.gsc_site} nicht sichtbar; Dienstkonto in der Search Console eintragen. Sichtbar: {list(sites)}")
    return f"Verbunden: {settings.gsc_site} ({sites[settings.gsc_site]})"
