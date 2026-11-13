import numpy as np
import pandas as pd
import pytest

from roofwatch.features import add_indices, roof_features, season_of


def _obs(roof_id, date, **bands):
    base = dict(blue=0.1, green=0.12, red=0.14, nir=0.2, rededge1=0.15, rededge2=0.18, rededge3=0.19,
                nir08=0.2, swir16=0.25, swir22=0.2, red_std=0.01, nir_std=0.02, n_clear=5)
    base.update(bands)
    return {"roof_id": roof_id, "date": pd.Timestamp(date), **base}


def test_season_of():
    assert season_of(pd.Series(pd.to_datetime(["2026-04-01", "2026-09-30", "2026-10-01", "2026-03-31"]))).tolist() == [
        "summer", "summer", "winter", "winter"]


def test_add_indices_values():
    df = pd.DataFrame([{"blue": 0.1, "green": 0.1, "red": 0.1, "nir": 0.3, "swir16": 0.2, "swir22": 0.1}])
    out = add_indices(df)
    assert out.loc[0, "brightness"] == pytest.approx(0.15)
    assert out.loc[0, "ndvi"] == pytest.approx(0.5)
    assert out.loc[0, "swir_ratio"] == pytest.approx(2.0)
    assert out.loc[0, "vis_slope"] == pytest.approx(0.0)


def test_roof_features_seasonal_medians_and_amplitude():
    obs = pd.DataFrame([
        _obs(1, "2026-06-01", red=0.10), _obs(1, "2026-07-01", red=0.20), _obs(1, "2026-08-01", red=0.30),
        _obs(1, "2026-01-01", red=0.40), _obs(1, "2025-12-01", red=0.40),
        _obs(1, "2024-06-01", red=0.99),  # outside the 12-month window
    ])
    tiers = pd.Series({1: "A"})
    f = roof_features(obs, tiers, end=pd.Timestamp("2026-11-01")).set_index("roof_id")
    assert f.loc[1, "red_summer"] == pytest.approx(0.20)
    assert f.loc[1, "red_winter"] == pytest.approx(0.40)
    assert f.loc[1, "n_dates"] == 5
    assert f.loc[1, "brightness_amplitude"] == pytest.approx(f.loc[1, "brightness_summer"] - f.loc[1, "brightness_winter"])


def test_roof_features_blank_20m_features_for_tier_b():
    obs = pd.DataFrame([_obs(2, "2026-06-01"), _obs(2, "2026-01-01")])
    f = roof_features(obs, pd.Series({2: "B"}), end=pd.Timestamp("2026-11-01")).set_index("roof_id")
    assert np.isnan(f.loc[2, "swir16_summer"])
    assert np.isnan(f.loc[2, "swir_ratio_winter"])
    assert not np.isnan(f.loc[2, "red_summer"])


def test_first_built_year_is_first_epoch_reaching_half_of_final():
    from roofwatch.features import first_built_year

    t = pd.DataFrame({1975: [0, 0, 800], 1980: [0, 0, 900], 1985: [500, 0, 1000], 1990: [1000, 0, 1000]})
    out = first_built_year(t, frac=0.5)
    assert out.iloc[0] == 1985
    assert np.isnan(out.iloc[1])
    assert out.iloc[2] == 1975
