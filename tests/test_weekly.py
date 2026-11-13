import pandas as pd

from roofwatch.weekly import new_events, roof_status


def _ev(rows):
    return pd.DataFrame(rows, columns=["roof_id", "date", "confirmed_on", "change_type"]).assign(
        date=lambda d: pd.to_datetime(d["date"]), confirmed_on=lambda d: pd.to_datetime(d["confirmed_on"]))


def test_new_events_excludes_already_reported():
    known = _ev([(1, "2026-01-01", "2026-01-11", "dark_pv")])
    ev = _ev([(1, "2026-01-01", "2026-01-11", "dark_pv"), (2, "2026-11-01", "2026-11-06", "bright_new_roof")])
    out = new_events(ev, known, run_date=pd.Timestamp("2026-11-13"))
    assert out["roof_id"].tolist() == [2]


def test_new_events_backfills_old_events_as_history_not_news():
    ev = _ev([(1, "2019-05-01", "2019-05-11", "dark_pv"), (2, "2026-11-01", "2026-11-06", "bright_new_roof")])
    out = new_events(ev, known=None, run_date=pd.Timestamp("2026-11-13"), news_window_days=60)
    assert out["roof_id"].tolist() == [2]


def test_new_events_rerun_same_week_gives_nothing_new():
    ev = _ev([(2, "2026-11-01", "2026-11-06", "bright_new_roof")])
    assert new_events(ev, known=ev, run_date=pd.Timestamp("2026-11-13")).empty


def test_roof_status_precedence():
    roofs = pd.Series([1, 2, 3, 4])
    official = {2, 3}
    scores = pd.Series({1: 0.9, 2: 0.1, 3: 0.9, 4: 0.2})
    changed_after_label = {3}
    st = roof_status(roofs, official, scores, changed_after_label, threshold=0.5)
    assert st.tolist() == ["suspected", "official_ac", "changed", "not_suspected"]


def test_record_news_rerun_same_day_returns_same_news():
    from roofwatch.weekly import mark_published, record_news

    ev = _ev([(2, "2026-11-01", "2026-11-06", "bright_new_roof")])
    day = pd.Timestamp("2026-11-13")
    known1, news1 = record_news(ev, None, day)
    known1 = mark_published(known1, news1)
    known2, news2 = record_news(ev, known1, day)
    assert news1["roof_id"].tolist() == [2] and news2["roof_id"].tolist() == [2]
    assert len(known2) == 1
    # next week: already reported, not news again
    _, news3 = record_news(ev, known2, day + pd.Timedelta(days=7))
    assert news3.empty


def test_record_news_stores_old_history_without_reporting_it():
    from roofwatch.weekly import record_news

    ev = _ev([(1, "2019-05-01", "2019-05-11", "dark_pv")])
    known, news = record_news(ev, None, pd.Timestamp("2026-11-13"))
    assert news.empty and len(known) == 1 and known["reported_on"].isna().all()


def test_new_events_same_roof_shifted_date_is_not_new():
    # a late-arriving scene moves the detected date by a few weeks: same change, not news
    known = _ev([(2, "2026-10-01", "2026-10-06", "bright_new_roof")])
    ev = _ev([(2, "2026-10-20", "2026-10-25", "bright_new_roof")])
    assert new_events(ev, known, run_date=pd.Timestamp("2026-11-13"), same_event_days=60).empty


def test_record_news_does_not_register_shifted_duplicates():
    from roofwatch.weekly import record_news

    day = pd.Timestamp("2026-11-13")
    known, _ = record_news(_ev([(2, "2026-10-01", "2026-10-06", "bright_new_roof")]), None, day)
    known2, news = record_news(_ev([(2, "2026-10-20", "2026-10-25", "bright_new_roof")]), known, day)
    assert len(known2) == 1


def test_unpublished_news_from_a_crashed_run_carries_over():
    from roofwatch.weekly import mark_published, record_news

    ev = _ev([(2, "2026-11-01", "2026-11-06", "bright_new_roof")])
    wk1 = pd.Timestamp("2026-11-13")
    reg1, news1 = record_news(ev, None, wk1)          # run crashes before publishing
    reg2, news2 = record_news(ev, reg1, wk1 + pd.Timedelta(days=7))
    assert news2["roof_id"].tolist() == [2]
    reg2 = mark_published(reg2, news2)
    _, news3 = record_news(ev, reg2, wk1 + pd.Timedelta(days=14))
    assert news3.empty


def test_published_news_rerun_same_day_is_stable():
    from roofwatch.weekly import mark_published, record_news

    ev = _ev([(2, "2026-11-01", "2026-11-06", "bright_new_roof")])
    day = pd.Timestamp("2026-11-13")
    reg, news = record_news(ev, None, day)
    reg = mark_published(reg, news)
    _, again = record_news(ev, reg, day)
    assert again["roof_id"].tolist() == [2]


def test_no_events_at_all_gives_a_usable_register():
    from roofwatch.change import detect_changes
    from roofwatch.weekly import record_news

    stable = pd.DataFrame({"roof_id": [1] * 10, "date": pd.date_range("2026-01-01", periods=10, freq="10D"),
                           "blue": 0.1, "green": 0.1, "red": 0.1, "nir": 0.2})
    ev = detect_changes(stable)
    reg, news = record_news(ev, None, pd.Timestamp("2026-11-13"))
    assert {"roof_id", "date", "confirmed_on", "change_type", "reported_on", "published"} <= set(reg.columns)
    assert reg[reg["confirmed_on"] >= pd.Timestamp("2026-01-01")].empty and news.empty


def test_roof_status_without_scores_does_not_claim_an_assessment():
    roofs = pd.Series([1, 2])
    st = roof_status(roofs, {2}, pd.Series(dtype=float), set(), threshold=float("inf"), scored=False)
    assert st.tolist() == ["no_official_record", "official_ac"]
