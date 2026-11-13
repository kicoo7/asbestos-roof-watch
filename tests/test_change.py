import numpy as np
import pandas as pd

from roofwatch.change import classify_change, detect_changes

GREY = dict(blue=0.12, green=0.13, red=0.14, nir=0.20, swir16=0.24, swir22=0.20)
METAL = dict(blue=0.30, green=0.31, red=0.32, nir=0.35, swir16=0.33, swir22=0.30)
PV = dict(blue=0.06, green=0.06, red=0.05, nir=0.08, swir16=0.07, swir22=0.06)


def _series(states, start="2024-01-01", step_days=10, noise=0.003, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for i, s in enumerate(states):
        r = {k: v + rng.normal(0, noise) for k, v in s.items()}
        rows.append({"roof_id": 1, "date": pd.Timestamp(start) + pd.Timedelta(days=step_days * i), **r})
    return pd.DataFrame(rows)


def test_stable_roof_has_no_change():
    assert detect_changes(_series([GREY] * 30)).empty


def test_step_change_detected_at_first_of_two_consecutive_dates():
    obs = _series([GREY] * 15 + [METAL] * 5)
    ch = detect_changes(obs)
    assert len(ch) == 1
    assert ch.iloc[0]["date"] == obs["date"].iloc[15]
    assert ch.iloc[0]["change_type"] == "bright_new_roof"


def test_single_outlier_is_not_a_change():
    obs = _series([GREY] * 10 + [METAL] + [GREY] * 10)
    assert detect_changes(obs).empty


def test_change_at_last_date_waits_for_confirmation():
    obs = _series([GREY] * 15 + [METAL])
    assert detect_changes(obs).empty


def test_too_little_history_gives_no_change():
    assert detect_changes(_series([GREY] * 3 + [METAL] * 3), min_history=5).empty


def test_only_one_event_per_new_state():
    ch = detect_changes(_series([GREY] * 15 + [METAL] * 20))
    assert len(ch) == 1


def test_classify_change_types():
    assert classify_change(GREY, METAL) == "bright_new_roof"
    assert classify_change(GREY, PV) == "dark_pv"
    veg = dict(blue=0.04, green=0.08, red=0.05, nir=0.35, swir16=0.2, swir22=0.1)
    assert classify_change(GREY, veg) == "vegetation"
    soil = dict(blue=0.08, green=0.10, red=0.13, nir=0.20, swir16=0.32, swir22=0.28)
    assert classify_change(GREY, soil) == "demolition_bare"
    assert classify_change(GREY, dict(GREY, red=0.17)) == "other"
