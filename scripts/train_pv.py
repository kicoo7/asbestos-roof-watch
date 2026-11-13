"""Solar-panel detector on AGEA 2024 chips (DINOv2 embeddings), municipality-grouped CV.

Labels from Lombardy layer 4: pv (AC replaced with PV by 2018) vs changed/unchanged (no PV as of 2018).
-> outputs/pv_results.csv, pv_oof.parquet, pv_top_predictions.png
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
from sklearn.metrics import average_precision_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from roofwatch.model import grouped_oof_predictions, lgbm_fit_predict, precision_at_recall

OUT = ROOT / "outputs"
ds = gpd.read_file(ROOT / "data/processed/pv_roofs.gpkg", layer="roofs").set_index("roof_id")
emb = pd.read_parquet(ROOT / "data/processed/pv_chip_embeddings.parquet").set_index("roof_id")
df = ds[["cls", "COD_ISTATN", "area_m2"]].join(emb, how="inner")
X = df[list(emb.columns)].copy()
X["log_area"] = np.log(df["area_m2"])
y = (df["cls"] == "pv").astype(int).values
groups = df["COD_ISTATN"].values
print(f"{len(df)} roofs: {df['cls'].value_counts().to_dict()}, {df['COD_ISTATN'].nunique()} municipalities")


def lr(Xtr, ytr, Xte, gtr=None, gte=None):
    m = make_pipeline(StandardScaler(), LogisticRegression(C=0.05, max_iter=3000, class_weight="balanced"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m.fit(Xtr, ytr)
    return m.predict_proba(Xte)[:, 1]


rows, oofs = [], {}
for name, fp in {"linear probe (logistic)": lr, "LightGBM": lambda a, b, c, d, e: lgbm_fit_predict(a, b, c)}.items():
    oof = grouped_oof_predictions(X, y, groups, n_splits=5, fit_predict=fp)
    oofs[name] = oof
    for neg in ["changed", "unchanged", "all"]:
        m = (df["cls"].values == "pv") | ((df["cls"].values == neg) if neg != "all" else True)
        yy, pp = y[m], oof[m]
        rows.append({"model": name, "negatives": neg, "n_pv": int(yy.sum()), "n_other": int((1 - yy).sum()),
                     "avg_precision": average_precision_score(yy, pp),
                     "precision@recall0.8": precision_at_recall(yy, pp, 0.8),
                     "precision@recall0.9": precision_at_recall(yy, pp, 0.9)})
res = pd.DataFrame(rows)
res.to_csv(OUT / "pv_results.csv", index=False)
best = max(oofs, key=lambda k: res[(res.model == k) & (res.negatives == "all")]["avg_precision"].iloc[0])
pd.DataFrame({"roof_id": df.index, "cls": df["cls"].values, "p_pv": oofs[best]}).to_parquet(OUT / "pv_oof.parquet")

d = pd.DataFrame({"roof_id": df.index, "y": y, "p": oofs[best], "cls": df["cls"].values})
fp_ = d[d.y == 0].sort_values("p", ascending=False).head(12)   # highest-scored "no PV" roofs
fn_ = d[d.y == 1].sort_values("p").head(12)                   # lowest-scored PV roofs
tp_ = d[d.y == 1].sort_values("p", ascending=False).head(12)
fig, axes = plt.subplots(6, 6, figsize=(14, 15))
for ax, (_, r) in zip(axes.flat, list(tp_.iterrows()) + list(fp_.iterrows()) + list(fn_.iterrows())):
    ax.imshow(Image.open(ROOT / f"data/processed/pv_chips/{int(r.roof_id)}.jpg"))
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title(f"p={r.p:.2f} label={r.cls}", fontsize=8, color="#1a9850" if r.y else "#d73027")
fig.suptitle(f"Solar detector ({best}). Rows 1-2: top PV; rows 3-4: highest-scored 'no PV' (as of 2018); rows 5-6: lowest-scored PV.\n"
             "Imagery: Ortofoto AGEA 2024, CC BY 4.0", fontsize=10)
fig.tight_layout(); fig.savefig(OUT / "pv_top_predictions.png", dpi=100)
print(res.round(3).to_string(index=False)); print("best:", best)
