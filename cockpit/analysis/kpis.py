"""Deterministische Kennzahlen aus den gespeicherten Tageswerten. Keine KI, nur Rechnen."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..models import (
    AdDaily,
    AdGroupDaily,
    AuctionInsight,
    CampaignDaily,
    ChangeEvent,
    KeywordDaily,
    Lead,
    OrganicQueryDaily,
    SearchTermDaily,
)
from ..util import pct_change, safe_div


def _metrics(row) -> dict[str, Any]:
    impressions = int(row.impressions or 0)
    clicks = int(row.clicks or 0)
    cost = float(row.cost or 0.0)
    conv = float(row.conversions or 0.0)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "cost": round(cost, 2),
        "conversions": round(conv, 2),
        "conv_value": round(float(row.conv_value or 0.0), 2),
        "all_conversions": round(float(row.all_conversions or 0.0), 2),
        "ctr": safe_div(clicks, impressions),
        "avg_cpc": safe_div(cost, clicks),
        "cvr": safe_div(conv, clicks),
        "cpl": safe_div(cost, conv),
    }


def _sum_cols(model):
    return [
        func.coalesce(func.sum(model.impressions), 0).label("impressions"),
        func.coalesce(func.sum(model.clicks), 0).label("clicks"),
        func.coalesce(func.sum(model.cost), 0.0).label("cost"),
        func.coalesce(func.sum(model.conversions), 0.0).label("conversions"),
        func.coalesce(func.sum(model.conv_value), 0.0).label("conv_value"),
        func.coalesce(func.sum(model.all_conversions), 0.0).label("all_conversions"),
    ]


def _scope_filter(model, platform: str | None, start: dt.date, end: dt.date, campaign_ids: list[str] | None = None):
    conds = [model.date >= start, model.date <= end]
    if platform and platform != "all":
        conds.append(model.platform == platform)
    if campaign_ids:
        conds.append(model.campaign_id.in_(campaign_ids))
    return and_(*conds)


def overview(
    session: Session,
    start: dt.date,
    end: dt.date,
    platform: str | None = "all",
    campaign_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Summen über Kampagnen-Tageswerte für einen Zeitraum."""
    stmt = select(*_sum_cols(CampaignDaily)).where(_scope_filter(CampaignDaily, platform, start, end, campaign_ids))
    row = session.execute(stmt).one()
    data = _metrics(row)
    days = (end - start).days + 1
    data.update({"start": start.isoformat(), "end": end.isoformat(), "days": days, "cost_per_day": round(data["cost"] / days, 2) if days else 0})
    # Impression Share (gewichtet mit Impressionen)
    is_stmt = select(
        func.sum(CampaignDaily.search_impression_share * CampaignDaily.impressions),
        func.sum(CampaignDaily.search_budget_lost_is * CampaignDaily.impressions),
        func.sum(CampaignDaily.search_rank_lost_is * CampaignDaily.impressions),
        func.sum(CampaignDaily.impressions),
    ).where(_scope_filter(CampaignDaily, platform, start, end, campaign_ids), CampaignDaily.search_impression_share.isnot(None))
    is_row = session.execute(is_stmt).one()
    if is_row[3]:
        data["search_impression_share"] = round(is_row[0] / is_row[3], 4) if is_row[0] is not None else None
        data["lost_is_budget"] = round(is_row[1] / is_row[3], 4) if is_row[1] is not None else None
        data["lost_is_rank"] = round(is_row[2] / is_row[3], 4) if is_row[2] is not None else None
    else:
        data["search_impression_share"] = data["lost_is_budget"] = data["lost_is_rank"] = None
    return data


def compare_periods(session: Session, days: int, platform: str = "all", end: dt.date | None = None) -> dict[str, Any]:
    end = end or (dt.date.today() - dt.timedelta(days=1))
    start = end - dt.timedelta(days=days - 1)
    prev_end = start - dt.timedelta(days=1)
    prev_start = prev_end - dt.timedelta(days=days - 1)
    cur = overview(session, start, end, platform)
    prev = overview(session, prev_start, prev_end, platform)
    deltas = {k: pct_change(cur.get(k), prev.get(k)) for k in ("impressions", "clicks", "cost", "conversions", "ctr", "avg_cpc", "cvr", "cpl")}
    return {"current": cur, "previous": prev, "deltas": deltas}


def timeseries(
    session: Session,
    start: dt.date,
    end: dt.date,
    platform: str = "all",
    granularity: str = "day",
    campaign_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    stmt = (
        select(CampaignDaily.date, *_sum_cols(CampaignDaily))
        .where(_scope_filter(CampaignDaily, platform, start, end, campaign_ids))
        .group_by(CampaignDaily.date)
        .order_by(CampaignDaily.date)
    )
    rows = session.execute(stmt).all()
    daily = [{"date": r.date.isoformat(), **_metrics(r)} for r in rows]
    if granularity == "day":
        return daily
    # Wochenaggregation (Montag als Start)
    buckets: dict[str, dict[str, float]] = {}
    for r in rows:
        key = (r.date - dt.timedelta(days=r.date.weekday())).isoformat()
        b = buckets.setdefault(key, {"impressions": 0, "clicks": 0, "cost": 0.0, "conversions": 0.0, "conv_value": 0.0, "all_conversions": 0.0})
        for k in b:
            b[k] += float(getattr(r, k) or 0)
    out = []
    for key in sorted(buckets):
        b = buckets[key]
        out.append(
            {
                "date": key,
                **b,
                "ctr": safe_div(b["clicks"], b["impressions"]),
                "avg_cpc": safe_div(b["cost"], b["clicks"]),
                "cvr": safe_div(b["conversions"], b["clicks"]),
                "cpl": safe_div(b["cost"], b["conversions"]),
            }
        )
    return out


def campaigns(session: Session, start: dt.date, end: dt.date, platform: str = "all") -> list[dict[str, Any]]:
    stmt = (
        select(
            CampaignDaily.platform,
            CampaignDaily.campaign_id,
            func.max(CampaignDaily.campaign_name).label("campaign_name"),
            func.max(CampaignDaily.status).label("status"),
            func.max(CampaignDaily.bidding_strategy).label("bidding_strategy"),
            func.max(CampaignDaily.budget_amount).label("budget_amount"),
            func.max(CampaignDaily.end_date).label("end_date"),
            *_sum_cols(CampaignDaily),
        )
        .where(_scope_filter(CampaignDaily, platform, start, end))
        .group_by(CampaignDaily.platform, CampaignDaily.campaign_id)
        .order_by(func.sum(CampaignDaily.cost).desc())
    )
    return [
        {
            "platform": r.platform,
            "campaign_id": r.campaign_id,
            "campaign_name": r.campaign_name,
            "status": r.status,
            "bidding_strategy": r.bidding_strategy,
            "budget_amount": r.budget_amount,
            "end_date": r.end_date,
            **_metrics(r),
        }
        for r in session.execute(stmt).all()
    ]


def ad_groups(session: Session, start: dt.date, end: dt.date, platform: str = "all") -> list[dict[str, Any]]:
    stmt = (
        select(
            AdGroupDaily.platform,
            AdGroupDaily.campaign_id,
            func.max(AdGroupDaily.campaign_name).label("campaign_name"),
            AdGroupDaily.ad_group_id,
            func.max(AdGroupDaily.ad_group_name).label("ad_group_name"),
            func.max(AdGroupDaily.status).label("status"),
            *_sum_cols(AdGroupDaily),
        )
        .where(_scope_filter(AdGroupDaily, platform, start, end))
        .group_by(AdGroupDaily.platform, AdGroupDaily.campaign_id, AdGroupDaily.ad_group_id)
        .order_by(func.sum(AdGroupDaily.cost).desc())
    )
    return [
        {
            "platform": r.platform,
            "campaign_id": r.campaign_id,
            "campaign_name": r.campaign_name,
            "ad_group_id": r.ad_group_id,
            "ad_group_name": r.ad_group_name,
            "status": r.status,
            **_metrics(r),
        }
        for r in session.execute(stmt).all()
    ]


def keywords(
    session: Session,
    start: dt.date,
    end: dt.date,
    platform: str = "all",
    sort: str = "cost",
    limit: int = 100,
    only_active: bool = False,
) -> list[dict[str, Any]]:
    stmt = (
        select(
            KeywordDaily.platform,
            KeywordDaily.campaign_id,
            KeywordDaily.ad_group_id,
            func.max(KeywordDaily.ad_group_name).label("ad_group_name"),
            KeywordDaily.criterion_id,
            func.max(KeywordDaily.keyword_text).label("keyword_text"),
            func.max(KeywordDaily.match_type).label("match_type"),
            func.max(KeywordDaily.status).label("status"),
            func.max(KeywordDaily.serving_status).label("serving_status"),
            func.max(KeywordDaily.quality_score).label("quality_score"),
            *_sum_cols(KeywordDaily),
        )
        .where(_scope_filter(KeywordDaily, platform, start, end))
        .group_by(KeywordDaily.platform, KeywordDaily.campaign_id, KeywordDaily.ad_group_id, KeywordDaily.criterion_id)
    )
    if only_active:
        stmt = stmt.having(func.max(KeywordDaily.status) == "ENABLED")
    order = {"cost": func.sum(KeywordDaily.cost), "clicks": func.sum(KeywordDaily.clicks), "conversions": func.sum(KeywordDaily.conversions), "impressions": func.sum(KeywordDaily.impressions)}
    stmt = stmt.order_by(order.get(sort, order["cost"]).desc()).limit(limit)
    return [
        {
            "platform": r.platform,
            "campaign_id": r.campaign_id,
            "ad_group_id": r.ad_group_id,
            "ad_group_name": r.ad_group_name,
            "criterion_id": r.criterion_id,
            "keyword_text": r.keyword_text,
            "match_type": r.match_type,
            "status": r.status,
            "serving_status": r.serving_status,
            "quality_score": r.quality_score,
            **_metrics(r),
        }
        for r in session.execute(stmt).all()
    ]


def search_terms(
    session: Session,
    start: dt.date,
    end: dt.date,
    platform: str = "all",
    min_cost: float = 0.0,
    only_without_conversions: bool = False,
    limit: int = 100,
    query: str | None = None,
) -> list[dict[str, Any]]:
    stmt = (
        select(
            SearchTermDaily.platform,
            SearchTermDaily.search_term,
            func.max(SearchTermDaily.ad_group_name).label("ad_group_name"),
            func.max(SearchTermDaily.matched_keyword).label("matched_keyword"),
            func.max(SearchTermDaily.match_type).label("match_type"),
            func.max(SearchTermDaily.status).label("status"),
            func.min(SearchTermDaily.date).label("first_seen"),
            func.max(SearchTermDaily.date).label("last_seen"),
            *_sum_cols(SearchTermDaily),
        )
        .where(_scope_filter(SearchTermDaily, platform, start, end))
        .group_by(SearchTermDaily.platform, SearchTermDaily.search_term)
    )
    if query:
        stmt = stmt.where(SearchTermDaily.search_term.ilike(f"%{query}%"))
    having = []
    if min_cost:
        having.append(func.sum(SearchTermDaily.cost) >= min_cost)
    if only_without_conversions:
        having.append(func.sum(SearchTermDaily.conversions) == 0)
    if having:
        stmt = stmt.having(and_(*having))
    stmt = stmt.order_by(func.sum(SearchTermDaily.cost).desc()).limit(limit)
    return [
        {
            "platform": r.platform,
            "search_term": r.search_term,
            "ad_group_name": r.ad_group_name,
            "matched_keyword": r.matched_keyword,
            "match_type": r.match_type,
            "status": r.status,
            "first_seen": r.first_seen.isoformat() if r.first_seen else None,
            "last_seen": r.last_seen.isoformat() if r.last_seen else None,
            **_metrics(r),
        }
        for r in session.execute(stmt).all()
    ]


def ads(session: Session, start: dt.date, end: dt.date, platform: str = "all") -> list[dict[str, Any]]:
    stmt = (
        select(
            AdDaily.platform,
            AdDaily.campaign_id,
            AdDaily.ad_group_id,
            AdDaily.ad_id,
            func.max(AdDaily.ad_type).label("ad_type"),
            func.max(AdDaily.ad_strength).label("ad_strength"),
            func.max(AdDaily.status).label("status"),
            func.max(AdDaily.final_url).label("final_url"),
            *_sum_cols(AdDaily),
        )
        .where(_scope_filter(AdDaily, platform, start, end))
        .group_by(AdDaily.platform, AdDaily.campaign_id, AdDaily.ad_group_id, AdDaily.ad_id)
        .order_by(func.sum(AdDaily.cost).desc())
    )
    out = []
    for r in session.execute(stmt).all():
        latest = session.execute(
            select(AdDaily.headlines, AdDaily.descriptions).where(AdDaily.ad_id == r.ad_id, AdDaily.platform == r.platform).order_by(AdDaily.date.desc()).limit(1)
        ).first()
        out.append(
            {
                "platform": r.platform,
                "campaign_id": r.campaign_id,
                "ad_group_id": r.ad_group_id,
                "ad_id": r.ad_id,
                "ad_type": r.ad_type,
                "ad_strength": r.ad_strength,
                "status": r.status,
                "final_url": r.final_url,
                "headlines": latest[0] if latest else [],
                "descriptions": latest[1] if latest else [],
                **_metrics(r),
            }
        )
    return out


def change_events(session: Session, start: dt.date, end: dt.date, platform: str = "all", limit: int = 200) -> list[dict[str, Any]]:
    conds = [ChangeEvent.change_date_time >= dt.datetime.combine(start, dt.time.min), ChangeEvent.change_date_time <= dt.datetime.combine(end, dt.time.max)]
    if platform != "all":
        conds.append(ChangeEvent.platform == platform)
    rows = session.execute(select(ChangeEvent).where(and_(*conds)).order_by(ChangeEvent.change_date_time.desc()).limit(limit)).scalars().all()
    return [
        {
            "id": r.id,
            "platform": r.platform,
            "when": r.change_date_time.isoformat(timespec="minutes"),
            "user": r.user_email,
            "client": r.client_type,
            "resource_type": r.resource_type,
            "change_type": r.change_type,
            "campaign_id": r.campaign_id,
            "ad_group_id": r.ad_group_id,
            "changed_fields": r.changed_fields,
            "summary": r.summary,
            "old": r.old_values,
            "new": r.new_values,
        }
        for r in rows
    ]


def auction_insights(session: Session, platform: str = "all") -> list[dict[str, Any]]:
    conds = []
    if platform != "all":
        conds.append(AuctionInsight.platform == platform)
    latest = session.execute(select(func.max(AuctionInsight.date_to)).where(*conds)).scalar()
    if not latest:
        return []
    rows = session.execute(
        select(AuctionInsight).where(AuctionInsight.date_to == latest, *conds).order_by(AuctionInsight.impression_share.desc())
    ).scalars().all()
    return [
        {
            "platform": r.platform,
            "campaign_id": r.campaign_id,
            "period": f"{r.date_from.isoformat()} bis {r.date_to.isoformat()}",
            "domain": r.domain,
            "impression_share": r.impression_share,
            "overlap_rate": r.overlap_rate,
            "outranking_share": r.outranking_share,
            "position_above_rate": r.position_above_rate,
            "top_of_page_rate": r.top_of_page_rate,
            "abs_top_rate": r.abs_top_rate,
        }
        for r in rows
    ]


def leads_summary(session: Session, start: dt.date, end: dt.date) -> dict[str, Any]:
    rows = session.execute(select(Lead).where(Lead.created_on >= start, Lead.created_on <= end).order_by(Lead.created_on.desc())).scalars().all()
    by_channel: dict[str, dict[str, float]] = {}
    for r in rows:
        b = by_channel.setdefault(r.channel or "unbekannt", {"leads": 0, "gewonnen": 0, "value": 0.0, "revenue": 0.0})
        b["leads"] += 1
        if r.status == "gewonnen":
            b["gewonnen"] += 1
        b["value"] += r.value or 0
        b["revenue"] += r.revenue or 0
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "total": len(rows),
        "by_channel": by_channel,
        "leads": [
            {
                "id": r.id,
                "date": r.created_on.isoformat(),
                "source": r.source,
                "kind": r.kind,
                "company": r.company,
                "status": r.status,
                "channel": r.channel,
                "value": r.value,
                "revenue": r.revenue,
                "url": r.url,
            }
            for r in rows
        ],
    }


def weekly_pack(session: Session, end: dt.date, platform: str = "all") -> dict[str, Any]:
    """Alles, was der Wochenreport braucht, als ein JSON-Paket."""
    start = end - dt.timedelta(days=6)
    pack = {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "compare_7d": compare_periods(session, 7, platform, end),
        "compare_28d": compare_periods(session, 28, platform, end),
        "weekly_series_12w": timeseries(session, end - dt.timedelta(days=83), end, platform, "week"),
        "campaigns": campaigns(session, start, end, platform),
        "ad_groups": ad_groups(session, start, end, platform),
        "top_keywords_28d": keywords(session, end - dt.timedelta(days=27), end, platform, "cost", 30),
        "wasted_search_terms_28d": search_terms(session, end - dt.timedelta(days=27), end, platform, min_cost=5.0, only_without_conversions=True, limit=30),
        "converting_search_terms_90d": [t for t in search_terms(session, end - dt.timedelta(days=89), end, platform, limit=200) if t["conversions"] > 0][:30],
        "ads_28d": ads(session, end - dt.timedelta(days=27), end, platform),
        "change_events_14d": change_events(session, end - dt.timedelta(days=13), end, platform, 100),
        "auction_insights": auction_insights(session, platform),
        "leads_28d": leads_summary(session, end - dt.timedelta(days=27), end),
    }
    try:
        ov = paid_organic_overlap(session, end - dt.timedelta(days=27), end, platform)
        pack["organic_28d"] = {"summary": organic_summary(session, end - dt.timedelta(days=27), end), "top_queries": organic_queries(session, end - dt.timedelta(days=27), end, 25), "kannibalisierung": ov["kannibalisierung"][:15], "organisch_nah": ov["organisch_nah"][:20], "nur_paid": ov["nur_paid"][:15]}
    except Exception:  # ohne Search-Console-Daten bleibt der Report wie bisher
        pass
    return pack


# ---------------------------------------------------------------- Organisch (Search Console)

def _norm_query(q: str) -> str:
    import re

    q = (q or "").lower().strip()
    q = re.sub(r"[^a-z0-9äöüß ]+", " ", q)
    return re.sub(r"\s+", " ", q).strip()


INTENT_WORDS = ("schulung", "training", "kurs", "workshop", "seminar", "weiterbildung", "inhouse", "firmen", "unternehmen", "mitarbeiter", "agentur", "beratung", "coaching", "einführung", "einfuehrung", "rollout", "anbieter")


def is_buying_intent(q: str) -> bool:
    """Kaufnahe Suchanfrage (Schulungs-/Dienstleistungsbedarf) statt reiner Information (Kosten, Lizenz, Anleitung)."""
    ql = (q or "").lower()
    return any(w in ql for w in INTENT_WORDS)


def organic_queries(session: Session, start: dt.date, end: dt.date, limit: int = 200, query: str | None = None, min_impressions: int = 0) -> list[dict[str, Any]]:
    """Organische Suchanfragen aus der Search Console, über Seiten und Tage aggregiert."""
    stmt = (
        select(
            OrganicQueryDaily.query,
            func.sum(OrganicQueryDaily.clicks).label("clicks"),
            func.sum(OrganicQueryDaily.impressions).label("impressions"),
            (func.sum(OrganicQueryDaily.position * OrganicQueryDaily.impressions) / func.nullif(func.sum(OrganicQueryDaily.impressions), 0)).label("position"),
            func.count(func.distinct(OrganicQueryDaily.page)).label("pages"),
            func.max(OrganicQueryDaily.page).label("top_page"),
        )
        .where(OrganicQueryDaily.date >= start, OrganicQueryDaily.date <= end)
        .group_by(OrganicQueryDaily.query)
    )
    if query:
        stmt = stmt.where(OrganicQueryDaily.query.ilike(f"%{query}%"))
    if min_impressions:
        stmt = stmt.having(func.sum(OrganicQueryDaily.impressions) >= min_impressions)
    stmt = stmt.order_by(func.sum(OrganicQueryDaily.impressions).desc()).limit(limit)
    out = []
    for r in session.execute(stmt).all():
        imp = int(r.impressions or 0)
        out.append({"query": r.query, "clicks": int(r.clicks or 0), "impressions": imp, "ctr": safe_div(int(r.clicks or 0), imp), "position": round(float(r.position or 0), 1), "pages": int(r.pages or 0), "top_page": r.top_page, "buying_intent": is_buying_intent(r.query)})
    return out


def organic_summary(session: Session, start: dt.date, end: dt.date) -> dict[str, Any]:
    r = session.execute(
        select(func.sum(OrganicQueryDaily.clicks), func.sum(OrganicQueryDaily.impressions), func.count(func.distinct(OrganicQueryDaily.query)), func.min(OrganicQueryDaily.date), func.max(OrganicQueryDaily.date)).where(OrganicQueryDaily.date >= start, OrganicQueryDaily.date <= end)
    ).one()
    clicks, imps = int(r[0] or 0), int(r[1] or 0)
    return {"clicks": clicks, "impressions": imps, "ctr": safe_div(clicks, imps), "queries": int(r[2] or 0), "first": r[3].isoformat() if r[3] else None, "last": r[4].isoformat() if r[4] else None}


def paid_organic_overlap(session: Session, start: dt.date, end: dt.date, platform: str = "all", min_impressions: int = 20) -> dict[str, Any]:
    """Bezahlte Suchbegriffe gegen organische Rankings stellen.

    kannibalisierung: organisch Top 3 und trotzdem bezahlte Klicks – Kandidaten, Gebote zu senken oder auszuschließen.
    organisch_nah:    organisch Position 4–20 mit Nachfrage, aber ohne bezahlte Abdeckung – Kandidaten für Paid-Test
                      (belegter Bedarf) oder SEO-Push.
    nur_paid:         bezahlt mit Conversions, organisch unsichtbar – Content-Lücke.
    """
    paid = {_norm_query(t["search_term"]): t for t in search_terms(session, start, end, platform, limit=2000)}
    organic = {_norm_query(o["query"]): o for o in organic_queries(session, start, end, limit=5000, min_impressions=1)}
    kann, nah, nur_paid = [], [], []
    for key, o in organic.items():
        p = paid.get(key)
        if p and o["position"] <= 3 and p["clicks"] > 0:
            kann.append({"query": o["query"], "organic_position": o["position"], "organic_clicks": o["clicks"], "organic_impressions": o["impressions"], "paid_cost": p["cost"], "paid_clicks": p["clicks"], "paid_conversions": p["conversions"]})
        elif not p and 4 <= o["position"] <= 20 and o["impressions"] >= min_impressions:
            nah.append({"query": o["query"], "organic_position": o["position"], "organic_clicks": o["clicks"], "organic_impressions": o["impressions"], "top_page": o["top_page"], "buying_intent": o["buying_intent"]})
    for key, p in paid.items():
        if key not in organic and p["conversions"] > 0:
            nur_paid.append({"search_term": p["search_term"], "paid_cost": p["cost"], "paid_clicks": p["clicks"], "paid_conversions": p["conversions"]})
    kann.sort(key=lambda x: -x["paid_cost"])
    nah.sort(key=lambda x: (not x["buying_intent"], -x["organic_impressions"]))  # kaufnahe zuerst
    nur_paid.sort(key=lambda x: -x["paid_conversions"])
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "paid_terms": len(paid),
        "organic_queries": len(organic),
        "kannibalisierung": kann[:40],
        "organisch_nah": nah[:60],
        "nur_paid": nur_paid[:40],
        "kannibalisierung_cost": round(sum(x["paid_cost"] for x in kann), 2),
    }
