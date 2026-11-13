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
