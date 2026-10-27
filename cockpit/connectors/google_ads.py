"""Google Ads API: nur lesende GAQL-Abfragen, Ergebnis in die Tagestabellen.

Benötigt das Paket google-ads sowie OAuth-Zugangsdaten eines Google-Kontos mit Zugriff auf das
Werbekonto (yellowboatschulung@gmail.com). Developer-Tokens sind seit 09.09.2026 ohne Funktion;
die Zugriffsstufe hängt am Google-Cloud-Projekt (mindestens „Explorer“ für echte Konten).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any, Callable, Iterable

from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Account,
    AdDaily,
    AdGroupDaily,
    AssetDaily,
    AuctionInsight,
    CampaignDaily,
    ChangeEvent,
    ConversionAction,
    KeywordDaily,
    SearchTermDaily,
)
from ..util import upsert

log = logging.getLogger(__name__)
PLATFORM = "google"


def _client():
    try:
        from google.ads.googleads.client import GoogleAdsClient
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Paket google-ads fehlt: pip install google-ads") from exc
    cfg = {"developer_token": settings.google_developer_token, "use_proto_plus": True}
    if settings.google_service_account_file:
        # Dienstkonto: in Google Ads als Nutzer eingetragen, kein Refresh-Token nötig
        cfg["json_key_file_path"] = settings.google_service_account_file
    else:
        cfg.update(
            {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "refresh_token": settings.google_refresh_token,
            }
        )
    if settings.google_login_customer_id:
        cfg["login_customer_id"] = settings.google_login_customer_id
    return GoogleAdsClient.load_from_dict(cfg)


def _micros(v) -> float:
    return round((v or 0) / 1_000_000, 4)


def _enum_name(v) -> str:
    if v is None:
        return ""
    name = getattr(v, "name", None)
    return name if name else str(v)


def _rows(client, query: str, customer_id: str | None = None) -> Iterable[Any]:
    service = client.get_service("GoogleAdsService")
    stream = service.search_stream(customer_id=customer_id or settings.google_customer_id, query=query)
    for batch in stream:
        for row in batch.results:
            yield row


def _between(start: dt.date, end: dt.date) -> str:
    return f"segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"


def _common_metrics(m) -> dict[str, Any]:
    return {
        "impressions": int(m.impressions),
        "clicks": int(m.clicks),
        "cost": _micros(m.cost_micros),
        "conversions": float(m.conversions),
        "conv_value": float(m.conversions_value),
        "all_conversions": float(m.all_conversions),
    }


# ---------------------------------------------------------------- Abfragen

def pull_campaigns(session: Session, client, start: dt.date, end: dt.date) -> int:
    q = f"""
        SELECT segments.date, campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type,
               campaign.bidding_strategy_type, campaign.end_date_time, campaign_budget.amount_micros,
               metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions,
               metrics.conversions_value, metrics.all_conversions,
               metrics.search_impression_share, metrics.search_budget_lost_impression_share,
               metrics.search_rank_lost_impression_share, metrics.top_impression_percentage,
               metrics.absolute_top_impression_percentage
        FROM campaign
        WHERE {_between(start, end)} AND campaign.status != 'REMOVED'
    """
    n = 0
    for r in _rows(client, q):
        m = r.metrics
        upsert(
            session,
            CampaignDaily,
            {"date": dt.date.fromisoformat(r.segments.date), "platform": PLATFORM, "campaign_id": str(r.campaign.id)},
            {
                "campaign_name": r.campaign.name,
                "status": _enum_name(r.campaign.status),
                "channel_type": _enum_name(r.campaign.advertising_channel_type),
                "bidding_strategy": _enum_name(r.campaign.bidding_strategy_type),
                "budget_amount": _micros(r.campaign_budget.amount_micros),
                "end_date": (r.campaign.end_date_time or "")[:10],
                "search_impression_share": _share(m.search_impression_share),
                "search_budget_lost_is": _share(m.search_budget_lost_impression_share),
                "search_rank_lost_is": _share(m.search_rank_lost_impression_share),
                "top_impression_pct": _share(m.top_impression_percentage),
                "abs_top_impression_pct": _share(m.absolute_top_impression_percentage),
                **_common_metrics(m),
            },
        )
        n += 1
    return n


def _share(v) -> float | None:
    """Google liefert 0.0999 für „< 10 %“ und 0 für „nicht verfügbar“."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def pull_ad_groups(session: Session, client, start: dt.date, end: dt.date) -> int:
    q = f"""
        SELECT segments.date, campaign.id, campaign.name, ad_group.id, ad_group.name, ad_group.status,
               metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions,
               metrics.conversions_value, metrics.all_conversions
        FROM ad_group
        WHERE {_between(start, end)} AND ad_group.status != 'REMOVED'
    """
    n = 0
    for r in _rows(client, q):
        upsert(
            session,
            AdGroupDaily,
            {"date": dt.date.fromisoformat(r.segments.date), "platform": PLATFORM, "ad_group_id": str(r.ad_group.id)},
            {
                "campaign_id": str(r.campaign.id),
                "campaign_name": r.campaign.name,
                "ad_group_name": r.ad_group.name,
                "status": _enum_name(r.ad_group.status),
                **_common_metrics(r.metrics),
            },
        )
        n += 1
    return n


def pull_keywords(session: Session, client, start: dt.date, end: dt.date) -> int:
    q = f"""
        SELECT segments.date, campaign.id, ad_group.id, ad_group.name,
               ad_group_criterion.criterion_id, ad_group_criterion.keyword.text,
               ad_group_criterion.keyword.match_type, ad_group_criterion.status,
               ad_group_criterion.system_serving_status, ad_group_criterion.quality_info.quality_score,
               metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions,
               metrics.conversions_value, metrics.all_conversions
        FROM keyword_view
        WHERE {_between(start, end)}
    """
    n = 0
    for r in _rows(client, q):
        c = r.ad_group_criterion
        qs = int(c.quality_info.quality_score) if c.quality_info.quality_score else None
        upsert(
            session,
            KeywordDaily,
            {
                "date": dt.date.fromisoformat(r.segments.date),
                "platform": PLATFORM,
                "ad_group_id": str(r.ad_group.id),
                "criterion_id": str(c.criterion_id),
            },
            {
                "campaign_id": str(r.campaign.id),
                "ad_group_name": r.ad_group.name,
                "keyword_text": c.keyword.text,
                "match_type": _enum_name(c.keyword.match_type),
                "status": _enum_name(c.status),
                "serving_status": _enum_name(c.system_serving_status),
                "quality_score": qs,
                **_common_metrics(r.metrics),
            },
        )
        n += 1
    return n


def pull_search_terms(session: Session, client, start: dt.date, end: dt.date) -> int:
    q = f"""
        SELECT segments.date, campaign.id, ad_group.id, ad_group.name,
               search_term_view.search_term, search_term_view.status,
               segments.keyword.info.text, segments.keyword.info.match_type,
               metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions,
               metrics.conversions_value, metrics.all_conversions
        FROM search_term_view
        WHERE {_between(start, end)}
    """
    n = 0
    for r in _rows(client, q):
        upsert(
            session,
            SearchTermDaily,
            {
                "date": dt.date.fromisoformat(r.segments.date),
                "platform": PLATFORM,
                "ad_group_id": str(r.ad_group.id),
                "search_term": r.search_term_view.search_term,
                "matched_keyword": r.segments.keyword.info.text or "",
                "match_type": _enum_name(r.segments.keyword.info.match_type),
            },
            {
                "campaign_id": str(r.campaign.id),
                "ad_group_name": r.ad_group.name,
                "status": _enum_name(r.search_term_view.status),
                **_common_metrics(r.metrics),
            },
        )
        n += 1
    return n


def pull_ads(session: Session, client, start: dt.date, end: dt.date) -> int:
    q = f"""
        SELECT segments.date, campaign.id, ad_group.id, ad_group_ad.ad.id, ad_group_ad.ad.type,
               ad_group_ad.ad_strength, ad_group_ad.status, ad_group_ad.ad.final_urls,
               ad_group_ad.ad.responsive_search_ad.headlines, ad_group_ad.ad.responsive_search_ad.descriptions,
               metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions,
               metrics.conversions_value, metrics.all_conversions
        FROM ad_group_ad
        WHERE {_between(start, end)} AND ad_group_ad.status != 'REMOVED'
    """
    n = 0
    for r in _rows(client, q):
        ad = r.ad_group_ad.ad
        rsa = ad.responsive_search_ad
        upsert(
            session,
            AdDaily,
            {"date": dt.date.fromisoformat(r.segments.date), "platform": PLATFORM, "ad_id": str(ad.id)},
            {
                "campaign_id": str(r.campaign.id),
                "ad_group_id": str(r.ad_group.id),
                "ad_type": _enum_name(ad.type_),
                "ad_strength": _enum_name(r.ad_group_ad.ad_strength),
                "status": _enum_name(r.ad_group_ad.status),
                "final_url": (list(ad.final_urls) or [""])[0],
                "headlines": [h.text for h in rsa.headlines],
                "descriptions": [d.text for d in rsa.descriptions],
                **_common_metrics(r.metrics),
            },
        )
        n += 1
    return n


def pull_assets(session: Session, client, start: dt.date, end: dt.date) -> int:
    q = f"""
        SELECT segments.date, campaign.id, ad_group.id, asset.id, asset.text_asset.text,
               ad_group_ad_asset_view.field_type, ad_group_ad_asset_view.performance_label,
               metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
        FROM ad_group_ad_asset_view
        WHERE {_between(start, end)}
    """
    n = 0
    for r in _rows(client, q):
        m = r.metrics
        upsert(
            session,
            AssetDaily,
            {
                "date": dt.date.fromisoformat(r.segments.date),
                "platform": PLATFORM,
                "ad_group_id": str(r.ad_group.id),
                "asset_id": str(r.asset.id),
                "field_type": _enum_name(r.ad_group_ad_asset_view.field_type),
            },
            {
                "campaign_id": str(r.campaign.id),
                "asset_text": r.asset.text_asset.text,
                "performance_label": _enum_name(r.ad_group_ad_asset_view.performance_label),
                "impressions": int(m.impressions),
                "clicks": int(m.clicks),
                "cost": _micros(m.cost_micros),
                "conversions": float(m.conversions),
            },
        )
        n += 1
    return n


def pull_change_events(session: Session, client, days: int = 14) -> int:
    """Änderungsverlauf (max. 30 Tage rückwirkend, LIMIT ist Pflicht)."""
    days = min(days, 29)
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    q = f"""
        SELECT change_event.resource_name, change_event.change_date_time, change_event.user_email,
               change_event.client_type, change_event.change_resource_type, change_event.resource_change_operation,
               change_event.changed_fields, change_event.old_resource, change_event.new_resource,
               change_event.campaign, change_event.ad_group
        FROM change_event
        WHERE change_event.change_date_time >= '{start.isoformat()}' AND change_event.change_date_time <= '{end.isoformat()} 23:59:59'
        ORDER BY change_event.change_date_time DESC
        LIMIT 2000
    """
    n = 0
    for r in _rows(client, q):
        ev = r.change_event
        fields = list(ev.changed_fields.paths) if ev.changed_fields else []
        old_vals, new_vals = _diff(ev, fields)
        when = _parse_dt(ev.change_date_time)
        rtype = _enum_name(ev.change_resource_type)
        ctype = _enum_name(ev.resource_change_operation)
        summary = _summarize(rtype, ctype, fields, old_vals, new_vals)
        upsert(
            session,
            ChangeEvent,
            {"platform": PLATFORM, "external_id": ev.resource_name},
            {
                "change_date_time": when,
                "user_email": ev.user_email,
                "client_type": _enum_name(ev.client_type),
                "resource_type": rtype,
                "change_type": ctype,
                "campaign_id": (ev.campaign or "").split("/")[-1],
                "ad_group_id": (ev.ad_group or "").split("/")[-1],
                "resource_name": ev.resource_name,
                "changed_fields": fields,
                "old_values": old_vals,
                "new_values": new_vals,
                "summary": summary,
            },
        )
        n += 1
    return n


def _parse_dt(s: str) -> dt.datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return dt.datetime.utcnow()


def _resource_to_dict(wrapper) -> dict[str, Any]:
    """old_resource/new_resource sind oneof-Wrapper; das gesetzte Feld als JSON lesen."""
    try:
        import proto  # type: ignore

        raw = json.loads(proto.Message.to_json(wrapper, including_default_value_fields=False))
        # Wrapper enthält genau einen Schlüssel (z. B. "campaign")
        if isinstance(raw, dict) and len(raw) == 1:
            return next(iter(raw.values())) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:  # pragma: no cover
        return {}


def _dig(d: dict, path: str):
    cur: Any = d
    for part in path.split("."):
        # JSON ist camelCase, GAQL-Pfade snake_case
        camel = "".join(p.capitalize() if i else p for i, p in enumerate(part.split("_")))
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, dict) and camel in cur:
            cur = cur[camel]
        else:
            return None
    return cur


def _diff(ev, fields: list[str]) -> tuple[dict, dict]:
    old = _resource_to_dict(ev.old_resource)
    new = _resource_to_dict(ev.new_resource)
    old_vals, new_vals = {}, {}
    for f in fields[:25]:
        old_vals[f] = _dig(old, f)
        new_vals[f] = _dig(new, f)
    if not fields:
        new_vals = {k: new[k] for k in list(new)[:10]}
    return old_vals, new_vals


def _summarize(rtype: str, ctype: str, fields: list[str], old: dict, new: dict) -> str:
    verb = {"CREATE": "angelegt", "UPDATE": "geändert", "REMOVE": "entfernt"}.get(ctype, ctype.lower())
    if ctype == "UPDATE" and fields:
        parts = []
        for f in fields[:4]:
            o, n = old.get(f), new.get(f)
            parts.append(f"{f}: {o} → {n}" if o is not None or n is not None else f)
        return f"{rtype} {verb}: " + "; ".join(parts)
    return f"{rtype} {verb}"


def pull_auction_insights(session: Session, client, start: dt.date, end: dt.date) -> int:
    q = f"""
        SELECT campaign.id, segments.auction_insight_domain,
               metrics.auction_insight_search_impression_share, metrics.auction_insight_search_overlap_rate,
               metrics.auction_insight_search_outranking_share, metrics.auction_insight_search_position_above_rate,
               metrics.auction_insight_search_top_impression_percentage,
               metrics.auction_insight_search_absolute_top_impression_percentage
        FROM campaign
        WHERE {_between(start, end)} AND campaign.status != 'REMOVED'
    """
    n = 0
    try:
        rows = list(_rows(client, q))
    except Exception as exc:  # Auktionsdaten sind nicht in jedem Konto verfügbar
        log.warning("Auktionsdaten nicht abrufbar: %s", exc)
        return 0
    for r in rows:
        m = r.metrics
        upsert(
            session,
            AuctionInsight,
            {"date_from": start, "date_to": end, "platform": PLATFORM, "campaign_id": str(r.campaign.id), "domain": r.segments.auction_insight_domain or "(Sie)"},
            {
                "impression_share": _share(m.auction_insight_search_impression_share),
                "overlap_rate": _share(m.auction_insight_search_overlap_rate),
                "outranking_share": _share(m.auction_insight_search_outranking_share),
                "position_above_rate": _share(m.auction_insight_search_position_above_rate),
                "top_of_page_rate": _share(m.auction_insight_search_top_impression_percentage),
                "abs_top_rate": _share(m.auction_insight_search_absolute_top_impression_percentage),
            },
        )
        n += 1
    return n


def pull_conversion_actions(session: Session, client) -> int:
    q = """
        SELECT conversion_action.id, conversion_action.name, conversion_action.category, conversion_action.status,
               conversion_action.primary_for_goal, conversion_action.counting_type,
               metrics.all_conversions, metrics.all_conversions_value
        FROM conversion_action
        WHERE segments.date DURING LAST_30_DAYS
    """
    n = 0
    for r in _rows(client, q):
        ca = r.conversion_action
        upsert(
            session,
            ConversionAction,
            {"platform": PLATFORM, "external_id": str(ca.id)},
            {
                "name": ca.name,
                "category": _enum_name(ca.category),
                "status": _enum_name(ca.status),
                "primary_for_goal": bool(ca.primary_for_goal),
                "counting_type": _enum_name(ca.counting_type),
                "conversions_30d": float(r.metrics.all_conversions),
                "all_conversions_30d": float(r.metrics.all_conversions),
            },
        )
        n += 1
    return n


def pull_account(session: Session, client) -> None:
    q = "SELECT customer.id, customer.descriptive_name, customer.currency_code FROM customer LIMIT 1"
    for r in _rows(client, q):
        upsert(
            session,
            Account,
            {"platform": PLATFORM, "external_id": str(r.customer.id)},
            {"name": r.customer.descriptive_name, "currency": r.customer.currency_code},
        )


def pull_all(session: Session, start: dt.date, end: dt.date, log_fn: Callable[[str], None] | None = None) -> dict[str, int]:
    """Kompletter Abzug für einen Zeitraum. Conversions reifen nach, deshalb ruft der Nightly-Job
    die letzten 7 Tage immer erneut ab (Upsert)."""
    log_fn = log_fn or (lambda s: log.info(s))
    client = _client()
    stats: dict[str, int] = {}
    pull_account(session, client)
    for name, fn in (
        ("campaigns", pull_campaigns),
        ("ad_groups", pull_ad_groups),
        ("keywords", pull_keywords),
        ("search_terms", pull_search_terms),
        ("ads", pull_ads),
        ("assets", pull_assets),
    ):
        stats[name] = fn(session, client, start, end)
        log_fn(f"google {name}: {stats[name]} Zeilen")
        session.flush()
    for name, fn in (("change_events", lambda: pull_change_events(session, client, 14)), ("conversion_actions", lambda: pull_conversion_actions(session, client))):
        try:
            stats[name] = fn()
        except Exception as exc:  # einzelner Teilabzug darf den Lauf nicht abbrechen
            log.warning("google %s fehlgeschlagen: %s", name, exc)
            log_fn(f"google {name}: FEHLER {str(exc)[:200]}")
            stats[name] = -1
    # Auktionsdaten wöchentlich (letzte 7 Tage, endend am Zeitraumende)
    stats["auction_insights"] = pull_auction_insights(session, client, end - dt.timedelta(days=6), end)
    log_fn(f"google change_events: {stats['change_events']}, conversion_actions: {stats['conversion_actions']}, auction: {stats['auction_insights']}")
    return stats


def test_connection() -> str:
    client = _client()
    for r in _rows(client, "SELECT customer.id, customer.descriptive_name, customer.currency_code, customer.time_zone FROM customer LIMIT 1"):
        return f"Verbunden: {r.customer.descriptive_name} ({r.customer.id}), {r.customer.currency_code}, {r.customer.time_zone}"
    return "Keine Antwort"
