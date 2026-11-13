"""ARPA Lombardia / Regione Lombardia asbestos-cement roof mapping (polygons)."""
from __future__ import annotations

import geopandas as gpd

from .labels import fetch_arcgis_esri_json

LOMBARDY_MS = "https://www.cartografia.servizirl.it/expo/rest/services/gpt/coperture_amianto/MapServer"
LAYER_AC_2020 = 1        # roofs mapped as AC in the 2020 survey
LAYER_AREAS_2020 = 2     # outline of the area surveyed in 2020
LAYER_OLDER = 4          # 2007-2015 mappings re-checked to 2018, with change type in `decodifica`
LAYER_AREAS_OLDER = 5
LAYER_MUNICIPALITIES = 7


def decode_older(decodifica: str | None) -> str:
    d = (decodifica or "").lower()
    if not d:
        return "unknown"
    if d.startswith("non variato"):
        return "unchanged"
    if "fotovoltaic" in d:
        return "pv"
    if "demolizione" in d:
        return "demolished"
    if d.startswith("variato"):
        return "changed"
    return "unknown"


def fetch_polygons(layer: int, where: str = "1=1") -> gpd.GeoDataFrame:
    # This server fails on large pages and cannot write some geometries as GeoJSON: use Esri JSON, 200 per page.
    feats = fetch_arcgis_esri_json(f"{LOMBARDY_MS}/{layer}", where=where, page_size=200)
    return gpd.GeoDataFrame([f["properties"] for f in feats], geometry=[f["geometry"] for f in feats], crs="EPSG:32632")
