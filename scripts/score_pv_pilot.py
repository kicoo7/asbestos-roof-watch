"""Apply the solar-panel detector (trained on all of layer 4) to every Brescia pilot roof, then re-check
the asbestos model without 'AC' labels whose roofs now carry panels (stale since the 2020 survey).
-> outputs/pilot_pv_scores.parquet, outputs/aerial_results_clean.csv
"""
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from roofwatch.lombardy import decode_older
from roofwatch.model import lgbm_fit_predict, precision_at_recall

PV_T = 0.9
ds = gpd.read_file(ROOT / "data/processed/pv_roofs.gpkg", layer="roofs").set_index("roof_id")
pe = pd.read_parquet(ROOT / "data/processed/pv_chip_embeddings.parquet").set_index("roof_id")
tr = ds[["decodifica", "area_m2"]].join(pe, how="inner")
Xtr = tr[list(pe.columns)].assign(log_area=np.log(tr["area_m2"]))
ytr = (tr["decodifica"].map(decode_older) == "pv").astype(int).values

roofs = gpd.read_file(ROOT / "data/processed/lombardy_roofs.gpkg", layer="roofs").set_index("roof_id")
be = pd.read_parquet(ROOT / "data/processed/chip_embeddings.parquet").set_index("roof_id")
te = roofs[["area_m2", "label", "size_band"]].join(be, how="inner")
Xte = te[list(be.columns)].assign(log_area=np.log(te["area_m2"]))
p_pv = np.mean([lgbm_fit_predict(Xtr, ytr, Xte, seed=s) for s in range(3)], axis=0)
scores = pd.DataFrame({"roof_id": te.index, "p_pv": p_pv})
scores.to_parquet(ROOT / "outputs/pilot_pv_scores.parquet", index=False)
te["p_pv"] = p_pv
print("pilot roofs with panels (p_pv >= %.1f):" % PV_T, pd.crosstab(te["label"], te["p_pv"] >= PV_T).to_dict())

oof = pd.read_parquet(ROOT / "outputs/aerial_oof.parquet").set_index("roof_id").join(te["p_pv"])
stale = (oof["y"] == 1) & (oof["p_pv"] >= PV_T)
print(f"'AC' labels that now carry panels: {int(stale.sum())} of {int(oof['y'].sum())}")
rows = []
for variant, d in [("as labelled", oof), ("without AC labels now under panels", oof[~stale])]:
    for b in ["100-500", "500-2000", ">=2000", "all"]:
        x = d if b == "all" else d[d["size_band"] == b]
        rows.append({"labels": variant, "size_band": b, "n_ac": int(x.y.sum()), "base_rate": x.y.mean(),
                     "avg_precision": average_precision_score(x.y, x.p),
                     "precision@recall0.5": precision_at_recall(x.y, x.p, 0.5),
                     "precision@recall0.8": precision_at_recall(x.y, x.p, 0.8)})
res = pd.DataFrame(rows)
res.to_csv(ROOT / "outputs/aerial_results_clean.csv", index=False)
print(res.round(3).to_string(index=False))
big = te[(te["area_m2"] >= 500) & (te["p_pv"] < 0.2)]
print(f"roofs >= 500 m2 with no panels (p_pv < 0.2): {len(big)} of {int((te['area_m2'] >= 500).sum())}, total {big['area_m2'].sum()/1e6:.2f} km2")
