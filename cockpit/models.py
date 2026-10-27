"""Datenmodell: Tageswerte je Ebene, Änderungen, Leads, Experimente, Vorschläge, Alerts, Reports."""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class MetricsMixin:
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)  # EUR
    conversions: Mapped[float] = mapped_column(Float, default=0.0)
    conv_value: Mapped[float] = mapped_column(Float, default=0.0)
    all_conversions: Mapped[float] = mapped_column(Float, default=0.0)


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(16))  # google | microsoft
    external_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200), default="")
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    __table_args__ = (UniqueConstraint("platform", "external_id"),)


class CampaignDaily(Base, MetricsMixin):
    __tablename__ = "campaign_daily"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    campaign_name: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="")
    channel_type: Mapped[str] = mapped_column(String(32), default="")
    bidding_strategy: Mapped[str] = mapped_column(String(64), default="")
    budget_amount: Mapped[float] = mapped_column(Float, default=0.0)
    end_date: Mapped[str] = mapped_column(String(16), default="")
    search_impression_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    search_budget_lost_is: Mapped[float | None] = mapped_column(Float, nullable=True)
    search_rank_lost_is: Mapped[float | None] = mapped_column(Float, nullable=True)
    top_impression_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    abs_top_impression_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    __table_args__ = (UniqueConstraint("date", "platform", "campaign_id"),)


class AdGroupDaily(Base, MetricsMixin):
    __tablename__ = "adgroup_daily"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    campaign_name: Mapped[str] = mapped_column(String(255), default="")
    ad_group_id: Mapped[str] = mapped_column(String(64), index=True)
    ad_group_name: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(32), default="")
    __table_args__ = (UniqueConstraint("date", "platform", "ad_group_id"),)


class KeywordDaily(Base, MetricsMixin):
    __tablename__ = "keyword_daily"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    ad_group_id: Mapped[str] = mapped_column(String(64), index=True)
    ad_group_name: Mapped[str] = mapped_column(String(255), default="")
    criterion_id: Mapped[str] = mapped_column(String(64))
    keyword_text: Mapped[str] = mapped_column(String(255), index=True)
    match_type: Mapped[str] = mapped_column(String(16), default="")
    status: Mapped[str] = mapped_column(String(32), default="")
    serving_status: Mapped[str] = mapped_column(String(64), default="")
    quality_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (UniqueConstraint("date", "platform", "ad_group_id", "criterion_id"),)


class SearchTermDaily(Base, MetricsMixin):
    __tablename__ = "search_term_daily"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    ad_group_id: Mapped[str] = mapped_column(String(64), index=True)
    ad_group_name: Mapped[str] = mapped_column(String(255), default="")
    search_term: Mapped[str] = mapped_column(String(500), index=True)
    matched_keyword: Mapped[str] = mapped_column(String(255), default="")
    match_type: Mapped[str] = mapped_column(String(16), default="")
    status: Mapped[str] = mapped_column(String(32), default="")  # NONE/ADDED/EXCLUDED
    __table_args__ = (
        UniqueConstraint("date", "platform", "ad_group_id", "search_term", "matched_keyword", "match_type"),
    )


class OrganicQueryDaily(Base):
    """Google Search Console: organische Suchanfragen je Tag (Dimension query, optional page)."""

    __tablename__ = "organic_query_daily"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    query: Mapped[str] = mapped_column(String(500), index=True)
    page: Mapped[str] = mapped_column(String(500), default="")
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)
    position: Mapped[float] = mapped_column(Float, default=0.0)
    __table_args__ = (UniqueConstraint("date", "query", "page"),)


class AdDaily(Base, MetricsMixin):
    __tablename__ = "ad_daily"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    ad_group_id: Mapped[str] = mapped_column(String(64), index=True)
    ad_id: Mapped[str] = mapped_column(String(64), index=True)
    ad_type: Mapped[str] = mapped_column(String(48), default="")
    ad_strength: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(32), default="")
    final_url: Mapped[str] = mapped_column(String(500), default="")
    headlines: Mapped[Any] = mapped_column(JSON, default=list)
    descriptions: Mapped[Any] = mapped_column(JSON, default=list)
    __table_args__ = (UniqueConstraint("date", "platform", "ad_id"),)


class AssetDaily(Base, MetricsMixin):
    __tablename__ = "asset_daily"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    ad_group_id: Mapped[str] = mapped_column(String(64), index=True)
    asset_id: Mapped[str] = mapped_column(String(64))
    field_type: Mapped[str] = mapped_column(String(32), default="")  # HEADLINE / DESCRIPTION / SITELINK ...
    asset_text: Mapped[str] = mapped_column(String(500), default="")
    performance_label: Mapped[str] = mapped_column(String(32), default="")
    __table_args__ = (UniqueConstraint("date", "platform", "ad_group_id", "asset_id", "field_type"),)


class AuctionInsight(Base):
    __tablename__ = "auction_insights"
    id: Mapped[int] = mapped_column(primary_key=True)
    date_from: Mapped[dt.date] = mapped_column(Date, index=True)
    date_to: Mapped[dt.date] = mapped_column(Date, index=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    domain: Mapped[str] = mapped_column(String(255))
    impression_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    overlap_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    outranking_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_above_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    top_of_page_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    abs_top_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    __table_args__ = (UniqueConstraint("date_from", "date_to", "platform", "campaign_id", "domain"),)


class ConversionAction(Base):
    __tablename__ = "conversion_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    external_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255), default="")
    category: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="")
    primary_for_goal: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    counting_type: Mapped[str] = mapped_column(String(32), default="")
    conversions_30d: Mapped[float] = mapped_column(Float, default=0.0)
    all_conversions_30d: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("platform", "external_id"),)


class ChangeEvent(Base):
    __tablename__ = "change_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(16), index=True)
    external_id: Mapped[str] = mapped_column(String(255))
    change_date_time: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    user_email: Mapped[str] = mapped_column(String(255), default="")
    client_type: Mapped[str] = mapped_column(String(64), default="")
    resource_type: Mapped[str] = mapped_column(String(64), default="")
    change_type: Mapped[str] = mapped_column(String(32), default="")  # CREATE / UPDATE / REMOVE
    campaign_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    ad_group_id: Mapped[str] = mapped_column(String(64), default="")
    resource_name: Mapped[str] = mapped_column(String(500), default="")
    changed_fields: Mapped[Any] = mapped_column(JSON, default=list)
    old_values: Mapped[Any] = mapped_column(JSON, default=dict)
    new_values: Mapped[Any] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(String(500), default="")
    __table_args__ = (UniqueConstraint("platform", "external_id"),)


class Lead(Base):
    __tablename__ = "leads"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)  # trello | booking | manual
    external_id: Mapped[str] = mapped_column(String(128), default="")
    created_on: Mapped[dt.date] = mapped_column(Date, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    company: Mapped[str] = mapped_column(String(255), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="neu")  # neu/kontaktiert/angebot/gewonnen/verloren
    value: Mapped[float] = mapped_column(Float, default=0.0)  # Auftragswert netto
    revenue: Mapped[float] = mapped_column(Float, default=0.0)  # realisierter Erlös
    channel: Mapped[str] = mapped_column(String(32), default="unbekannt")  # google/microsoft/organisch/...
    kind: Mapped[str] = mapped_column(String(32), default="kontaktformular")  # kontaktformular/erstgespraech
    notes: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("source", "external_id"),)


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    platform: Mapped[str] = mapped_column(String(16), default="google")
    scope: Mapped[Any] = mapped_column(JSON, default=dict)  # {"campaign_ids": [...], "ad_group_ids": [...]}
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    baseline_days: Mapped[int] = mapped_column(Integer, default=0)  # 0 = gleiche Länge wie Testfenster
    status: Mapped[str] = mapped_column(String(16), default="laufend")  # geplant/laufend/abgeschlossen/abgebrochen
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    result_note: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[Any] = mapped_column(JSON, default=list)


class Suggestion(Base):
    __tablename__ = "suggestions"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(32), default="weekly")  # weekly/monthly/alert/chat/manual
    platform: Mapped[str] = mapped_column(String(16), default="google")
    category: Mapped[str] = mapped_column(String(48), default="")
    title: Mapped[str] = mapped_column(String(255))
    rationale: Mapped[str] = mapped_column(Text, default="")
    expected_effect: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[Any] = mapped_column(JSON, default=list)
    priority: Mapped[int] = mapped_column(Integer, default=3)  # 1 hoch .. 3 niedrig
    confidence: Mapped[str] = mapped_column(String(16), default="mittel")
    effort: Mapped[str] = mapped_column(String(32), default="")
    evidence: Mapped[Any] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="offen", index=True)  # offen/umgesetzt/verworfen/später
    status_changed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    status_by: Mapped[str] = mapped_column(String(64), default="")
    status_note: Mapped[str] = mapped_column(Text, default="")
    verification: Mapped[Any] = mapped_column(JSON, default=dict)  # Abgleich mit Änderungsverlauf
    experiment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    report_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    platform: Mapped[str] = mapped_column(String(16), default="google")
    rule: Mapped[str] = mapped_column(String(64), index=True)
    key: Mapped[str] = mapped_column(String(255), default="")
    severity: Mapped[str] = mapped_column(String(16), default="warn")  # info/warn/kritisch
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[Any] = mapped_column(JSON, default=dict)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("date", "platform", "rule", "key"),)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(16), index=True)  # weekly/monthly
    period_start: Mapped[dt.date] = mapped_column(Date)
    period_end: Mapped[dt.date] = mapped_column(Date)
    title: Mapped[str] = mapped_column(String(255))
    content_md: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[Any] = mapped_column(JSON, default=dict)
    model: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    user: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(16))  # user/assistant/tool
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job: Mapped[str] = mapped_column(String(48), index=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="läuft")  # läuft/ok/fehler
    log: Mapped[str] = mapped_column(Text, default="")
    stats: Mapped[Any] = mapped_column(JSON, default=dict)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


Index("ix_search_term_cost", SearchTermDaily.platform, SearchTermDaily.date, SearchTermDaily.cost)

STAT_TABLES = {
    "campaign": CampaignDaily,
    "ad_group": AdGroupDaily,
    "keyword": KeywordDaily,
    "search_term": SearchTermDaily,
    "ad": AdDaily,
    "asset": AssetDaily,
}
