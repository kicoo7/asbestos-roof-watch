import numpy as np
import pandas as pd
import pytest

from roofwatch.model import grouped_oof_predictions, precision_at_recall


def test_precision_at_recall_picks_best_precision_meeting_recall():
    y = np.array([1, 1, 1, 1, 0, 0, 0, 0])
    p = np.array([0.9, 0.8, 0.3, 0.2, 0.85, 0.1, 0.05, 0.01])
    # thresholds meeting recall >= .5: 0.8 -> 2/3, 0.3 -> 3/4, 0.2 -> 4/5 (best), 0.1 -> 4/6
    assert precision_at_recall(y, p, 0.5) == pytest.approx(0.8)


def test_precision_at_recall_nan_without_positives():
    assert np.isnan(precision_at_recall(np.array([0, 0]), np.array([0.1, 0.2]), 0.5))


def test_grouped_oof_never_trains_on_the_test_group():
    rng = np.random.default_rng(0)
    n = 400
    groups = np.repeat([f"m{i}" for i in range(8)], n // 8)
    X = pd.DataFrame({"x": rng.normal(size=n)})
    y = (X["x"] > 0).astype(int).values
    seen = []

    def fit_predict(Xtr, ytr, Xte, gtr, gte):
        seen.append((set(gtr), set(gte)))
        return np.full(len(Xte), 0.5)

    oof = grouped_oof_predictions(X, y, groups, n_splits=4, fit_predict=fit_predict)
    assert len(oof) == n and not np.isnan(oof).any()
    assert all(not (tr & te) for tr, te in seen)


def test_grouped_oof_learns_separable_signal():
    rng = np.random.default_rng(1)
    n = 800
    groups = np.repeat([f"m{i}" for i in range(10)], n // 10)
    X = pd.DataFrame({"x": rng.normal(size=n), "noise": rng.normal(size=n)})
    y = (X["x"] > 1).astype(int).values
    oof = grouped_oof_predictions(X, y, groups, n_splits=5)
    assert precision_at_recall(y, oof, 0.5) > 0.9
