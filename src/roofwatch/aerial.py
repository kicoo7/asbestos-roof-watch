"""AGEA aerial orthophoto tiles (CC BY 4.0) -> masked per-roof image chips.

The AGEA 2024 image service exposes a cached Web Mercator tile pyramid; level 18
(~0.6 m in Mercator, ~0.42 m on the ground at 45 deg N) is the deepest cached level.
Dynamic exportImage returns a checkerboard placeholder, so tiles are the only route.
"""
from __future__ import annotations

import io
import math
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from affine import Affine
from rasterio import features

ORIGIN = -20037508.342787
AGEA_2024 = "https://geoportale.agea.gov.it/image/rest/services/AgEA/Ortofoto_AgEA_2024/ImageServer"
ATTRIBUTION = "Ortofoto AGEA 2024, CC BY 4.0"
LEVEL = 18
TILE = 256


def tile_res(level: int) -> float:
    return 156543.03392804097 / 2 ** level


def tile_bounds(level: int, row: int, col: int):
    size = TILE * tile_res(level)
    minx = ORIGIN + col * size
    maxy = -ORIGIN - row * size
    return (minx, maxy - size, minx + size, maxy)


def tiles_for_bounds(bounds, level: int) -> list[tuple[int, int]]:
    minx, miny, maxx, maxy = bounds
    size = TILE * tile_res(level)
    eps = 1e-7  # bounds that sit exactly on a tile edge must not pull in the neighbour
    c0 = math.floor((minx - ORIGIN) / size + eps)
    c1 = math.floor((maxx - ORIGIN) / size - eps)
    r0 = math.floor((-ORIGIN - maxy) / size + eps)
    r1 = math.floor((-ORIGIN - miny) / size - eps)
    return [(r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)]


def mosaic(bounds, level: int, fetch):
    """RGB array covering `bounds` (EPSG:3857) on the tile pixel grid, and its affine transform.

    `fetch(level, row, col)` returns a (256, 256, 3) uint8 array or None (missing -> black).
    """
    res = tile_res(level)
    tiles = tiles_for_bounds(bounds, level)
    rows = sorted({r for r, _ in tiles})
    cols = sorted({c for _, c in tiles})
    canvas = np.zeros((len(rows) * TILE, len(cols) * TILE, 3), dtype=np.uint8)
    for r, c in tiles:
        t = fetch(level, r, c)
        if t is not None:
            i, j = (r - rows[0]) * TILE, (c - cols[0]) * TILE
            canvas[i:i + TILE, j:j + TILE] = t[:TILE, :TILE, :3]
    cminx, _, _, cmaxy = tile_bounds(level, rows[0], cols[0])
    x0 = round((bounds[0] - cminx) / res)
    x1 = round((bounds[2] - cminx) / res)
    y0 = round((cmaxy - bounds[3]) / res)
    y1 = round((cmaxy - bounds[1]) / res)
    img = canvas[y0:y1, x0:x1]
    return img, Affine(res, 0, cminx + x0 * res, 0, -res, cmaxy - y0 * res)


def mask_square(img: np.ndarray, transform: Affine, poly, size: int = 224, margin_frac: float = 0.1) -> np.ndarray:
    """Square chip centred on `poly`, pixels outside the polygon set to 0, resized to size x size."""
    from PIL import Image

    inside = features.geometry_mask([poly], out_shape=img.shape[:2], transform=transform, invert=True)
    masked = img * inside[..., None]
    minx, miny, maxx, maxy = poly.bounds
    c0, r0 = (minx - transform.c) / transform.a, (maxy - transform.f) / transform.e
    c1, r1 = (maxx - transform.c) / transform.a, (miny - transform.f) / transform.e
    side = max(c1 - c0, r1 - r0) * (1 + 2 * margin_frac)
    cc, rc = (c0 + c1) / 2, (r0 + r1) / 2
    left, top = int(round(cc - side / 2)), int(round(rc - side / 2))
    n = max(int(round(side)), 1)
    out = np.zeros((n, n, 3), dtype=np.uint8)
    sr0, sc0 = max(top, 0), max(left, 0)
    sr1, sc1 = min(top + n, img.shape[0]), min(left + n, img.shape[1])
    if sr1 > sr0 and sc1 > sc0:
        out[sr0 - top:sr1 - top, sc0 - left:sc1 - left] = masked[sr0:sr1, sc0:sc1]
    return np.asarray(Image.fromarray(out).resize((size, size), Image.BILINEAR))


def make_fetcher(cache_dir, url: str = AGEA_2024, retries: int = 4):
    """Tile fetcher with an on-disk cache (`.none` marks tiles the server does not have)."""
    from PIL import Image

    cache_dir = Path(cache_dir)

    def fetch(level, row, col):
        p = cache_dir / str(level) / str(row) / f"{col}.jpg"
        if p.with_suffix(".none").exists():
            return None
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(f"{url}/tile/{level}/{row}/{col}",
                                         headers={"User-Agent": "roofwatch-prototype (research)"})
            for attempt in range(retries):
                try:
                    with urllib.request.urlopen(req, timeout=60) as r:
                        data = r.read()
                    break
                except urllib.error.HTTPError as e:
                    if e.code == 404:
                        p.with_suffix(".none").touch()
                        return None
                    if attempt == retries - 1:
                        raise
                except (urllib.error.URLError, TimeoutError):
                    if attempt == retries - 1:
                        raise
                time.sleep(2 * (attempt + 1))
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(p)
        return np.asarray(Image.open(p).convert("RGB"))

    return fetch
