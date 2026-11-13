"""Per-roof change detection against a rolling baseline (PLAN 4.5 step 4, T2.1, T2.3)."""
from __future__ import annotations

import numpy as np
import pandas as pd

CHANGE_BANDS = ["blue", "green", "red", "nir"]  # 10 m bands, usable for both tiers

# Operating point used by label cleaning and the weekly run; chosen in T2.2 (docs/RESULTS.md).
DETECT_PARAMS = dict(normalize=True, rel_floor=0.1, threshold=4.0)


def _bright(s) -> float:
    return float(np.mean([s["blue"], s["green"], s["red"], s["nir"]]))


def _ndvi(s) -> float:
    return float((s["nir"] - s["red"]) / (s["nir"] + s["red"]))


def classify_change(before, after) -> str:
    """Rough type of a confirmed change, from the baseline and the new spectrum."""
    db = _bright(after) - _bright(before)
    if _ndvi(after) > 0.4 and _ndvi(after) - _ndvi(before) > 0.15:
        return "vegetation"
    if db <= -0.04 and _bright(after) < 0.12:
        return "dark_pv"
    if db >= 0.04:
        return "bright_new_roof"
    if "swir16" in before and "swir16" in after and not (pd.isna(before["swir16"]) or pd.isna(after["swir16"])):
        ndbi = lambda s: (s["swir16"] - s["nir"]) / (s["swir16"] + s["nir"])
        if ndbi(after) - ndbi(before) > 0.1 and _ndvi(after) < 0.3:
            return "demolition_bare"
    return "other"


def detect_changes(
    obs: pd.DataFrame,
    threshold: float = 3.0,
    min_history: int = 5,
    window: int = 8,
    floor: float = 0.02,
    bands=CHANGE_BANDS,
    normalize: bool = False,
    rel_floor: float = 0.1,
    min_roofs_per_date: int = 30,
) -> pd.DataFrame:
    """Change events: a roof departs from its own baseline on 2 consecutive clear dates.

    `obs` holds clear observations only (one row per roof per date). The baseline is the
    median of up to `window` earlier observations in the current state; the scale is the
    larger of 1.4826 x MAD and `floor` (reflectance units). After a change the baseline
    restarts from the new state, so one new state gives one event.

    With `normalize`, each band is divided by that date's median over all roofs before
    scoring (dates with fewer than `min_roofs_per_date` roofs are skipped), so haze,
    illumination and seasonal shifts shared by all roofs cancel out. The floor is then
    `rel_floor` (a fraction). Before/after values and change types stay in reflectance.
    """
    extra = [b for b in ("swir16", "swir22") if b in obs.columns]
    obs = obs.sort_values("date")
    if normalize:
        counts = obs.groupby("date")["roof_id"].transform("size")
        obs = obs[counts >= min_roofs_per_date]
        med = obs.groupby("date")[bands].transform("median")
        obs = obs.join((obs[bands] / med).add_prefix("n_"))
        score_bands, floor = [f"n_{b}" for b in bands], rel_floor
    else:
        score_bands = bands
    events = []
    for roof_id, g in obs.groupby("roof_id", sort=False):
        X = g[score_bands].to_numpy(float)
        dates = g["date"].to_numpy()
        state_start, i, n = 0, 0, len(g)
        z = np.zeros(n)
        signs = np.zeros(n)

        def score(j):
            hist = X[max(state_start, j - window):j]
            if len(hist) < min_history:
                return None
            base = np.median(hist, axis=0)
            scale = np.maximum(1.4826 * np.median(np.abs(hist - base), axis=0), floor)
            d = (X[j] - base) / scale
            return float(np.max(np.abs(d))), float(np.sign(np.mean(X[j] - base))), base

        while i < n - 1:
            s0 = score(i)
            if s0 is None or s0[0] <= threshold:
                i += 1
                continue
            s1 = score(i + 1)
            # second date compared to the same baseline as the first
            hist = X[max(state_start, i - window):i]
            base = s0[2]
            scale = np.maximum(1.4826 * np.median(np.abs(hist - base), axis=0), floor)
            z1 = float(np.max(np.abs((X[i + 1] - base) / scale)))
            sign1 = float(np.sign(np.mean(X[i + 1] - base)))
            if z1 > threshold and sign1 == s0[1]:
                rows = g.iloc[[i, i + 1]]
                hist_rows = g.iloc[max(state_start, i - window):i]
                before = {b: float(hist_rows[b].median()) for b in bands + extra}
                after = rows[bands + extra].mean().to_dict()
                events.append({
                    "roof_id": roof_id, "date": pd.Timestamp(dates[i]), "confirmed_on": pd.Timestamp(dates[i + 1]),
                    "score": min(s0[0], z1), "change_type": classify_change(before, after),
                    **{f"{b}_before": before[b] for b in bands + extra},
                    **{f"{b}_after": after[b] for b in bands + extra},
                })
                state_start = i
                i += 2
            else:
                i += 1
    return pd.DataFrame(events)
