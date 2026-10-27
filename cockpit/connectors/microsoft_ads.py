"""Microsoft Advertising (Bing) Reporting API über das Paket bingads (SDK v13).

Nur lesend. Abruf von Tagesberichten für Kampagne, Anzeigengruppe, Keyword, Suchbegriff und Anzeige.
Voraussetzungen: Developer-Token (universell, Super-Admin des Kontos beantragt ihn unter
ads.microsoft.com → Einstellungen → Entwicklereinstellungen), registrierte OAuth-App (Client-ID,
optional Secret) und ein Refresh-Token des Nutzers (python -m cockpit.cli ms-auth).

Hinweis: Das Konto der Copilotenschule ist aktuell pausiert; dieser Connector ist gegen die
SDK-Schnittstelle geschrieben, aber noch nicht gegen das Live-Konto gelaufen.
"""
from __future__ import annotations

import csv
import datetime as dt
import logging
import tempfile
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Account, AdDaily, AdGroupDaily, CampaignDaily, KeywordDaily, SearchTermDaily
from ..util import upsert

log = logging.getLogger(__name__)
PLATFORM = "microsoft"
ENVIRONMENT = "production"
NATIVE_REDIRECT = "https://login.microsoftonline.com/common/oauth2/nativeclient"


def redirect_uri() -> str:
    """Rückleitung nach der Microsoft-Anmeldung: ins Cockpit (/oauth/microsoft/callback), wenn eine Basis-URL gesetzt ist."""
    return f"{settings.base_url}/oauth/microsoft/callback" if settings.base_url.startswith("https://") else NATIVE_REDIRECT


def _grant(client_id: str | None = None, client_secret: str | None = None):
    from bingads.authorization import OAuthWebAuthCodeGrant

    return OAuthWebAuthCodeGrant(
        client_id=client_id or settings.ms_client_id,
        client_secret=(client_secret if client_secret is not None else settings.ms_client_secret) or None,
        redirection_uri=redirect_uri(),
        env=ENVIRONMENT,
        tenant=settings.ms_tenant or "common",
    )


def _sdk():
    try:
        from bingads.authorization import AuthorizationData, OAuthWebAuthCodeGrant  # noqa: F401
        from bingads.v13.reporting import ReportingServiceManager, ReportingDownloadParameters  # noqa: F401
        from bingads.service_client import ServiceClient  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Paket bingads fehlt: pip install bingads") from exc
    import bingads

    return bingads


def _authorization():
    from bingads.authorization import AuthorizationData

    oauth = _grant()
    oauth.request_oauth_tokens_by_refresh_token(settings.ms_refresh_token)
    return AuthorizationData(
        account_id=settings.ms_account_id,
        customer_id=settings.ms_customer_id,
        developer_token=settings.ms_developer_token,
        authentication=oauth,
    )


def auth_url(client_id: str | None = None, state: str = "sea-cockpit") -> str:
    """URL für die einmalige Nutzer-Zustimmung (Admin → „Microsoft Advertising verbinden“ oder cli ms-auth)."""
    oauth = _grant(client_id, "")
    oauth.state = state
    return oauth.get_authorization_endpoint()


def refresh_token_from_redirect(client_id: str | None, client_secret: str | None, response_uri: str) -> str:
    oauth = _grant(client_id, client_secret)
    oauth.request_oauth_tokens_by_response_uri(response_uri)
    return oauth.oauth_tokens.refresh_token


def _download_report(report_request) -> list[dict[str, str]]:
    from bingads.v13.reporting import ReportingDownloadParameters, ReportingServiceManager

    manager = ReportingServiceManager(authorization_data=_authorization(), poll_interval_in_milliseconds=5000, environment=ENVIRONMENT)
    tmpdir = Path(tempfile.mkdtemp(prefix="msads-"))
    params = ReportingDownloadParameters(
        report_request=report_request,
        result_file_directory=str(tmpdir),
        result_file_name="report.csv",
        overwrite_result_file=True,
        timeout_in_milliseconds=300_000,
    )
    path = manager.download_file(params)
    if not path:
        return []
    rows: list[dict[str, str]] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        # Microsoft-CSV hat Kopfzeilen vor der eigentlichen Tabelle; erste Zeile mit "TimePeriod" suchen
        lines = fh.read().splitlines()
    header_idx = next((i for i, l in enumerate(lines) if "TimePeriod" in l), None)
    if header_idx is None:
        return []
    reader = csv.DictReader(lines[header_idx:])
    for r in reader:
        if not r.get("TimePeriod") or r["TimePeriod"].startswith("©"):
            continue
        rows.append(r)
    return rows


def _request(manager_client, kind: str, start: dt.date, end: dt.date, columns: list[str]):
    """Report-Request-Objekt nach Art bauen."""
    factory = manager_client.factory
    req = factory.create(f"{kind}ReportRequest")
    req.Format = "Csv"
    req.ReportName = f"sea-cockpit {kind}"
    req.ReturnOnlyCompleteData = False
    req.Aggregation = "Daily"
    scope = factory.create("AccountThroughCampaignReportScope")
    scope.AccountIds = {"long": [int(settings.ms_account_id)]}
    scope.Campaigns = None
    req.Scope = scope
    time = factory.create("ReportTime")
    time.CustomDateRangeStart = factory.create("Date")
    time.CustomDateRangeStart.Day, time.CustomDateRangeStart.Month, time.CustomDateRangeStart.Year = start.day, start.month, start.year
    time.CustomDateRangeEnd = factory.create("Date")
    time.CustomDateRangeEnd.Day, time.CustomDateRangeEnd.Month, time.CustomDateRangeEnd.Year = end.day, end.month, end.year
    time.PredefinedTime = None
    time.ReportTimeZone = "BrusselsCopenhagenMadridParis"
    req.Time = time
    cols = factory.create(f"ArrayOf{kind}ReportColumn")
    setattr(cols, f"{kind}ReportColumn", columns)
    req.Columns = cols
    return req


def _f(v: str | None) -> float:
    try:
        return float((v or "0").replace(",", "."))
    except ValueError:
        return 0.0


def _i(v: str | None) -> int:
    try:
        return int(float(v or 0))
    except ValueError:
        return 0


def _d(v: str) -> dt.date:
    # Microsoft liefert z. B. 2026-10-01 oder 10/1/2026
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unbekanntes Datum: {v}")


def pull_all(session: Session, start: dt.date, end: dt.date, log_fn: Callable[[str], None] | None = None) -> dict[str, int]:
    log_fn = log_fn or (lambda s: log.info(s))
    _sdk()
    from bingads.service_client import ServiceClient

    auth = _authorization()
    client = ServiceClient(service="ReportingService", version=13, authorization_data=auth, environment=ENVIRONMENT)
    upsert(session, Account, {"platform": PLATFORM, "external_id": str(settings.ms_account_id)}, {"name": "Microsoft Advertising", "currency": "EUR"})
    stats: dict[str, int] = {}

    base_cols = ["TimePeriod", "Impressions", "Clicks", "Spend", "Conversions", "Revenue", "AllConversions"]

    rows = _download_report(_request(client, "CampaignPerformance", start, end, ["CampaignId", "CampaignName", "CampaignStatus", "BidStrategyType", "ImpressionSharePercent", "ImpressionLostToBudgetPercent", "ImpressionLostToRankAggPercent", *base_cols]))
    for r in rows:
        upsert(
            session,
            CampaignDaily,
            {"date": _d(r["TimePeriod"]), "platform": PLATFORM, "campaign_id": r["CampaignId"]},
            {
                "campaign_name": r.get("CampaignName", ""),
                "status": r.get("CampaignStatus", ""),
                "channel_type": "SEARCH",
                "bidding_strategy": r.get("BidStrategyType", ""),
                "search_impression_share": _pct(r.get("ImpressionSharePercent")),
                "search_budget_lost_is": _pct(r.get("ImpressionLostToBudgetPercent")),
                "search_rank_lost_is": _pct(r.get("ImpressionLostToRankAggPercent")),
                **_metrics(r),
            },
        )
    stats["campaigns"] = len(rows)
    log_fn(f"microsoft campaigns: {len(rows)}")

    rows = _download_report(_request(client, "AdGroupPerformance", start, end, ["CampaignId", "CampaignName", "AdGroupId", "AdGroupName", "Status", *base_cols]))
    for r in rows:
        upsert(
            session,
            AdGroupDaily,
            {"date": _d(r["TimePeriod"]), "platform": PLATFORM, "ad_group_id": r["AdGroupId"]},
            {"campaign_id": r["CampaignId"], "campaign_name": r.get("CampaignName", ""), "ad_group_name": r.get("AdGroupName", ""), "status": r.get("Status", ""), **_metrics(r)},
        )
    stats["ad_groups"] = len(rows)

    rows = _download_report(_request(client, "KeywordPerformance", start, end, ["CampaignId", "AdGroupId", "AdGroupName", "KeywordId", "Keyword", "BidMatchType", "KeywordStatus", "QualityScore", *base_cols]))
    for r in rows:
        upsert(
            session,
            KeywordDaily,
            {"date": _d(r["TimePeriod"]), "platform": PLATFORM, "ad_group_id": r["AdGroupId"], "criterion_id": r["KeywordId"]},
            {
                "campaign_id": r["CampaignId"],
                "ad_group_name": r.get("AdGroupName", ""),
                "keyword_text": r.get("Keyword", ""),
                "match_type": (r.get("BidMatchType", "") or "").upper(),
                "status": r.get("KeywordStatus", ""),
                "quality_score": _i(r.get("QualityScore")) or None,
                **_metrics(r),
            },
        )
    stats["keywords"] = len(rows)

    rows = _download_report(_request(client, "SearchQueryPerformance", start, end, ["CampaignId", "AdGroupId", "AdGroupName", "SearchQuery", "Keyword", "BidMatchType", *base_cols]))
    for r in rows:
        upsert(
            session,
            SearchTermDaily,
            {
                "date": _d(r["TimePeriod"]),
                "platform": PLATFORM,
                "ad_group_id": r["AdGroupId"],
                "search_term": r.get("SearchQuery", ""),
                "matched_keyword": r.get("Keyword", ""),
                "match_type": (r.get("BidMatchType", "") or "").upper(),
            },
            {"campaign_id": r["CampaignId"], "ad_group_name": r.get("AdGroupName", ""), "status": "", **_metrics(r)},
        )
    stats["search_terms"] = len(rows)

    rows = _download_report(_request(client, "AdPerformance", start, end, ["CampaignId", "AdGroupId", "AdId", "AdType", "AdStatus", "FinalUrl", "TitlePart1", "TitlePart2", "TitlePart3", "AdDescription", *base_cols]))
    for r in rows:
        upsert(
            session,
            AdDaily,
            {"date": _d(r["TimePeriod"]), "platform": PLATFORM, "ad_id": r["AdId"]},
            {
                "campaign_id": r["CampaignId"],
                "ad_group_id": r["AdGroupId"],
                "ad_type": r.get("AdType", ""),
                "status": r.get("AdStatus", ""),
                "final_url": r.get("FinalUrl", ""),
                "headlines": [h for h in (r.get("TitlePart1"), r.get("TitlePart2"), r.get("TitlePart3")) if h],
                "descriptions": [r.get("AdDescription")] if r.get("AdDescription") else [],
                **_metrics(r),
            },
        )
    stats["ads"] = len(rows)
    log_fn(f"microsoft ad_groups: {stats['ad_groups']}, keywords: {stats['keywords']}, search_terms: {stats['search_terms']}, ads: {stats['ads']}")
    return stats


def _pct(v: str | None) -> float | None:
    if v in (None, "", "--"):
        return None
    try:
        return float(v.replace("%", "").replace(",", ".")) / 100.0
    except ValueError:
        return None


def _metrics(r: dict[str, str]) -> dict[str, Any]:
    return {
        "impressions": _i(r.get("Impressions")),
        "clicks": _i(r.get("Clicks")),
        "cost": _f(r.get("Spend")),
        "conversions": _f(r.get("Conversions")),
        "conv_value": _f(r.get("Revenue")),
        "all_conversions": _f(r.get("AllConversions")),
    }


def test_connection() -> str:
    _sdk()
    from bingads.service_client import ServiceClient

    auth = _authorization()
    client = ServiceClient(service="CustomerManagementService", version=13, authorization_data=auth, environment=ENVIRONMENT)
    acc = client.GetAccount(AccountId=int(settings.ms_account_id))
    return f"Verbunden: {acc.Account.Name} ({acc.Account.Id}), Status {acc.Account.AccountLifeCycleStatus}"
