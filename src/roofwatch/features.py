"""Per-roof feature table for the AC classifier (PLAN 4.2)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .observe import BANDS, BANDS_20M

INDICES = ["brightness", "ndvi", "swir_ratio", "vis_slope", "ndbi"]
INDICES_20M = ["swir_ratio", "ndbi"]


def season_of(dates: pd.Series) -> pd.Series:
    return np.where(dates.dt.month.between(4, 9), "summer", "winter")


def _nd(a, b):
    return (a - b) / (a + b)


def add_indices(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["brightness"] = out[["blue", "green", "red", "nir"]].mean(axis=1)
    out["ndvi"] = _nd(out["nir"], out["red"])
    out["vis_slope"] = _nd(out["red"], out["blue"])
    if "swir16" in out:
        out["swir_ratio"] = out["swir16"] / out["swir22"]
        out["ndbi"] = _nd(out["swir16"], out["nir"])
    return out


def roof_features(obs: pd.DataFrame, tiers: pd.Series, end: pd.Timestamp, months: int = 12) -> pd.DataFrame:
    """Seasonal median spectra and indices over the `months` before `end`, plus temporal and texture stats."""
    start = end - pd.DateOffset(months=months)
    w = obs[(obs["date"] > start) & (obs["date"] <= end)].copy()
    w = add_indices(w)
    w["season"] = season_of(w["date"])
    cols = BANDS + INDICES
    seas = w.groupby(["roof_id", "season"])[cols].median().unstack("season")
    seas.columns = [f"{c}_{s}" for c, s in seas.columns]
    seas = seas.reindex(columns=[f"{c}_{s}" for s in ("summer", "winter") for c in cols])
    g = w.groupby("roof_id")
    f = seas.join(g.size().rename("n_dates"))
    f["brightness_amplitude"] = f["brightness_summer"] - f["brightness_winter"]
    f["ndvi_amplitude"] = f["ndvi_summer"] - f["ndvi_winter"]
    f["brightness_iqr"] = g["brightness"].quantile(0.75) - g["brightness"].quantile(0.25)
    f["red_std_med"] = g["red_std"].median()
    f["nir_std_med"] = g["nir_std"].median()
    tier = tiers.reindex(f.index)
    blank = [c for c in f.columns if any(c.startswith(p + "_") for p in BANDS_20M + INDICES_20M)]
    f.loc[tier == "B", blank] = np.nan
    return f.reset_index()
