"""T1.3 label cleaning, T1.5 spectra plot, T1.7 grouped classifier, T1.8 go/no-go -> docs/RESULTS.md."""
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from roofwatch.change import detect_changes
from roofwatch.pipeline import clean_labels, feature_matrix, load_all_observations
from roofwatch.model import grouped_oof_predictions, lgbm_fit_predict, precision_at_recall
from roofwatch.observe import BANDS
from roofwatch.store import load_observations

END = pd.Timestamp(sys.argv[1]) if len(sys.argv) > 1 else pd.Timestamp("2026-11-13")
OUT = ROOT / "outputs"
inv = gpd.read_file(ROOT / "data/processed/roofs_inventory.gpkg", layer="roofs")
ctx = pd.read_parquet(ROOT / "data/processed/context.parquet")
obs = load_observations(OUT / "observations.parquet")
allobs = load_all_observations(OUT)

# --- T1.3: drop AC labels whose roof changed after the label date -------------------------
ev = detect_changes(allobs)
labels = inv.set_index("roof_id")[["label", "label_date", "tier", "comune"]].copy()
labels["label_date"] = pd.to_datetime(labels["label_date"])
labels["clean_label"] = clean_labels(labels, ev)
label_table = pd.crosstab(labels["clean_label"], labels["tier"], margins=True)

# --- features -----------------------------------------------------------------------------
X = feature_matrix(obs, labels, ctx, END)
lab = labels["clean_label"].reindex(X.index)
train = X[lab.isin(["ac", "none"])]
y = (lab.reindex(train.index) == "ac").astype(int).values
groups = labels["comune"].reindex(train.index).values
tier = labels["tier"].reindex(train.index).values

# --- T1.5: spectra ----------------------------------------------------------------------
fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
for ax, season in zip(axes, ["summer", "winter"]):
    for name, color in [("ac", "#c0392b"), ("none", "#2c7fb8"), ("removed", "#7f8c8d")]:
        sel = X[(labels["clean_label"].reindex(X.index) == name) & (X["tier_a"] == 1)]
        cols = [f"{b}_{season}" for b in BANDS]
        q = sel[cols].quantile([0.25, 0.5, 0.75])
        ax.plot(range(len(BANDS)), q.loc[0.5], color=color, label=f"{name} (n={len(sel)})")
        ax.fill_between(range(len(BANDS)), q.loc[0.25], q.loc[0.75], color=color, alpha=0.15)
    ax.set_xticks(range(len(BANDS)), BANDS, rotation=45, ha="right")
    ax.set_title(f"Tier A roofs, {season} median reflectance (IQR shaded)")
axes[0].set_ylabel("surface reflectance"); axes[0].legend()
fig.tight_layout(); fig.savefig(OUT / "spectra_ac_vs_other.png", dpi=120)

single_auc = {}
for c in train.columns:
    v = train[c]
    if v.dtype.kind in "fi" and v.notna().sum() > 50:
        m = v.notna().values
        single_auc[c] = roc_auc_score(y[m], v.values[m])
single = pd.Series(single_auc).map(lambda a: max(a, 1 - a)).sort_values(ascending=False)

# --- T1.7: grouped classifier ---------------------------------------------------------------
spectral = [c for c in train.columns if c not in ("area_m2", "built_year", "built_2020_m2", "compactness", "class", "subtype", "tier_a")]
context = ["area_m2", "built_year", "built_2020_m2", "compactness", "class", "subtype", "tier_a"]
models = {"all features": list(train.columns), "spectral only": spectral, "context only": context}
rows, curves = [], {}
for name, cols in models.items():
    oof = np.mean([grouped_oof_predictions(train[cols], y, groups, n_splits=5,
                   fit_predict=lambda a, b, c, d, e, s=s: lgbm_fit_predict(a, b, c, seed=s))
                   for s in range(3)], axis=0)
    for t in ("A", "B", "all"):
        m = np.ones(len(y), bool) if t == "all" else tier == t
        yy, pp = y[m], oof[m]
        rows.append({"model": name, "tier": t, "n_ac": int(yy.sum()), "n_other": int((1 - yy).sum()),
                     "base_rate": yy.mean(), "avg_precision": average_precision_score(yy, pp),
                     "precision@recall0.5": precision_at_recall(yy, pp, 0.5),
                     "precision@recall0.3": precision_at_recall(yy, pp, 0.3)})
        curves[(name, t)] = precision_recall_curve(yy, pp)
    if name == "all features":
        best_oof = oof
res = pd.DataFrame(rows)
fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
for ax, t in zip(axes, ["A", "B"]):
    for name in models:
        p, r, _ = curves[(name, t)]
        ax.plot(r, p, label=name)
    br = res[(res.model == "all features") & (res.tier == t)]["base_rate"].iloc[0]
    ax.axhline(br, color="grey", ls=":", label=f"base rate {br:.2f}")
    ax.plot([0.5], [0.6], "k*", ms=10, label="go/no-go bar")
    ax.set_title(f"Tier {t}: precision-recall, held-out municipalities"); ax.set_xlabel("recall")
axes[0].set_ylabel("precision"); axes[0].legend(fontsize=8)
fig.tight_layout(); fig.savefig(OUT / "pr_curves.png", dpi=120)

pd.DataFrame({"roof_id": train.index, "y": y, "p_oof": best_oof, "tier": tier, "comune": groups}).to_parquet(OUT / "oof_predictions.parquet")
res.to_csv(OUT / "classifier_results.csv", index=False)
label_table.to_csv(OUT / "label_table.csv")
single.head(15).to_csv(OUT / "single_feature_auc.csv")
print(label_table); print(res.round(3).to_string(index=False)); print(single.head(12).round(3))
print("observation dates per roof (12 m):", X["n_dates"].describe().round(1).to_dict())
print("change events used for label cleaning:", len(ev))
