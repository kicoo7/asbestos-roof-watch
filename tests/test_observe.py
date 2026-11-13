import numpy as np
import pandas as pd
import pytest

from roofwatch.observe import BANDS, clear_mask, roof_stats, to_reflectance, scale_offset


def test_to_reflectance_applies_scale_offset_and_nodata():
    dn = np.array([0, 1000, 2000], dtype=np.uint16)
    out = to_reflectance(dn, scale=0.0001, offset=-0.1, nodata=0)
    assert np.isnan(out[0])
    assert out[1] == pytest.approx(0.0, abs=1e-6)
    assert out[2] == pytest.approx(0.1, abs=1e-6)


def test_scale_offset_prefers_asset_metadata():
    assert scale_offset({"scale": 0.0001, "offset": -0.1}, "05.10") == (0.0001, -0.1)


def test_scale_offset_falls_back_to_processing_baseline():
    # Baseline 04.00+ (Jan 2022) carries a +1000 DN offset; older baselines do not.
    assert scale_offset(None, "04.00") == (0.0001, -0.1)
    assert scale_offset(None, "03.01") == (0.0001, 0.0)


def test_clear_mask_keeps_only_surface_classes():
    scl = np.arange(12)
    # 4 vegetation, 5 not vegetated, 6 water, 7 unclassified
    assert np.flatnonzero(clear_mask(scl)).tolist() == [4, 5, 6, 7]


def test_roof_stats_medians_over_clear_pixels_only():
    shape = (2, 3)
    bands = {b: np.full(shape, 0.2) for b in BANDS}
    bands["red"] = np.array([[0.1, 0.2, 0.9], [0.3, 0.5, 0.5]])
    bands["nir"] = np.array([[0.2, 0.4, 0.9], [0.6, 0.5, 0.5]])
    scl = np.array([[5, 5, 9], [5, 5, 5]])  # pixel (0,2) is cloud
    lut = {7: (np.array([0, 0, 0, 1]), np.array([0, 1, 2, 0])),
           8: (np.array([1]), np.array([2]))}
    df = roof_stats(bands, scl, lut, min_clear_fraction=0.5)
    r7 = df.set_index("roof_id").loc[7]
    assert r7["n_px"] == 4 and r7["n_clear"] == 3
    assert r7["red"] == pytest.approx(0.2)
    assert r7["nir"] == pytest.approx(0.4)
    assert r7["red_std"] == pytest.approx(np.std([0.1, 0.2, 0.3]))
    assert set(df["roof_id"]) == {7, 8}


def test_roof_stats_drops_roofs_below_clear_fraction():
    shape = (1, 4)
    bands = {b: np.full(shape, 0.2) for b in BANDS}
    scl = np.array([[5, 9, 9, 9]])
    lut = {1: (np.array([0, 0, 0, 0]), np.array([0, 1, 2, 3]))}
    assert roof_stats(bands, scl, lut, min_clear_fraction=0.8).empty


def test_roof_stats_skips_roofs_with_no_pixels():
    shape = (1, 1)
    bands = {b: np.full(shape, 0.2) for b in BANDS}
    lut = {1: (np.array([], dtype=int), np.array([], dtype=int))}
    assert roof_stats(bands, np.array([[5]]), lut).empty
