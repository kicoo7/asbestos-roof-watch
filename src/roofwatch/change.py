"""Per-roof change detection against a rolling baseline (PLAN 4.5 step 4, T2.1, T2.3)."""
from __future__ import annotations

import numpy as np
import pandas as pd

CHANGE_BANDS = ["blue", "green", "red", "nir"]  # 10 m bands, usable for both tiers

# Operating point used by label cleaning and the weekly run; chosen in T2.2 (docs/RESULTS.md).
DETECT_PARAMS = dict(normalize=True, rel_floor=0.15, threshold=4.0, min_span_days=10)


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
    min_span_days: int = 10,
) -> pd.DataFrame:
    """Change events: a roof departs from its own baseline and stays departed.

    `obs` holds clear observations only. The baseline is the median of up to `window`
    earlier observations in the current state (at least `min_history`); the scale is the
    larger of 1.4826 x MAD and `floor`. A departure (max band |z| > threshold) is confirmed
    when every following clear observation departs in the same direction until one at least
    `min_span_days` later (so at least 2 clear dates, never two looks at one weather episode).
    After a change the baseline restarts from the new state: one new state, one event.

    With `normalize`, each band is divided by the median over all roofs of the same scene
    (`scene_id`, else `date`); groups with fewer than `min_roofs_per_date` roofs are skipped.
    Haze, illumination and seasonal shifts shared by a pass cancel out; the floor is then
    `rel_floor` (a fraction). Before/after values and change types stay in reflectance.
    """
    extra = [b for b in ("swir16", "swir22") if b in obs.columns]
    obs = obs.sort_values(["date"] + (["scene_id"] if "scene_id" in obs.columns else []), kind="stable")
    if normalize:
        key = "scene_id" if "scene_id" in obs.columns else "date"
        counts = obs.groupby(key)["roof_id"].transform("size")
        obs = obs[counts >= min_roofs_per_date]
        med = obs.groupby(key)[bands].transform("median")
        obs = obs.assign(**{f"n_{b}": obs[b] / med[b] for b in bands})
        score_bands, floor = [f"n_{b}" for b in bands], rel_floor
    else:
        score_bands = bands
    span = np.timedelta64(int(min_span_days), "D")
    events = []
    for roof_id, g in obs.groupby("roof_id", sort=False):
        X = g[score_bands].to_numpy(float)
        RAW = g[bands + extra].to_numpy(float)
        dates = g["date"].to_numpy()
        n, i, state_start = len(g), 0, 0
        while i < n:
            hist = X[max(state_start, i - window):i]
            if len(hist) < min_history:
                i += 1
                continue
            base = np.nanmedian(hist, axis=0)
            scale = np.maximum(1.4826 * np.nanmedian(np.abs(hist - base), axis=0), floor)

            def dev(k):
                d = X[k] - base
                return float(np.nanmax(np.abs(d / scale))), float(np.sign(np.nanmean(d)))

            z0, s0 = dev(i)
            if not z0 > threshold:
                i += 1
                continue
            j, confirmed, zmin = i, False, z0
            while j + 1 < n:
                j += 1
                zj, sj = dev(j)
                if not (zj > threshold and sj == s0):
                    break
                zmin = min(zmin, zj)
                if dates[j] - dates[i] >= span:
                    confirmed = True
                    break
            if not confirmed:
                i += 1
                continue
            raw_hist = RAW[max(state_start, i - window):i]
            before = dict(zip(bands + extra, np.nanmedian(raw_hist, axis=0)))
            after = dict(zip(bands + extra, np.nanmean(RAW[i:j + 1], axis=0)))
            events.append({
                "roof_id": roof_id, "date": pd.Timestamp(dates[i]), "confirmed_on": pd.Timestamp(dates[j]),
                "score": zmin, "change_type": classify_change(before, after),
                **{f"{b}_before": before[b] for b in bands + extra},
                **{f"{b}_after": after[b] for b in bands + extra},
            })
            state_start, i = i, j + 1
    cols = ["roof_id", "date", "confirmed_on", "score", "change_type",
            *[f"{b}_before" for b in bands + extra], *[f"{b}_after" for b in bands + extra]]
    return pd.DataFrame(events, columns=cols)
