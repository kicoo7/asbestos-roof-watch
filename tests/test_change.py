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


def _many_roofs(n_roofs=60, n_dates=20, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for r in range(n_roofs):
        level = rng.uniform(0.8, 1.2)
        for i in range(n_dates):
            d = pd.Timestamp("2024-01-01") + pd.Timedelta(days=10 * i)
            rows.append({"roof_id": r, "date": d, **{k: v * level * (1 + rng.normal(0, 0.01)) for k, v in GREY.items()}})
    return pd.DataFrame(rows)


def test_scene_wide_shift_is_not_a_change_when_normalised():
    obs = _many_roofs()
    shifted = obs["date"].isin(obs["date"].unique()[12:14])
    for b in ["blue", "green", "red", "nir"]:
        obs.loc[shifted, b] *= 1.6  # haze or illumination on two dates, all roofs
    assert len(detect_changes(obs, normalize=False)) > 0
    assert detect_changes(obs, normalize=True).empty


def test_single_roof_change_survives_normalisation():
    obs = _many_roofs()
    late = (obs["roof_id"] == 7) & (obs["date"] >= obs["date"].unique()[14])
    for b, v in METAL.items():
        obs.loc[late, b] = v
    ch = detect_changes(obs, normalize=True)
    assert ch["roof_id"].tolist() == [7]
    assert ch.iloc[0]["change_type"] == "bright_new_roof"


def test_short_lived_departure_within_span_is_not_a_change():
    # two deviating dates 3 days apart (one weather episode), then back to normal
    rows = _series([GREY] * 15, step_days=10).to_dict("records")
    last = rows[-1]["date"]
    for k, d in enumerate([3, 6]):
        rows.append({"roof_id": 1, "date": last + pd.Timedelta(days=d), **METAL})
    for k in range(5):
        rows.append({"roof_id": 1, "date": last + pd.Timedelta(days=10 + 10 * k), **GREY})
    assert detect_changes(pd.DataFrame(rows), min_span_days=10).empty


def test_change_persisting_over_span_is_confirmed_at_span_end():
    obs = _series([GREY] * 15 + [METAL] * 4, step_days=5)
    ch = detect_changes(obs, min_span_days=10)
    assert len(ch) == 1
    assert ch.iloc[0]["date"] == obs["date"].iloc[15]
    assert ch.iloc[0]["confirmed_on"] == obs["date"].iloc[17]


def test_change_not_confirmed_before_span_has_elapsed():
    obs = _series([GREY] * 15 + [METAL] * 2, step_days=3)
    assert detect_changes(obs, min_span_days=10).empty


def test_normalisation_is_per_scene_not_per_date():
    obs = _many_roofs()
    # split roofs into two passes on every date; pass B is hazier on two dates
    obs["scene_id"] = np.where(obs["roof_id"] % 2 == 0, "A", "B") + obs["date"].dt.strftime("%Y%m%d")
    hazy = (obs["roof_id"] % 2 == 1) & obs["date"].isin(obs["date"].unique()[12:15])
    for b in ["blue", "green", "red", "nir"]:
        obs.loc[hazy, b] *= 1.6
    assert detect_changes(obs, normalize=True, min_roofs_per_date=20).empty
