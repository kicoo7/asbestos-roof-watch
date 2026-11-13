"""Solar-panel dataset from Lombardy's older AC mapping (layer 4), region-wide.

pv        = AC roof replaced with photovoltaic (2007-2018)       -> panels expected in 2024 imagery
changed   = AC roof replaced, no PV and no demolition recorded   -> new roof without panels (as of 2018)
unchanged = AC roof still present in 2018                        -> no panels (as of 2018)
-> data/processed/pv_roofs.gpkg ; chips in data/processed/pv_chips/
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import pandas as pd
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from roofwatch.aerial import LEVEL, make_fetcher, mask_square, mosaic, tiles_for_bounds
from roofwatch.lombardy import LAYER_OLDER, decode_older, fetch_polygons

PROC = ROOT / "data/processed"
N_NEG = 3000  # per negative class
out_gpkg = PROC / "pv_roofs.gpkg"
if not out_gpkg.exists():
    pv = fetch_polygons(LAYER_OLDER, where="decodifica LIKE '%fotovoltaic%'")
    other = fetch_polygons(LAYER_OLDER, where="decodifica LIKE 'variato fra%' OR decodifica LIKE 'non variato%'")
    allp = pd.concat([pv, other], ignore_index=True)
    allp["cls"] = allp["decodifica"].map(decode_older)
    allp = allp[allp["cls"].isin(["pv", "changed", "unchanged"]) & (allp.area >= 100)]
    # (groupby().apply drops the grouping column in recent pandas, so sample each class explicitly)
    neg = pd.concat([allp[allp["cls"] == c].sample(min(N_NEG, int((allp["cls"] == c).sum())), random_state=0)
                     for c in ("changed", "unchanged")])
    ds = gpd.GeoDataFrame(pd.concat([allp[allp["cls"] == "pv"], neg]), crs=allp.crs).reset_index(drop=True)
    ds["roof_id"] = range(len(ds))
    ds["area_m2"] = ds.area
    ds[["roof_id", "cls", "decodifica", "COD_ISTATN", "area_m2", "geometry"]].to_file(out_gpkg, layer="roofs", driver="GPKG")
ds = gpd.read_file(out_gpkg, layer="roofs")
print(ds["cls"].value_counts().to_dict(), "municipalities:", ds["COD_ISTATN"].nunique(), flush=True)

g3857 = ds.to_crs(3857)
chips = PROC / "pv_chips"
chips.mkdir(exist_ok=True)
fetch = make_fetcher(ROOT / "data/raw/agea_tiles")


def square_bounds(geom, margin=0.1):
    minx, miny, maxx, maxy = geom.bounds
    side = max(maxx - minx, maxy - miny) * (1 + 2 * margin) + 2.0
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    return (cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2)


todo = g3857[[not (chips / f"{r}.jpg").exists() for r in g3857["roof_id"]]]
tiles = sorted({t for g in todo.geometry for t in tiles_for_bounds(square_bounds(g), LEVEL)})
print(f"{len(todo)} chips, {len(tiles)} tiles", flush=True)
t0 = time.time()
with ThreadPoolExecutor(max_workers=6) as ex:
    for n, _ in enumerate(ex.map(lambda rc: fetch(LEVEL, *rc), tiles), 1):
        if n % 2000 == 0:
            print(f"tiles {n}/{len(tiles)} {time.time()-t0:.0f}s", flush=True)
for rid, g in zip(todo["roof_id"], todo.geometry):
    img, tr = mosaic(square_bounds(g), LEVEL, fetch)
    Image.fromarray(mask_square(img, tr, g, size=224, margin_frac=0.1)).save(chips / f"{rid}.jpg", quality=92)
print(f"done {time.time()-t0:.0f}s", flush=True)
