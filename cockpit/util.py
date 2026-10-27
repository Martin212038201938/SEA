"""Kleine Helfer: Upserts, Einstellungen, Datumsrechnung, Formatierung."""
from __future__ import annotations

import datetime as dt
import json
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Setting

TZ = ZoneInfo(settings.timezone)


def today_local() -> dt.date:
    return dt.datetime.now(TZ).date()


def yesterday() -> dt.date:
    return today_local() - dt.timedelta(days=1)


def daterange(start: dt.date, end: dt.date) -> Iterable[dt.date]:
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def parse_date(value: str | None, default: dt.date | None = None) -> dt.date | None:
    if not value:
        return default
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return default


def upsert(session: Session, model, keys: dict[str, Any], values: dict[str, Any]):
    """Datensatz anhand der Schlüsselspalten finden und aktualisieren oder anlegen."""
    stmt = select(model).filter_by(**keys)
    obj = session.execute(stmt).scalar_one_or_none()
    if obj is None:
        obj = model(**keys, **values)
        session.add(obj)
    else:
        for k, v in values.items():
            setattr(obj, k, v)
    return obj


def get_setting(session: Session, key: str, default: str = "") -> str:
    row = session.get(Setting, key)
    return row.value if row else default


def set_setting(session: Session, key: str, value: str) -> None:
    row = session.get(Setting, key)
    if row:
        row.value = value
    else:
        session.add(Setting(key=key, value=value))


def get_setting_json(session: Session, key: str, default: Any) -> Any:
    raw = get_setting(session, key, "")
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def safe_div(a: float, b: float) -> float | None:
    return (a / b) if b else None


def eur(value: float | None) -> str:
    if value is None:
        return "–"
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "–"
    return f"{value * 100:.{digits}f} %".replace(".", ",")


def num(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "–"
    s = f"{value:,.{digits}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def pct_change(new: float | None, old: float | None) -> float | None:
    if new is None or old in (None, 0):
        return None
    return (new - old) / old


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)
