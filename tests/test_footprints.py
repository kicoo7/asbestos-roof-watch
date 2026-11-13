import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, box

from roofwatch.footprints import join_labels, label_coverage

UTM = "EPSG:32632"


def _roofs():
    return gpd.GeoDataFrame(
        {"roof_id": [0, 1, 2, 3], "area_m2": [3600.0, 3600.0, 3600.0, 3600.0]},
        geometry=[box(0, 0, 60, 60), box(100, 0, 160, 60), box(200, 0, 260, 60), box(300, 0, 360, 60)],
        crs=UTM,
    )


def _labels(rows):
    return gpd.GeoDataFrame(
        pd.DataFrame(rows, columns=["label", "mq_stimati", "data_sopr", "x", "y"]),
        geometry=[Point(r[3], r[4]) for r in rows],
        crs=UTM,
    )


def test_join_labels_inside_and_nearby():
    labels = _labels([
        ("ac", 3000, pd.Timestamp("2016-01-01"), 30, 30),      # inside roof 0
        ("removed", 3000, pd.Timestamp("2020-01-01"), 170, 30),  # 10 m east of roof 1
        ("ac", 3000, pd.Timestamp("2016-01-01"), 500, 500),    # far from everything
    ])
    out = join_labels(_roofs(), labels, max_dist_m=15).set_index("roof_id")
    assert out.loc[0, "label"] == "ac"
    assert out.loc[1, "label"] == "removed"
    assert out.loc[1, "label_date"] == pd.Timestamp("2020-01-01")
    assert out.loc[2, "label"] == "none"


def test_join_labels_small_ac_patch_on_big_roof_is_excluded():
    labels = _labels([("ac", 400, pd.NaT, 30, 30)])  # 400 of 3600 m2
    out = join_labels(_roofs(), labels, max_dist_m=15, min_ac_fraction=0.5).set_index("roof_id")
    assert out.loc[0, "label"] == "ac_small_part"


def test_join_labels_mixed_ac_and_removed_is_partial():
    labels = _labels([
        ("ac", 3000, pd.NaT, 10, 10),
        ("removed", 3000, pd.NaT, 50, 50),
    ])
    out = join_labels(_roofs(), labels, max_dist_m=15).set_index("roof_id")
    assert out.loc[0, "label"] == "partial"


def test_join_labels_marks_unverified_candidates():
    labels = _labels([("candidate", 3000, pd.NaT, 330, 30)])
    out = join_labels(_roofs(), labels, max_dist_m=15).set_index("roof_id")
    assert out.loc[3, "label"] == "candidate"


def test_label_coverage_counts_points_matched_to_a_roof():
    labels = _labels([
        ("ac", 3000, pd.NaT, 30, 30),
        ("ac", 3000, pd.NaT, 170, 30),
        ("ac", 3000, pd.NaT, 500, 500),
        ("ac", 3000, pd.NaT, 520, 500),
    ])
    assert label_coverage(_roofs(), labels, max_dist_m=15) == 0.5


def test_label_coverage_zero_distance_means_strictly_inside():
    labels = _labels([("ac", 3000, pd.NaT, 30, 30), ("ac", 3000, pd.NaT, 170, 30)])
    assert label_coverage(_roofs(), labels, max_dist_m=0) == 0.5
