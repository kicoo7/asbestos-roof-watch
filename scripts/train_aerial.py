"""Aerial (AGEA 2024, ~0.42 m) asbestos classifier on DINOv2 embeddings, municipality-grouped CV.

-> outputs/aerial_results.csv, aerial_oof.parquet, aerial_pr_curves.png, aerial_top_predictions.png
"""
import sys
import warnings
from pathlib import Path

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from PIL import Image
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_recall_curve
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from roofwatch.model import grouped_oof_predictions, lgbm_fit_predict, precision_at_recall

OUT = ROOT / "outputs"
roofs = gpd.read_file(ROOT / "data/processed/lombardy_roofs.gpkg", layer="roofs")
emb = pd.read_parquet(ROOT / "data/processed/chip_embeddings.parquet").set_index("roof_id")
df = roofs.set_index("roof_id")[["comune", "label", "size_band", "area_m2"]].join(emb, how="inner")
df = df[df["label"].isin(["ac", "none"])]
ecols = [c for c in emb.columns]
X = df[ecols].copy()
X["log_area"] = np.log(df["area_m2"])
y = (df["label"] == "ac").astype(int).values
groups = df["comune"].values
band = df["size_band"].values
print(f"{len(df)} roofs, {y.sum()} AC, {df['comune'].nunique()} municipalities")


def lr_fit_predict(Xtr, ytr, Xte, gtr=None, gte=None):
    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.05, max_iter=3000, class_weight="balanced"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m.fit(Xtr, ytr)
    return m.predict_proba(Xte)[:, 1]


models = {"linear probe (logistic)": lr_fit_predict, "LightGBM": lambda a, b, c, d, e: lgbm_fit_predict(a, b, c)}
rows, oofs = [], {}
for name, fp in models.items():
    oof = grouped_oof_predictions(X, y, groups, n_splits=5, fit_predict=fp)
    oofs[name] = oof
    for b in ["100-500", "500-2000", ">=2000", "all"]:
        m = np.ones(len(y), bool) if b == "all" else band == b
        yy, pp = y[m], oof[m]
        order = np.argsort(-pp)
        r = {"model": name, "size_band": b, "n_ac": int(yy.sum()), "n_other": int((1 - yy).sum()),
             "base_rate": yy.mean(), "avg_precision": average_precision_score(yy, pp),
             "precision@recall0.5": precision_at_recall(yy, pp, 0.5),
             "precision@recall0.8": precision_at_recall(yy, pp, 0.8)}
        for k in (25, 100):
            r[f"hits_in_top{k}"] = int(yy[order[:k]].sum())
        rows.append(r)
res = pd.DataFrame(rows)
res.to_csv(OUT / "aerial_results.csv", index=False)
best = max(oofs, key=lambda k: res[(res.model == k) & (res.size_band == "all")]["avg_precision"].iloc[0])
pd.DataFrame({"roof_id": df.index, "y": y, "p": oofs[best], "size_band": band, "comune": groups}).to_parquet(OUT / "aerial_oof.parquet")

fig, axes = plt.subplots(1, 3, figsize=(15, 4), sharey=True)
for ax, b in zip(axes, ["100-500", "500-2000", ">=2000"]):
    m = band == b
    for name, oof in oofs.items():
        p, r, _ = precision_recall_curve(y[m], oof[m])
        ax.plot(r, p, label=name)
    ax.axhline(y[m].mean(), color="grey", ls=":", label=f"base rate {y[m].mean():.2f}")
    ax.plot([0.5], [0.6], "k*", ms=10, label="go/no-go bar")
    ax.set_title(f"{b} m2: held-out municipalities"); ax.set_xlabel("recall")
axes[0].set_ylabel("precision"); axes[0].legend(fontsize=8)
fig.tight_layout(); fig.savefig(OUT / "aerial_pr_curves.png", dpi=120)

# contact sheet: top-scored large roofs and the AC roofs the model missed most
CH = ROOT / "data/processed/chips"
d = pd.DataFrame({"roof_id": df.index, "y": y, "p": oofs[best], "band": band})
big = d[d.band != "100-500"]
top = big.sort_values("p", ascending=False).head(24)
miss = big[big.y == 1].sort_values("p").head(12)
fig, axes = plt.subplots(6, 6, figsize=(14, 15))
for ax, (_, r) in zip(axes.flat, list(top.iterrows()) + list(miss.iterrows())):
    ax.imshow(Image.open(CH / f"{int(r.roof_id)}.jpg"))
    col = "#1a9850" if r.y == 1 else "#d73027"
    for s in ax.spines.values():
        s.set_edgecolor(col); s.set_linewidth(4)
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title(f"p={r.p:.2f} {'AC' if r.y else 'not AC'}", fontsize=8, color=col)
fig.suptitle(f"Rows 1-4: top-scored roofs >=500 m2 ({best}); rows 5-6: AC roofs scored lowest.\n"
             "Green = mapped AC (2020), red = not mapped. Imagery: Ortofoto AGEA 2024, CC BY 4.0", fontsize=10)
fig.tight_layout(); fig.savefig(OUT / "aerial_top_predictions.png", dpi=100)
print(res.round(3).to_string(index=False))
print("best model:", best)
