"""AC classifier with municipality-grouped evaluation (PLAN 4.3)."""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.metrics import precision_recall_curve
from sklearn.model_selection import GroupKFold


def precision_at_recall(y, p, min_recall: float) -> float:
    y = np.asarray(y)
    if y.sum() == 0:
        return float("nan")
    prec, rec, _ = precision_recall_curve(y, p)
    ok = rec >= min_recall
    return float(prec[ok].max()) if ok.any() else float("nan")


def lgbm_fit_predict(Xtr, ytr, Xte, gtr=None, gte=None, seed: int = 0):
    import lightgbm as lgb

    pos = max(int(np.sum(ytr)), 1)
    clf = lgb.LGBMClassifier(
        n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=10,
        subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
        scale_pos_weight=(len(ytr) - pos) / pos, random_state=seed, verbose=-1,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        clf.fit(Xtr, ytr)
        return clf.predict_proba(Xte)[:, 1]


def grouped_oof_predictions(X: pd.DataFrame, y, groups, n_splits: int = 5, fit_predict=lgbm_fit_predict):
    """Out-of-fold probabilities where each fold holds out whole groups (municipalities)."""
    y = np.asarray(y)
    groups = np.asarray(groups)
    oof = np.full(len(y), np.nan)
    for tr, te in GroupKFold(n_splits=n_splits).split(X, y, groups):
        oof[te] = fit_predict(X.iloc[tr], y[tr], X.iloc[te], groups[tr], groups[te])
    return oof
