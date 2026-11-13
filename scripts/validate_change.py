"""T2.2/T2.3: detection rate on remediated roofs and false alarms per 1,000 roofs per year on roofs still AC at inspection."""
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from roofwatch.change import detect_changes
from roofwatch.pipeline import load_all_observations

OUT = ROOT / "outputs"
inv = gpd.read_file(ROOT / "data/processed/roofs_inventory.gpkg", layer="roofs").set_index("roof_id")
inv["label_date"] = pd.to_datetime(inv["label_date"])
obs = load_all_observations(OUT)
first_obs = obs.groupby("roof_id")["date"].min()
SLACK = pd.Timedelta(days=60)
MIN_START = pd.Timestamp("2018-01-01")  # leave a year of archive for a baseline

removed = inv[(inv.label == "removed") & (inv.label_date >= pd.Timestamp("2019-01-01"))].index
still_ac = inv[(inv.label == "ac") & (inv.label_date >= MIN_START)].index
roof_years = ((inv.loc[still_ac, "label_date"] - first_obs.reindex(still_ac)).dt.days / 365.25).clip(lower=0)
subset = obs[obs.roof_id.isin(removed.union(still_ac))]
print(f"removed roofs (inspected 2019+): {len(removed)}; still-AC roofs inspected 2018+: {len(still_ac)}, {roof_years.sum():.0f} roof-years")

rows, best = [], None
for floor in (0.02, 0.03):
    for thr in (2.5, 3, 4, 5, 6, 8):
        ev = detect_changes(subset, threshold=thr, floor=floor).join(inv[["label_date", "tier"]], on="roof_id")
        hit = ev[ev.roof_id.isin(removed) & (ev.date <= ev.label_date + SLACK)]
        fa = ev[ev.roof_id.isin(still_ac) & (ev.confirmed_on <= ev.label_date)]
        r = {"floor": floor, "threshold": thr}
        for t in ("A", "B", "all"):
            rm = removed if t == "all" else removed[inv.loc[removed, "tier"] == t]
            sa = still_ac if t == "all" else still_ac[inv.loc[still_ac, "tier"] == t]
            r[f"detect_{t}"] = hit[hit.roof_id.isin(rm)].roof_id.nunique() / max(len(rm), 1)
            r[f"fa_per_1000_roof_yr_{t}"] = 1000 * len(fa[fa.roof_id.isin(sa)]) / max(roof_years.reindex(sa).sum(), 1e-9)
        rows.append(r)
        if (floor, thr) == (0.02, 3):
            best = hit
res = pd.DataFrame(rows)
res.to_csv(OUT / "change_validation.csv", index=False)
print(res.round(3).to_string(index=False))
if best is not None and len(best):
    print("change types on detected remediated roofs (floor .02, thr 3):", best.drop_duplicates("roof_id").change_type.value_counts().to_dict())
