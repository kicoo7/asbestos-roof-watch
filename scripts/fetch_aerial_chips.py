"""AGEA 2024 tiles (CC BY 4.0) -> one masked 224 px JPEG chip per roof in data/processed/chips/ (resumable)."""
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from roofwatch.aerial import LEVEL, make_fetcher, mask_square, mosaic, tiles_for_bounds

MARGIN = 0.1
roofs = gpd.read_file(ROOT / "data/processed/lombardy_roofs.gpkg", layer="roofs").to_crs(3857)
out = ROOT / "data/processed/chips"
out.mkdir(parents=True, exist_ok=True)
fetch = make_fetcher(ROOT / "data/raw/agea_tiles")


def square_bounds(geom):
    minx, miny, maxx, maxy = geom.bounds
    side = max(maxx - minx, maxy - miny) * (1 + 2 * MARGIN) + 2.0
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    return (cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2)


todo = roofs[[not (out / f"{r}.jpg").exists() for r in roofs["roof_id"]]]
tiles = sorted({t for g in todo.geometry for t in tiles_for_bounds(square_bounds(g), LEVEL)})
print(f"{len(todo)} chips to make, {len(tiles)} tiles needed", flush=True)
t0 = time.time()
with ThreadPoolExecutor(max_workers=6) as ex:
    for n, _ in enumerate(ex.map(lambda rc: fetch(LEVEL, *rc), tiles), 1):
        if n % 2000 == 0:
            print(f"tiles {n}/{len(tiles)} {time.time()-t0:.0f}s", flush=True)
print(f"tiles done {time.time()-t0:.0f}s", flush=True)
for n, (rid, g) in enumerate(zip(todo["roof_id"], todo.geometry), 1):
    img, transform = mosaic(square_bounds(g), LEVEL, fetch)
    chip = mask_square(img, transform, g, size=224, margin_frac=MARGIN)
    Image.fromarray(chip).save(out / f"{rid}.jpg", quality=92)
    if n % 5000 == 0:
        print(f"chips {n}/{len(todo)} {time.time()-t0:.0f}s", flush=True)
print(f"done {time.time()-t0:.0f}s", flush=True)
