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
print(f"removed roofs (inspected 2019+): {len(removed)}; still-AC roofs inspected 2018+: {len(still_ac)}, {roof_years.sum():.0f} roof-years")

# normalisation needs every roof in a scene, so detection runs on all observations
configs = [dict(normalize=False, floor=0.03, threshold=t, min_span_days=10) for t in (4, 6)]
configs += [dict(normalize=True, rel_floor=f, threshold=t, min_span_days=sp)
            for f in (0.1, 0.15, 0.2) for t in (3, 4, 6) for sp in (10, 20)]


def evaluate(cfg):
    ev = detect_changes(obs, **cfg)
    ev = ev.join(inv[["label_date", "tier"]], on="roof_id")
    before_insp = ev[ev.date <= ev.label_date + SLACK]
    r = dict(cfg)
    for t in ("A", "B", "all"):
        rm = removed if t == "all" else removed[inv.loc[removed, "tier"] == t]
        sa = still_ac if t == "all" else still_ac[inv.loc[still_ac, "tier"] == t]
        hit = before_insp[before_insp.roof_id.isin(rm)]
        fa = ev[ev.roof_id.isin(sa) & (ev.confirmed_on <= ev.label_date)]
        r[f"detect_{t}"] = hit.roof_id.nunique() / max(len(rm), 1)
        r[f"any_event_still_ac_{t}"] = fa.roof_id.nunique() / max(len(sa), 1)  # chance level for detect_
        r[f"fa_per_1000_roof_yr_{t}"] = 1000 * len(fa) / max(roof_years.reindex(sa).sum(), 1e-9)
    r["events_total"] = len(ev)
    r["roofs_with_event_share"] = ev.roof_id.nunique() / obs.roof_id.nunique()
    r["types_detected"] = before_insp[before_insp.roof_id.isin(removed)].drop_duplicates("roof_id").change_type.value_counts().to_dict()
    return r


if __name__ == "__main__":
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=6) as ex:
        rows = list(ex.map(evaluate, configs))
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "change_validation.csv", index=False)
    cols = ["normalize", "rel_floor", "floor", "threshold", "min_span_days", "detect_A", "any_event_still_ac_A",
            "fa_per_1000_roof_yr_A", "detect_B", "any_event_still_ac_B", "fa_per_1000_roof_yr_B", "roofs_with_event_share"]
    print(res[cols].round(3).to_string(index=False))
