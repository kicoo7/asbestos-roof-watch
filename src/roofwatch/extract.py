"""Read Sentinel-2 L2A COG windows over roofs and turn them into per-roof observations."""
from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from affine import Affine
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
from shapely.geometry import box

from .inventory import pixel_lookup
from .observe import BANDS, roof_stats, scale_offset, to_reflectance

COLLECTION = "sentinel-2-c1-l2a"
STAC_URL = "https://earth-search.aws.element84.com/v1"
GDAL_ENV = {
    "AWS_NO_SIGN_REQUEST": "YES",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_HTTP_VERSION": "2",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "VSI_CACHE": "TRUE",
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "2",
}


def snap_bounds(bounds, step: float):
    """Expand (minx, miny, maxx, maxy) outward to multiples of `step`."""
    minx, miny, maxx, maxy = bounds
    return (
        math.floor(minx / step) * step,
        math.floor(miny / step) * step,
        math.ceil(maxx / step) * step,
        math.ceil(maxy / step) * step,
    )


def assign_home_tiles(roofs: gpd.GeoDataFrame, tiles: dict, priority: list[str]) -> pd.Series:
    """Each roof is read from one tile only: the first in `priority` that fully contains it."""
    home = pd.Series([None] * len(roofs), index=roofs.index, dtype=object)
    for code in reversed(priority):
        inside = roofs.geometry.within(tiles[code])
        home[inside] = code
    return home


def tile_extent(item) -> object:
    """Full MGRS tile square (in the tile's UTM CRS) from an item's asset metadata."""
    ef = item.assets["red"].extra_fields
    if "proj:bbox" in ef:
        return box(*ef["proj:bbox"])
    a, _, x0, _, e, y0 = ef["proj:transform"][:6]
    rows, cols = ef["proj:shape"]
    return box(x0, y0 + e * rows, x0 + a * cols, y0)


def tile_code(item) -> str:
    return item.properties["grid:code"].replace("MGRS-", "")


def roof_grid(roofs: gpd.GeoDataFrame, res: float = 10, pad: float = 20):
    """Snapped window bounds, transform and shape covering the roofs' sampling polygons."""
    minx, miny, maxx, maxy = roofs["sample_geom"].total_bounds
    b = snap_bounds((minx - pad, miny - pad, maxx + pad, maxy + pad), 20)
    transform = Affine(res, 0, b[0], 0, -res, b[3])
    shape = (int(round((b[3] - b[1]) / res)), int(round((b[2] - b[0]) / res)))
    return b, transform, shape


def build_lookup(roofs: gpd.GeoDataFrame):
    bounds, transform, shape = roof_grid(roofs)
    lut = pixel_lookup(list(roofs["sample_geom"]), transform, shape)
    return bounds, transform, shape, dict(zip(roofs["roof_id"].tolist(), lut))


def _read_asset(href: str, bounds, shape, resampling=Resampling.nearest):
    with rasterio.Env(**GDAL_ENV):
        with rasterio.open(href) as src:
            win = from_bounds(*bounds, transform=src.transform)
            return src.read(1, window=win, out_shape=shape, resampling=resampling, boundless=True, fill_value=0)


def read_scene(item, bounds, shape, bands=BANDS, max_workers: int = 8):
    """Reflectance arrays for `bands` and the SCL layer, all on the 10 m grid of `bounds`."""
    baseline = item.properties.get("s2:processing_baseline")
    names = list(bands) + ["scl"]
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        arrays = dict(zip(names, ex.map(lambda n: _read_asset(item.assets[n].href, bounds, shape), names)))
    out = {}
    for b in bands:
        rb = (item.assets[b].extra_fields.get("raster:bands") or [None])[0]
        s, o = scale_offset(rb, baseline)
        out[b] = to_reflectance(arrays[b], s, o, nodata=0)
    return out, arrays["scl"]


def extract_scene(item, bounds, shape, lut, min_clear_fraction: float = 0.8) -> pd.DataFrame:
    bands, scl = read_scene(item, bounds, shape)
    df = roof_stats(bands, scl, lut, min_clear_fraction=min_clear_fraction)
    df.insert(1, "scene_id", item.id)
    df.insert(2, "date", pd.Timestamp(item.properties["datetime"][:10]))
    df.insert(3, "tile", tile_code(item))
    return df
