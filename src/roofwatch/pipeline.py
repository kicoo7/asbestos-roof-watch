"""Shared steps for training, scoring and the weekly run."""
from __future__ import annotations

import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from .features import roof_features
from .model import grouped_oof_predictions, lgbm_fit_predict
from .store import load_observations

CONTEXT_COLS = ["area_m2", "built_year", "built_2020_m2", "compactness", "class", "subtype"]
MIN_DATES = 6


def clean_labels(labels: pd.DataFrame, events: pd.DataFrame) -> pd.Series:
    """'ac' roofs with a detected change after their label date (or with no date) become 'ac_changed_since_label'."""
    out = labels["label"].copy()
    if events is None or events.empty:
        return out
    ev = events.join(labels["label_date"], on="roof_id")
    hit = ev[ev["label_date"].isna() | (ev["date"] > ev["label_date"])]["roof_id"].unique()
    out[(out == "ac") & out.index.isin(hit)] = "ac_changed_since_label"
    return out


def load_all_observations(outputs: Path) -> pd.DataFrame:
    """Recent and archive observations, one row per roof per date (the clearest scene wins)."""
    frames = [load_observations(Path(outputs) / f) for f in ("observations_archive.parquet", "observations.parquet")]
    obs = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    obs = obs.drop_duplicates(["roof_id", "scene_id"])
    return obs.sort_values("n_clear", ascending=False).drop_duplicates(["roof_id", "date"]).sort_values(["roof_id", "date"])


def feature_matrix(obs: pd.DataFrame, labels: pd.DataFrame, ctx: pd.DataFrame, end: pd.Timestamp) -> pd.DataFrame:
    feat = roof_features(obs, labels["tier"], end=end).set_index("roof_id")
    X = feat.join(ctx.set_index("roof_id")[CONTEXT_COLS])
    X["tier_a"] = (labels["tier"].reindex(X.index) == "A").astype(int)
    for c in ("class", "subtype"):
        X[c] = X[c].astype("category")
    return X[X["n_dates"] >= MIN_DATES]


def train_and_score(X: pd.DataFrame, y: np.ndarray, train_idx: pd.Index, groups, seeds=(0, 1, 2)) -> pd.Series:
    """Calibrated AC probability for every roof in X.

    Training roofs get out-of-fold scores (grouped by municipality); all other roofs get the
    full model's score. Raw scores are mapped to probabilities by isotonic regression fitted
    on the out-of-fold scores.
    """
    Xtr = X.loc[train_idx]
    oof = np.mean([grouped_oof_predictions(Xtr, y, groups, n_splits=5,
                   fit_predict=lambda a, b, c, d, e, s=s: lgbm_fit_predict(a, b, c, seed=s)) for s in seeds], axis=0)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(oof, y)
    rest = X.index.difference(train_idx)
    raw_rest = np.mean([lgbm_fit_predict(Xtr, y, X.loc[rest], seed=s) for s in seeds], axis=0) if len(rest) else []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        p = pd.concat([pd.Series(iso.predict(oof), index=train_idx), pd.Series(iso.predict(raw_rest), index=rest)])
    return p.rename("ac_probability")
