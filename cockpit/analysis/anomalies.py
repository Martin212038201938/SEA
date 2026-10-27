"""Regelbasierte Auffälligkeiten. Jede Regel liefert Alert-Kandidaten; Duplikate pro Tag werden verworfen."""
from __future__ import annotations

import datetime as dt
import statistics
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Alert, CampaignDaily, ConversionAction, KeywordDaily, SearchTermDaily
from ..util import eur, get_setting_json, pct, safe_div

DEFAULT_THRESHOLDS = {
    "cpc_spike_pct": 0.40,          # Ø-CPC der letzten 3 Tage vs. Median der 14 Tage davor
    "spend_over_budget_factor": 1.3,  # Tagesausgabe über Faktor x Tagesbudget
    "zero_impressions_weekday": True,
    "no_conversion_days": 14,       # so viele Tage ohne Conversion bei Klicks
    "no_conversion_min_clicks": 40,
    "lost_is_budget": 0.25,         # Anteil verlorener Impressionen durch Budget
    "wasted_term_cost": 25.0,       # Suchbegriff ohne Conversion mit Kosten ab x € (28 Tage)
    "campaign_end_within_days": 14,
    "cpl_over_max_days": 28,
}


def thresholds(session: Session) -> dict[str, Any]:
    t = dict(DEFAULT_THRESHOLDS)
    t.update(get_setting_json(session, "alert_thresholds", {}))
    return t


def _daily_campaign(session: Session, platform: str, start: dt.date, end: dt.date) -> dict[str, list[CampaignDaily]]:
    rows = session.execute(
        select(CampaignDaily)
        .where(CampaignDaily.platform == platform, CampaignDaily.date >= start, CampaignDaily.date <= end)
        .order_by(CampaignDaily.campaign_id, CampaignDaily.date)
    ).scalars().all()
    out: dict[str, list[CampaignDaily]] = {}
    for r in rows:
        out.setdefault(r.campaign_id, []).append(r)
    return out


def evaluate(session: Session, day: dt.date, platform: str = "google") -> list[dict[str, Any]]:
    """Alle Regeln für einen Tag (in der Regel: gestern) auswerten. Gibt Alert-Dicts zurück."""
    t = thresholds(session)
    found: list[dict[str, Any]] = []
    lookback_start = day - dt.timedelta(days=30)
    by_campaign = _daily_campaign(session, platform, lookback_start, day)

    for cid, rows in by_campaign.items():
        name = rows[-1].campaign_name
        status = rows[-1].status
        last = rows[-1] if rows[-1].date == day else None
        if status not in ("ENABLED", "Active", "") and status is not None:
            continue

        # 1) CPC-Sprung
        recent = [r for r in rows if r.date > day - dt.timedelta(days=3) and r.clicks]
        base = [r for r in rows if day - dt.timedelta(days=17) <= r.date <= day - dt.timedelta(days=3) and r.clicks]
        if len(recent) >= 2 and len(base) >= 7:
            recent_cpc = safe_div(sum(r.cost for r in recent), sum(r.clicks for r in recent))
            base_cpc = statistics.median([r.cost / r.clicks for r in base])
            if recent_cpc and base_cpc and (recent_cpc - base_cpc) / base_cpc >= t["cpc_spike_pct"]:
                found.append(
                    dict(
                        rule="cpc_spike",
                        key=cid,
                        severity="warn",
                        title=f"CPC-Sprung in „{name}“",
                        message=f"Ø-CPC der letzten 3 Tage {eur(recent_cpc)} gegenüber Median {eur(base_cpc)} der zwei Wochen davor (+{(recent_cpc - base_cpc) / base_cpc * 100:.0f} %).",
                        payload={"campaign_id": cid, "recent_cpc": recent_cpc, "base_cpc": base_cpc},
                    )
                )

        # 2) Überausgabe
        if last and last.budget_amount and last.cost > last.budget_amount * t["spend_over_budget_factor"]:
            found.append(
                dict(
                    rule="overspend",
                    key=cid,
                    severity="info",
                    title=f"Tagesausgabe über Budget in „{name}“",
                    message=f"{eur(last.cost)} ausgegeben bei {eur(last.budget_amount)} Tagesbudget. Google darf bis zum Doppelten ausliefern; über den Monat gleicht sich das normalerweise aus.",
                    payload={"campaign_id": cid, "cost": last.cost, "budget": last.budget_amount},
                )
            )

        # 3) Keine Impressionen an einem Werktag
        if t["zero_impressions_weekday"] and day.weekday() < 5:
            prev_active = [r for r in rows if r.date < day and r.impressions > 0]
            if prev_active and (last is None or last.impressions == 0):
                found.append(
                    dict(
                        rule="zero_impressions",
                        key=cid,
                        severity="kritisch",
                        title=f"Keine Impressionen in „{name}“",
                        message=f"Am {day.strftime('%d.%m.%Y')} (Werktag) wurden keine Impressionen erfasst, obwohl die Kampagne zuvor ausgeliefert hat. Status, Budget, Zahlungsmittel, Enddatum und Anzeigenfreigabe prüfen.",
                        payload={"campaign_id": cid},
                    )
                )

        # 4) Klicks ohne Conversions
        window = [r for r in rows if r.date > day - dt.timedelta(days=t["no_conversion_days"])]
        clicks = sum(r.clicks for r in window)
        convs = sum(r.conversions for r in window)
        if clicks >= t["no_conversion_min_clicks"] and convs == 0:
            found.append(
                dict(
                    rule="no_conversions",
                    key=cid,
                    severity="warn",
                    title=f"{clicks} Klicks ohne Conversion in „{name}“",
                    message=f"In den letzten {t['no_conversion_days']} Tagen {clicks} Klicks und {eur(sum(r.cost for r in window))} Kosten, aber keine erfasste Conversion. Erst Tracking prüfen (Testconversion), dann Landingpage und Suchbegriffe.",
                    payload={"campaign_id": cid, "clicks": clicks},
                )
            )

        # 5) Verlorener Anteil durch Budget
        recent7 = [r for r in rows if r.date > day - dt.timedelta(days=7) and r.search_budget_lost_is is not None and r.impressions]
        if recent7:
            lost = sum(r.search_budget_lost_is * r.impressions for r in recent7) / sum(r.impressions for r in recent7)
            if lost >= t["lost_is_budget"]:
                found.append(
                    dict(
                        rule="lost_is_budget",
                        key=cid,
                        severity="info",
                        title=f"Budget begrenzt Auslieferung in „{name}“",
                        message=f"{pct(lost)} der möglichen Impressionen gingen in den letzten 7 Tagen durch Budget verloren. Vor einer Erhöhung prüfen, ob die zusätzlichen Impressionen Kaufabsicht haben (Suchbegriffsbericht).",
                        payload={"campaign_id": cid, "lost_is_budget": lost},
                    )
                )

        # 6) Enddatum naht
        end_date = (rows[-1].end_date or "").strip()
        if end_date and end_date not in ("2037-12-30",):
            try:
                ed = dt.date.fromisoformat(end_date)
                if 0 <= (ed - day).days <= t["campaign_end_within_days"]:
                    found.append(
                        dict(
                            rule="campaign_end",
                            key=cid,
                            severity="kritisch",
                            title=f"Kampagne „{name}“ endet am {ed.strftime('%d.%m.%Y')}",
                            message="Das Enddatum liegt innerhalb der nächsten zwei Wochen. Entfernen oder bewusst bestätigen.",
                            payload={"campaign_id": cid, "end_date": end_date},
                        )
                    )
            except ValueError:
                pass

        # 7) CPL über Maximum
        w = [r for r in rows if r.date > day - dt.timedelta(days=t["cpl_over_max_days"])]
        c = sum(r.cost for r in w)
        k = sum(r.conversions for r in w)
        if k >= 2 and c / k > settings.max_cpl:
            found.append(
                dict(
                    rule="cpl_over_max",
                    key=cid,
                    severity="warn",
                    title=f"CPL über Abbruchschwelle in „{name}“",
                    message=f"Kosten je Conversion der letzten {t['cpl_over_max_days']} Tage: {eur(c / k)} (Ziel {eur(settings.target_cpl)}, Schwelle {eur(settings.max_cpl)}).",
                    payload={"campaign_id": cid, "cpl": c / k},
                )
            )

    # 8) Teure Suchbegriffe ohne Conversion (28 Tage)
    st = session.execute(
        select(
            SearchTermDaily.search_term,
            func.sum(SearchTermDaily.cost).label("cost"),
            func.sum(SearchTermDaily.clicks).label("clicks"),
            func.sum(SearchTermDaily.conversions).label("conv"),
            func.max(SearchTermDaily.status).label("status"),
        )
        .where(SearchTermDaily.platform == platform, SearchTermDaily.date > day - dt.timedelta(days=28), SearchTermDaily.date <= day)
        .group_by(SearchTermDaily.search_term)
        .having(func.sum(SearchTermDaily.conversions) == 0, func.sum(SearchTermDaily.cost) >= t["wasted_term_cost"])
        .order_by(func.sum(SearchTermDaily.cost).desc())
        .limit(5)
    ).all()
    for r in st:
        if (r.status or "").upper() == "EXCLUDED":
            continue
        found.append(
            dict(
                rule="wasted_search_term",
                key=r.search_term,
                severity="info",
                title=f"Suchbegriff ohne Conversion: „{r.search_term}“",
                message=f"{eur(r.cost)} und {int(r.clicks)} Klicks in 28 Tagen ohne Conversion. Prüfen, ob Kaufabsicht vorliegt; sonst als auszuschließendes Keyword aufnehmen.",
                payload={"search_term": r.search_term, "cost": r.cost, "clicks": r.clicks},
            )
        )

    # 9) Keywords mit „geringes Suchvolumen“ (aktueller Stand)
    low = session.execute(
        select(func.count(KeywordDaily.id))
        .where(KeywordDaily.platform == platform, KeywordDaily.date == day, KeywordDaily.serving_status.ilike("%LOW_SEARCH_VOLUME%"))
    ).scalar()
    total = session.execute(select(func.count(KeywordDaily.id)).where(KeywordDaily.platform == platform, KeywordDaily.date == day)).scalar()
    if total and low and low / total >= 0.4:
        found.append(
            dict(
                rule="low_search_volume",
                key="keywords",
                severity="info",
                title=f"{low} von {total} Keywords inaktiv wegen geringem Suchvolumen",
                message="Ein großer Teil der Keywords liefert nicht aus. Exact-Match in Nischen-Kombinationen (z. B. Stadt + Training) ist meist zu eng; Phrase-Match mit sauberer Negativliste prüfen.",
                payload={"low": low, "total": total},
            )
        )

    # 10) Conversion-Datenqualität: Aktion mit Kategorie PAGE_VIEW als primär
    bad = session.execute(
        select(ConversionAction).where(ConversionAction.platform == platform, ConversionAction.primary_for_goal.is_(True), ConversionAction.category.in_(("PAGE_VIEW", "DEFAULT")))
    ).scalars().all()
    for ca in bad:
        found.append(
            dict(
                rule="conversion_quality",
                key=ca.external_id,
                severity="warn",
                title=f"Primäre Conversion-Aktion „{ca.name}“ misst Seitenaufrufe",
                message="Eine primäre Aktion mit Kategorie Seitenaufruf speist Smart Bidding mit Rauschen. Auf sekundär stellen oder entfernen.",
                payload={"conversion_action": ca.external_id, "category": ca.category},
            )
        )

    return found


def persist(session: Session, day: dt.date, platform: str, candidates: list[dict[str, Any]]) -> list[Alert]:
    """Neue Alerts speichern; vorhandene (gleiche Regel, gleicher Schlüssel, gleicher Tag) überspringen."""
    created: list[Alert] = []
    for c in candidates:
        exists = session.execute(
            select(Alert).where(Alert.date == day, Alert.platform == platform, Alert.rule == c["rule"], Alert.key == str(c["key"]))
        ).scalar_one_or_none()
        if exists:
            continue
        a = Alert(date=day, platform=platform, rule=c["rule"], key=str(c["key"]), severity=c["severity"], title=c["title"], message=c["message"], payload=c.get("payload", {}))
        session.add(a)
        created.append(a)
    session.flush()
    return created
