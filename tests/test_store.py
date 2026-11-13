import pandas as pd

from roofwatch.store import append_observations, load_observations


def _obs(scene, roofs):
    return pd.DataFrame({
        "roof_id": roofs, "scene_id": scene, "date": pd.Timestamp("2026-06-01"),
        "red": 0.1, "n_clear": 4,
    })


def test_append_is_idempotent_per_roof_and_scene(tmp_path):
    p = tmp_path / "observations.parquet"
    append_observations(p, _obs("S1", [1, 2]))
    append_observations(p, _obs("S1", [1, 2]))
    append_observations(p, _obs("S2", [1]))
    df = load_observations(p)
    assert len(df) == 3
    assert sorted(zip(df.roof_id, df.scene_id)) == [(1, "S1"), (1, "S2"), (2, "S1")]


def test_load_missing_file_returns_empty(tmp_path):
    assert load_observations(tmp_path / "none.parquet").empty
