import datetime as dt

from cockpit.analysis import anomalies, experiments, kpis
from cockpit.models import Experiment
from cockpit.util import yesterday


def test_overview_sums(session):
    end = yesterday()
    ov = kpis.overview(session, end - dt.timedelta(days=27), end, "google")
    assert ov["cost"] > 0 and ov["clicks"] > 0
    assert ov["avg_cpc"] == ov["cost"] / ov["clicks"]
    assert ov["days"] == 28


def test_compare_periods_has_deltas(session):
    cmp = kpis.compare_periods(session, 14, "all")
    assert set(cmp["deltas"]) >= {"cost", "clicks", "cpl"}
    assert cmp["previous"]["cost"] >= 0


def test_search_terms_filter(session):
    end = yesterday()
    waste = kpis.search_terms(session, end - dt.timedelta(days=27), end, min_cost=1, only_without_conversions=True)
    assert all(t["conversions"] == 0 for t in waste)
    assert all(t["cost"] >= 1 for t in waste)


def test_anomalies_do_not_duplicate(session):
    day = yesterday()
    cands = anomalies.evaluate(session, day, "google")
    created1 = anomalies.persist(session, day, "google", cands)
    created2 = anomalies.persist(session, day, "google", cands)
    assert len(created1) == len(cands) or len(created1) <= len(cands)
    assert created2 == []


def test_experiment_compare_windows(session):
    e = session.query(Experiment).first()
    cmp = experiments.compare(session, e)
    w = cmp["windows"]
    assert w["base_end"] < w["test_start"]
    base_len = (dt.date.fromisoformat(w["base_end"]) - dt.date.fromisoformat(w["base_start"])).days
    test_len = (dt.date.fromisoformat(w["test_end"]) - dt.date.fromisoformat(w["test_start"])).days
    assert base_len == test_len
    assert "verdict" in cmp and cmp["verdict"]


def test_wilson_interval():
    lo, hi = experiments.wilson_interval(3, 100)
    assert 0 < lo < 0.03 < hi < 0.1
    assert experiments.wilson_interval(0, 0) is None


def test_zero_impressions_plausibility_and_counting(session):
    import datetime as dt
    from cockpit.analysis import anomalies
    from cockpit.models import CampaignDaily, ConversionAction

    day = dt.date(2026, 10, 27)  # Dienstag
    for i in range(1, 10):
        d = day - dt.timedelta(days=i)
        session.add(CampaignDaily(date=d, platform="google", campaign_id="P1", campaign_name="Plausi", status="ENABLED", impressions=100, clicks=5, cost=40.0))
    # Import unvollständig: Kosten und Klicks da, Impressionen 0 -> kein Alarm
    row = CampaignDaily(date=day, platform="google", campaign_id="P1", campaign_name="Plausi", status="ENABLED", impressions=0, clicks=4, cost=148.0)
    session.add(row)
    session.add(ConversionAction(platform="google", external_id="CA9", name="Lead Formular senden", category="SUBMIT_LEAD_FORM", status="ENABLED", primary_for_goal=True, counting_type="MANY_PER_CLICK"))
    session.flush()
    rules = {(a["rule"], a["key"]) for a in anomalies.evaluate(session, day, "google")}
    assert ("zero_impressions", "P1") not in rules
    assert ("counting_type", "CA9") in rules
    # echter Ausfall: alles 0 -> Alarm
    row.clicks, row.cost = 0, 0.0
    session.flush()
    assert ("zero_impressions", "P1") in {(a["rule"], a["key"]) for a in anomalies.evaluate(session, day, "google")}
    assert anomalies.evaluation_day(session, dt.date(2026, 10, 28)) == dt.date(2026, 10, 26)
    session.rollback()
