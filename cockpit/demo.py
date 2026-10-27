"""Synthetische Demo-Daten, damit Oberfläche, Reports und Chat vor der API-Freigabe benutzbar sind.

Die Zahlen sind erfunden, orientieren sich aber an der Größenordnung des echten Kontos
(ca. 25 €/Tag, CPC 3–7 €, wenige Conversions). Alle Datensätze tragen das Präfix „demo-“ bzw. source „demo“.
"""
from __future__ import annotations

import datetime as dt
import random

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .models import (
    AdDaily,
    AdGroupDaily,
    AssetDaily,
    AuctionInsight,
    CampaignDaily,
    ChangeEvent,
    ConversionAction,
    Experiment,
    KeywordDaily,
    Lead,
    SearchTermDaily,
    Suggestion,
)
from .util import set_setting, yesterday

CAMPAIGN_ID = "demo-1001"
CAMPAIGN_NAME = "Copilot Schulung – Suche DE (Demo)"
AD_GROUPS = {
    "demo-2001": "Copilot Training & Schulung",
    "demo-2002": "KI-Kompetenz & AI Act",
    "demo-2003": "Copilot Workshop & Seminar",
}
KEYWORDS = {
    "demo-2001": [("copilot schulung", "PHRASE", 7), ("copilot training", "PHRASE", 6), ("microsoft copilot schulung", "EXACT", 6), ("copilot schulung unternehmen", "PHRASE", 7), ("m365 copilot training", "EXACT", 5), ("copilot training köln", "EXACT", 0)],
    "demo-2002": [("ki schulung unternehmen", "PHRASE", 6), ("ai act schulung", "PHRASE", 5), ("ki kompetenz schulung", "PHRASE", 6), ("eu ai act schulung mitarbeiter", "EXACT", 0), ("ki training mitarbeiter", "PHRASE", 5)],
    "demo-2003": [("copilot workshop", "PHRASE", 6), ("copilot seminar", "EXACT", 5), ("copilot kurs firmen", "EXACT", 0), ("copilot inhouse schulung", "EXACT", 7)],
}
SEARCH_TERMS_GOOD = ["copilot schulung für unternehmen", "microsoft copilot training inhouse", "copilot workshop firma", "ki schulung mitarbeiter", "ai act schulung pflicht", "copilot seminar köln", "copilot schulung führungskräfte"]
SEARCH_TERMS_BAD = ["copilot kostenlos lernen", "was ist microsoft copilot", "copilot tutorial youtube", "copilot lizenz preis", "pilot ausbildung", "ms 900 prüfung", "copilot login", "copilot studio tutorial", "chatgpt kurs kostenlos", "copilot jobs"]
HEADLINES_A = ["Microsoft Copilot Schulung", "Inhouse-Training für Teams", "Praxis statt Theorie", "Inkl. AI-Act-Pflichtwissen", "Köln & deutschlandweit"]
HEADLINES_B = ["Copilot im Arbeitsalltag", "Schulung für Entscheider", "Erstgespräch in 15 Minuten", "Trainer mit M365-Praxis", "Für Unternehmen im DACH-Raum"]
COMPETITORS = ["wettbewerber-akademie.de", "it-seminare-beispiel.de", "ki-training-muster.com", "microsoft.com"]


def seed(session: Session, days: int = 120, seed: int = 42) -> int:
    random.seed(seed)
    purge(session)
    end = yesterday()
    start = end - dt.timedelta(days=days - 1)
    switch_day = end - dt.timedelta(days=55)  # Wechsel auf „Conversions maximieren“
    rows = 0
    for d in _dates(start, end):
        weekday = d.weekday() < 5
        if not weekday:
            continue
        after = d >= switch_day
        budget = 25.0
        # Tageswerte Kampagne
        impressions = int(random.gauss(150 if after else 120, 35))
        impressions = max(20, impressions)
        ctr = random.gauss(0.075 if after else 0.052, 0.012)
        clicks = max(1, int(impressions * max(0.02, ctr)))
        cpc = max(1.5, random.gauss(6.3 if after else 3.8, 0.9))
        cost = min(clicks * cpc, budget * 1.6)
        clicks = max(1, int(cost / cpc))
        conv = 1.0 if random.random() < (0.14 if after else 0.09) else 0.0
        c_metrics = dict(impressions=impressions, clicks=clicks, cost=round(cost, 2), conversions=conv, conv_value=0.0, all_conversions=conv + (random.randint(2, 6) if not after else 0))
        session.add(
            CampaignDaily(
                date=d,
                platform="google",
                campaign_id=CAMPAIGN_ID,
                campaign_name=CAMPAIGN_NAME,
                status="ENABLED",
                channel_type="SEARCH",
                bidding_strategy="MAXIMIZE_CONVERSIONS" if after else "MAXIMIZE_CLICKS",
                budget_amount=budget,
                end_date="2037-12-30",
                search_impression_share=round(random.uniform(0.32, 0.48), 3),
                search_budget_lost_is=round(random.uniform(0.12, 0.3), 3),
                search_rank_lost_is=round(random.uniform(0.28, 0.45), 3),
                top_impression_pct=round(random.uniform(0.55, 0.8), 3),
                abs_top_impression_pct=round(random.uniform(0.2, 0.45), 3),
                **c_metrics,
            )
        )
        rows += 1
        # Verteilung auf Anzeigengruppen
        shares = _split(len(AD_GROUPS), (0.55, 0.25, 0.20))
        remaining_conv = conv
        for (agid, agname), share in zip(AD_GROUPS.items(), shares):
            ag = _scale(c_metrics, share)
            if remaining_conv and share == max(shares):
                ag["conversions"] = remaining_conv
                remaining_conv = 0
            else:
                ag["conversions"] = 0.0
            session.add(AdGroupDaily(date=d, platform="google", campaign_id=CAMPAIGN_ID, campaign_name=CAMPAIGN_NAME, ad_group_id=agid, ad_group_name=agname, status="ENABLED", **ag))
            # Keywords
            kws = KEYWORDS[agid]
            kshares = _split(len(kws))
            for (text, mt, qs), ks in zip(kws, kshares):
                km = _scale(ag, ks) if qs else dict(impressions=0, clicks=0, cost=0.0, conversions=0.0, conv_value=0.0, all_conversions=0.0)
                km["conversions"] = ag["conversions"] if (ks == max(kshares) and qs) else 0.0
                session.add(
                    KeywordDaily(
                        date=d,
                        platform="google",
                        campaign_id=CAMPAIGN_ID,
                        ad_group_id=agid,
                        ad_group_name=agname,
                        criterion_id=f"demo-k-{abs(hash(text)) % 100000}",
                        keyword_text=text,
                        match_type=mt,
                        status="ENABLED",
                        serving_status="ELIGIBLE" if qs else "RARELY_SERVED_LOW_SEARCH_VOLUME",
                        quality_score=qs or None,
                        **km,
                    )
                )
            # Suchbegriffe: pro Tag 2–4 Begriffe, davon teils Streuverlust
            n_bad = sum(1 for _ in range(random.randint(2, 4)) if random.random() < (0.3 if after else 0.55))
            picked = [(t, True) for t in random.sample(SEARCH_TERMS_BAD, n_bad)] + [(t, False) for t in random.sample(SEARCH_TERMS_GOOD, max(1, 3 - n_bad))]
            for term, bad in picked:
                cl = random.randint(1, 3)
                session.add(
                    SearchTermDaily(
                        date=d,
                        platform="google",
                        campaign_id=CAMPAIGN_ID,
                        ad_group_id=agid,
                        ad_group_name=agname,
                        search_term=term,
                        matched_keyword=kws[0][0],
                        match_type=kws[0][1],
                        status="NONE",
                        impressions=cl * random.randint(4, 12),
                        clicks=cl,
                        cost=round(cl * cpc * random.uniform(0.3, 0.6), 2),
                        conversions=ag["conversions"] if (not bad and random.random() < 0.5) else 0.0,
                    )
                )
        # Anzeigen
        for ad_id, hl, share, strength in (("demo-ad-1", HEADLINES_A, 0.6, "GOOD"), ("demo-ad-2", HEADLINES_B, 0.4, "EXCELLENT")):
            if ad_id == "demo-ad-2" and d < switch_day:
                continue
            am = _scale(c_metrics, share if d >= switch_day else 1.0)
            am["conversions"] = conv if share == 0.6 else 0.0
            session.add(AdDaily(date=d, platform="google", campaign_id=CAMPAIGN_ID, ad_group_id="demo-2001", ad_id=ad_id, ad_type="RESPONSIVE_SEARCH_AD", ad_strength=strength, status="ENABLED", final_url="https://www.copilotenschule.de/", headlines=hl, descriptions=["Copilot-Schulung für Unternehmen. Praxisnah, auf Deutsch, vor Ort oder remote.", "Jetzt kostenloses Erstgespräch buchen."], **am))
            for i, h in enumerate(hl):
                session.add(AssetDaily(date=d, platform="google", campaign_id=CAMPAIGN_ID, ad_group_id="demo-2001", asset_id=f"{ad_id}-h{i}", field_type="HEADLINE", asset_text=h, performance_label=random.choice(["BEST", "GOOD", "LOW", "LEARNING"]), impressions=int(am["impressions"] * random.uniform(0.2, 0.6)), clicks=int(am["clicks"] * random.uniform(0.1, 0.5)), cost=round(am["cost"] * random.uniform(0.1, 0.4), 2)))

    # Änderungsverlauf
    events = [
        (end - dt.timedelta(days=84), "CAMPAIGN", "UPDATE", ["network_settings.target_search_network"], {"network_settings.target_search_network": True}, {"network_settings.target_search_network": False}, "Suchnetzwerk-Partner deaktiviert"),
        (end - dt.timedelta(days=84), "AD_GROUP_CRITERION", "REMOVE", [], {}, {}, "56 Phrase-Keywords entfernt"),
        (end - dt.timedelta(days=84), "AD_GROUP_CRITERION", "CREATE", [], {}, {}, "56 Exact-Keywords angelegt"),
        (end - dt.timedelta(days=70), "SHARED_SET", "CREATE", [], {}, {"name": "Ausschluss-Keywords V1"}, "Negativliste angelegt"),
        (switch_day, "CAMPAIGN", "UPDATE", ["bidding_strategy_type"], {"bidding_strategy_type": "MAXIMIZE_CLICKS"}, {"bidding_strategy_type": "MAXIMIZE_CONVERSIONS"}, "Gebotsstrategie: Klicks maximieren → Conversions maximieren"),
        (end - dt.timedelta(days=40), "CAMPAIGN_BUDGET", "UPDATE", ["amount_micros"], {"amount_micros": 25000000}, {"amount_micros": 35000000}, "Tagesbudget 25 € → 35 €"),
        (end - dt.timedelta(days=33), "CAMPAIGN_BUDGET", "UPDATE", ["amount_micros"], {"amount_micros": 35000000}, {"amount_micros": 25000000}, "Tagesbudget 35 € → 25 €"),
        (end - dt.timedelta(days=20), "AD_GROUP_AD", "CREATE", [], {}, {"ad.type": "RESPONSIVE_SEARCH_AD"}, "Zweite RSA angelegt"),
        (end - dt.timedelta(days=9), "CAMPAIGN_CRITERION", "CREATE", [], {}, {"negative": True, "keyword.text": "kostenlos"}, "Auszuschließendes Keyword „kostenlos“"),
    ]
    for i, (day, rtype, ctype, fields, old, new, summary) in enumerate(events):
        session.add(ChangeEvent(platform="google", external_id=f"demo-ev-{i}", change_date_time=dt.datetime.combine(day, dt.time(9, 30 + i)), user_email="chris@copilotenschule.de", client_type="GOOGLE_ADS_WEB_CLIENT", resource_type=rtype, change_type=ctype, campaign_id=CAMPAIGN_ID, changed_fields=fields, old_values=old, new_values=new, summary=summary))

    # Auktionsdaten
    for dom, is_, ov, outr in zip(COMPETITORS, (0.31, 0.22, 0.14, 0.46), (0.6, 0.45, 0.3, 0.7), (0.4, 0.55, 0.65, 0.2)):
        session.add(AuctionInsight(date_from=end - dt.timedelta(days=6), date_to=end, platform="google", campaign_id=CAMPAIGN_ID, domain=dom, impression_share=is_, overlap_rate=ov, outranking_share=outr, position_above_rate=round(1 - outr, 2), top_of_page_rate=0.7, abs_top_rate=0.3))
    session.add(AuctionInsight(date_from=end - dt.timedelta(days=6), date_to=end, platform="google", campaign_id=CAMPAIGN_ID, domain="(Sie)", impression_share=0.41, overlap_rate=None, outranking_share=None, position_above_rate=None, top_of_page_rate=0.68, abs_top_rate=0.33))

    # Conversion-Aktionen
    session.add(ConversionAction(platform="google", external_id="demo-ca-1", name="Lead Kontaktformular", category="SUBMIT_LEAD_FORM", status="ENABLED", primary_for_goal=True, counting_type="ONE_PER_CLICK", conversions_30d=3, all_conversions_30d=3))
    session.add(ConversionAction(platform="google", external_id="demo-ca-2", name="Seitenaufruf (Seitenaufbau copilotenschule)", category="PAGE_VIEW", status="ENABLED", primary_for_goal=True, counting_type="MANY_PER_CLICK", conversions_30d=0, all_conversions_30d=187))
    session.add(ConversionAction(platform="google", external_id="demo-ca-3", name="Erstgespräch gestartet", category="LEAD", status="ENABLED", primary_for_goal=False, counting_type="ONE_PER_CLICK", conversions_30d=0, all_conversions_30d=5))

    # Leads (anonymisiert)
    companies = ["Beispiel Maschinenbau GmbH", "Muster Verpackungen AG", "Demo Verband e. V.", "Testwerk Logistik GmbH", "Probe Beratung GmbH", "Beispiel Stiftung", "Muster Pharma GmbH", "Demo Marketing GmbH", "Testbau Holding"]
    for i, comp in enumerate(companies):
        day = end - dt.timedelta(days=int(68 - i * 7.5))
        status = "gewonnen" if i == 0 else ("angebot" if i in (1, 4) else ("verloren" if i == 2 else "kontaktiert"))
        session.add(Lead(source="demo", external_id=f"demo-lead-{i}", created_on=day, name="Ansprechpartner", company=comp, status=status, value=7600 if i == 0 else (5500 if status == "angebot" else 0), revenue=1520 if i == 0 else 0, channel="google" if i % 3 else "organisch", kind="kontaktformular" if i % 4 else "erstgespraech", notes="Demo"))

    # Experiment + Vorschläge
    session.add(Experiment(name="Gebotsstrategie: Conversions maximieren (Demo)", hypothesis="Smart Bidding kauft teurere, aber besser konvertierende Klicks; CPL sinkt trotz höherem CPC.", platform="google", scope={"campaign_ids": [CAMPAIGN_ID]}, start_date=switch_day, status="laufend", created_by="demo", tags=["demo", "gebotsstrategie"]))
    session.add(
        Suggestion(
            source="manual",
            platform="google",
            category="negativ-keywords",
            title="Negativliste um Selbstlern- und Lizenzbegriffe erweitern (Demo)",
            rationale="In 28 Tagen entfielen rund 30 % der Kosten auf Suchbegriffe wie „kostenlos lernen“, „tutorial“, „lizenz preis“ ohne eine Conversion.",
            expected_effect="20–30 % des Budgets verschieben sich zu Begriffen mit Kaufabsicht; Klicks sinken, CPL dürfte fallen. Effekt nach 2–3 Wochen im Suchbegriffsbericht sichtbar.",
            steps=["Tools → Gemeinsam genutzte Bibliothek → Listen mit auszuschließenden Keywords → „Ausschluss-Keywords V1“ öffnen", "Begriffe ergänzen: kostenlos, tutorial, youtube, lizenz, preis, login, jobs, prüfung, ms-900 (als Wortgruppe)", "Liste der Kampagne zuweisen, falls noch nicht geschehen", "Nach 7 Tagen Suchbegriffsbericht prüfen: tauchen die Begriffe noch auf?"],
            priority=1,
            confidence="hoch",
            effort="30 Min",
            evidence={"text": "Demo-Daten", "campaign_ids": [CAMPAIGN_ID]},
        )
    )
    set_setting(session, "demo_seeded", "1")
    session.flush()
    return rows


def purge(session: Session) -> None:
    for model in (CampaignDaily, AdGroupDaily, KeywordDaily, SearchTermDaily, AdDaily, AssetDaily, AuctionInsight):
        session.execute(delete(model).where(model.campaign_id.like("demo-%")))
    session.execute(delete(ChangeEvent).where(ChangeEvent.external_id.like("demo-%")))
    session.execute(delete(ConversionAction).where(ConversionAction.external_id.like("demo-%")))
    session.execute(delete(Lead).where(Lead.source == "demo"))
    session.execute(delete(Experiment).where(Experiment.created_by == "demo"))
    session.execute(delete(Suggestion).where(Suggestion.title.like("%(Demo)%")))
    set_setting(session, "demo_seeded", "0")
    session.flush()


def _dates(start: dt.date, end: dt.date):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def _split(n: int, weights: tuple[float, ...] | None = None) -> list[float]:
    if weights is None:
        raw = [random.uniform(0.5, 1.5) for _ in range(n)]
        s = sum(raw)
        return [r / s for r in raw]
    return list(weights)


def _scale(m: dict, share: float) -> dict:
    return dict(
        impressions=int(m["impressions"] * share),
        clicks=int(m["clicks"] * share),
        cost=round(m["cost"] * share, 2),
        conversions=0.0,
        conv_value=0.0,
        all_conversions=round(m.get("all_conversions", 0) * share, 1),
    )
