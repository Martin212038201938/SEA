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


def test_sea_excel_parse_and_kpi(session, tmp_path):
    import datetime as dt
    import io
    from openpyxl import Workbook
    from cockpit.connectors import sea_excel
    from cockpit.analysis import kpis

    wb = Workbook(); ws = wb.active
    ws["B2"], ws["C2"] = dt.datetime(2026, 9, 7), "13.10.2026"
    ws["B6"], ws["B8"], ws["B10"], ws["B12"], ws["B18"] = "Vorname: Kim", "Nachname: Tretter", "E-Mail: ktretter@rajapack.de", "Unternehmen: Rajapack GmbH", "https://trello.com/c/x"
    ws["C10"], ws["C12"] = "E-Mail: a@b.de", "Unternehmen: B GmbH"
    ws["D10"] = ""  # leere Spalte wird übersprungen
    ws["A20"], ws["A21"] = "Status", "Erlös"
    ws["B20"], ws["C20"] = "7.600", None
    ws["B21"] = 1520
    buf = io.BytesIO(); wb.save(buf)
    rows = sea_excel.parse(buf.getvalue())
    assert [r["column"] for r in rows] == ["B", "C"]
    assert rows[0]["net_revenue"] == 7600.0 and rows[0]["margin"] == 1520.0 and rows[0]["company"] == "Rajapack GmbH" and rows[0]["date"] == dt.date(2026, 9, 7)
    assert rows[1]["date"] == dt.date(2026, 10, 13) and rows[1]["net_revenue"] is None
    sea_excel.store(session, rows)
    k = kpis.sea_revenue(session, dt.date(2026, 9, 1), dt.date(2026, 10, 31))
    assert k["ads_leads"] == 2 and k["net_revenue"] == 7600.0 and k["leads_with_revenue"] == 1
    assert sea_excel._num("7.600,50") == 7600.5 and sea_excel._num("1520") == 1520.0
    session.rollback()
