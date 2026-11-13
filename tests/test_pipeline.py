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
