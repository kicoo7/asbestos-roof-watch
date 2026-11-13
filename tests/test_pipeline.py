import pandas as pd

from roofwatch.pipeline import clean_labels


def test_clean_labels_marks_ac_roofs_that_changed_after_label_date():
    labels = pd.DataFrame({
        "label": ["ac", "ac", "ac", "none"],
        "label_date": pd.to_datetime(["2018-01-01", "2022-01-01", None, None]),
    }, index=pd.Index([1, 2, 3, 4], name="roof_id"))
    events = pd.DataFrame({"roof_id": [1, 2, 3, 4], "date": pd.to_datetime(["2020-06-01", "2020-06-01", "2019-01-01", "2020-01-01"])})
    out = clean_labels(labels, events)
    assert out.tolist() == ["ac_changed_since_label", "ac", "ac_changed_since_label", "none"]


def test_clean_labels_without_events_keeps_labels():
    labels = pd.DataFrame({"label": ["ac"], "label_date": pd.to_datetime(["2018-01-01"])}, index=[1])
    assert clean_labels(labels, pd.DataFrame()).tolist() == ["ac"]


def test_feature_matrix_excludes_acquisition_count_and_filters_sparse_roofs():
    import numpy as np
    from roofwatch.observe import BANDS
    from roofwatch.pipeline import MIN_DATES, feature_matrix

    rows = []
    for rid, n in [(1, MIN_DATES), (2, MIN_DATES - 1)]:
        for i in range(n):
            rows.append({"roof_id": rid, "date": pd.Timestamp("2026-01-05") + pd.Timedelta(days=20 * i),
                         **{b: 0.2 for b in BANDS}, "red_std": 0.01, "nir_std": 0.01})
    obs = pd.DataFrame(rows)
    labels = pd.DataFrame({"tier": ["A", "A"]}, index=pd.Index([1, 2], name="roof_id"))
    ctx = pd.DataFrame({"roof_id": [1, 2], "area_m2": [3000.0, 3000.0], "built_year": [1975.0, 1975.0],
                        "built_2020_m2": [5000.0, 5000.0], "compactness": [0.7, 0.7], "class": [None, None], "subtype": [None, None]})
    X = feature_matrix(obs, labels, ctx, end=pd.Timestamp("2026-11-01"))
    assert list(X.index) == [1]
    assert "n_dates" not in X.columns


def test_load_all_observations_tie_break_is_deterministic(tmp_path):
    from roofwatch.pipeline import load_all_observations
    from roofwatch.store import append_observations

    d = pd.Timestamp("2026-09-12")
    a = pd.DataFrame({"roof_id": [1, 1], "scene_id": ["S2B_x", "S2A_x"], "date": [d, d], "n_clear": [4, 4], "red": [0.2, 0.3]})
    append_observations(tmp_path / "observations.parquet", a)
    first = load_all_observations(tmp_path)
    (tmp_path / "observations.parquet").unlink()
    append_observations(tmp_path / "observations.parquet", a.iloc[::-1])
    second = load_all_observations(tmp_path)
    assert first["scene_id"].tolist() == second["scene_id"].tolist() == ["S2A_x"]


def test_clean_labels_ignores_events_confirmed_after_end():
    labels = pd.DataFrame({"label": ["ac"], "label_date": pd.to_datetime(["2018-01-01"])}, index=pd.Index([1], name="roof_id"))
    events = pd.DataFrame({"roof_id": [1], "date": pd.to_datetime(["2025-06-01"]), "confirmed_on": pd.to_datetime(["2025-06-20"])})
    assert clean_labels(labels, events, end=pd.Timestamp("2025-01-01")).tolist() == ["ac"]
    assert clean_labels(labels, events, end=pd.Timestamp("2026-01-01")).tolist() == ["ac_changed_since_label"]
