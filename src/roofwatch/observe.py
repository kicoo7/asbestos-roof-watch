"""Per-roof Sentinel-2 observations: reflectance scaling, SCL masking, roof statistics (PLAN 4.5 step 2)."""
from __future__ import annotations

import numpy as np
import pandas as pd

BANDS_10M = ["blue", "green", "red", "nir"]
BANDS_20M = ["rededge1", "rededge2", "rededge3", "nir08", "swir16", "swir22"]
BANDS = BANDS_10M + BANDS_20M
STD_BANDS = ["red", "nir"]  # within-roof texture

# SCL: 4 vegetation, 5 not vegetated, 6 water, 7 unclassified.
# Excluded: 0 nodata, 1 saturated, 2 dark/cast shadow, 3 cloud shadow, 8-10 cloud/cirrus, 11 snow.
CLEAR_SCL = (4, 5, 6, 7)


def scale_offset(raster_band: dict | None, processing_baseline: str | None) -> tuple[float, float]:
    """Scale and offset to turn L2A digital numbers into reflectance.

    Uses the asset's own `raster:bands` metadata when present. Otherwise applies the
    rule that processing baseline 04.00 and later carry a +1000 DN offset.
    """
    if raster_band and "scale" in raster_band:
        return float(raster_band["scale"]), float(raster_band.get("offset", 0.0))
    major = float(processing_baseline or "0")
    return 0.0001, (-0.1 if major >= 4.0 else 0.0)


def to_reflectance(dn: np.ndarray, scale: float, offset: float, nodata: int | None = 0) -> np.ndarray:
    out = dn.astype("float32") * scale + offset
    if nodata is not None:
        out[dn == nodata] = np.nan
    return out


def clear_mask(scl: np.ndarray) -> np.ndarray:
    return np.isin(scl, CLEAR_SCL)


def roof_stats(
    bands: dict[str, np.ndarray],
    scl: np.ndarray,
    lut: dict[int, tuple[np.ndarray, np.ndarray]],
    min_clear_fraction: float = 0.8,
) -> pd.DataFrame:
    """One row per roof with enough clear pixels: median reflectance per band, texture, pixel counts."""
    clear = clear_mask(scl)
    for b in BANDS:
        clear &= ~np.isnan(bands[b])
    rows = []
    for roof_id, (r, c) in lut.items():
        n_px = len(r)
        if n_px == 0:
            continue
        keep = clear[r, c]
        n_clear = int(keep.sum())
        if n_clear == 0 or n_clear / n_px < min_clear_fraction:
            continue
        rr, cc = r[keep], c[keep]
        row = {"roof_id": roof_id, "n_px": n_px, "n_clear": n_clear}
        for b in BANDS:
            row[b] = float(np.median(bands[b][rr, cc]))
        for b in STD_BANDS:
            row[f"{b}_std"] = float(np.std(bands[b][rr, cc]))
        rows.append(row)
    cols = ["roof_id", "n_px", "n_clear", *BANDS, *[f"{b}_std" for b in STD_BANDS]]
    return pd.DataFrame(rows, columns=cols)
