"""T1.4: per-roof Sentinel-2 observations into outputs/observations.parquet (resumable, idempotent).

  extract_observations.py START END [--max-cloud 40] [--best-per-month]
"""
import argparse
import pickle
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
from pystac_client import Client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from roofwatch.extract import COLLECTION, STAC_URL, extract_scene, tile_code
from roofwatch.lock import run_lock
from roofwatch.store import append_observations

ap = argparse.ArgumentParser()
ap.add_argument("start"); ap.add_argument("end")
ap.add_argument("--max-cloud", type=float, default=40)
ap.add_argument("--best-per-month", action="store_true")
ap.add_argument("--workers", type=int, default=3)
ap.add_argument("--out", default=str(ROOT / "outputs/observations.parquet"))
a = ap.parse_args()

lookups = pickle.load(open(ROOT / "data/processed/lookups.pkl", "rb"))
done_log = Path(a.out).with_suffix(".scenes_done.txt")
done = set(done_log.read_text().split()) if done_log.exists() else set()

import geopandas as gpd
bbox = list(gpd.read_file(ROOT / "data/pilot_area.geojson").total_bounds)
items = [i for i in Client.open(STAC_URL).search(collections=[COLLECTION], bbox=bbox, datetime=f"{a.start}/{a.end}",
         query={"eo:cloud_cover": {"lt": a.max_cloud}}).items() if tile_code(i) in lookups]
if a.best_per_month:
    df = pd.DataFrame({"item": items, "tile": [tile_code(i) for i in items],
                       "month": [i.properties["datetime"][:7] for i in items],
                       "cc": [i.properties["eo:cloud_cover"] for i in items]})
    items = list(df.sort_values("cc").groupby(["tile", "month"]).head(1)["item"])
todo = [i for i in items if i.id not in done]
print(f"{len(items)} scenes, {len(todo)} to do", flush=True)

def run(it):
    b, t, shape, lut = lookups[tile_code(it)]
    for attempt in range(3):
        try:
            return it.id, extract_scene(it, b, shape, lut)
        except Exception as e:  # network hiccups on S3
            err = e; time.sleep(5 * (attempt + 1))
    return it.id, err

lock = run_lock(ROOT / "outputs/.roofwatch.lock")
with lock:
    buf, t0 = [], time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(run, it) for it in todo]
        for n, f in enumerate(as_completed(futs), 1):
            sid, res = f.result()
            if isinstance(res, Exception):
                print("FAILED", sid, repr(res)[:200], flush=True); continue
            buf.append((sid, res))
            if len(buf) >= 10 or n == len(todo):
                frames = [r for _, r in buf if not r.empty]
                added = append_observations(a.out, pd.concat(frames)) if frames else 0
                with open(done_log, "a") as fh:
                    fh.write("".join(s + "\n" for s, _ in buf))
                print(f"{n}/{len(todo)} scenes, +{added} rows, {time.time()-t0:.0f}s", flush=True)
                buf = []
