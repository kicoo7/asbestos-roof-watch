"""Asbestos-cement roof labels from ARPA Piemonte's public ArcGIS FeatureServer."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Callable

PIEMONTE_FS = (
    "https://webgis.arpa.piemonte.it/server/rest/services/amianto/"
    "amianto_PRA_pubblico/FeatureServer"
)
LAYER_CHECKED = 5  # field-checked roofs: AC / remediated
LAYER_CANDIDATES = 3  # remote-sensing candidates, not yet verified
LAYER_MUNICIPALITIES = 2


def _http_get_json(url: str, params: dict) -> dict:
    full = url + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(full, timeout=120) as r:
        return json.load(r)


def fetch_arcgis_layer(
    layer_url: str,
    where: str = "1=1",
    out_sr: int = 32632,
    page_size: int = 2000,
    get_json: Callable[[str, dict], dict] = _http_get_json,
) -> list[dict]:
    """Return every feature of an ArcGIS layer as GeoJSON features, paging by offset.

    Some servers fail on pages whose payload is too large; the page size is then halved
    and the same offset retried, down to one feature.
    """
    feats: list[dict] = []
    offset = 0
    while True:
        params = {
            "where": where,
            "outFields": "*",
            "outSR": out_sr,
            "f": "geojson",
            "orderByFields": "objectid",
            "resultOffset": offset,
            "resultRecordCount": page_size,
        }
        page = get_json(layer_url + "/query", params)
        if "error" in page:
            if page_size > 1:
                page_size = max(page_size // 2, 1)
                continue
            raise RuntimeError(f"{layer_url}: {page['error'].get('message')}")
        batch = page.get("features", [])
        feats.extend(batch)
        more = page.get("exceededTransferLimit") or page.get("properties", {}).get(
            "exceededTransferLimit"
        )
        if not more or not batch:
            return feats
        offset += len(batch)


def esri_rings_to_shapely(rings):
    """Esri JSON polygon rings -> shapely (clockwise rings are exteriors, counter-clockwise are holes)."""
    from shapely.geometry import LinearRing, MultiPolygon, Polygon

    exteriors, holes = [], []
    for r in rings:
        if len(r) < 4:
            continue
        (holes if LinearRing(r).is_ccw else exteriors).append(r)
    polys = [Polygon(e) for e in exteriors]
    assigned = [[] for _ in polys]
    for h in holes:
        hp = Polygon(h)
        for i, p in enumerate(polys):
            if p.contains(hp.representative_point()):
                assigned[i].append(h)
                break
    out = [Polygon(e, assigned[i]) for i, e in enumerate(exteriors)]
    if not out:
        return Polygon()
    return out[0] if len(out) == 1 else MultiPolygon(out)


def fetch_arcgis_esri_json(layer_url: str, where: str = "1=1", out_sr: int = 32632, page_size: int = 200,
                           get_json: Callable[[str, dict], dict] = _http_get_json) -> list[dict]:
    """Like fetch_arcgis_layer but in Esri JSON (for servers that fail to write some geometries as GeoJSON).

    Returns GeoJSON-like features: {"properties": {...}, "geometry": shapely geometry}.
    """
    feats: list[dict] = []
    offset = 0
    while True:
        params = {"where": where, "outFields": "*", "outSR": out_sr, "f": "json", "orderByFields": "objectid",
                  "resultOffset": offset, "resultRecordCount": page_size}
        page = get_json(layer_url + "/query", params)
        if "error" in page:
            if page_size > 1:
                page_size = max(page_size // 2, 1)
                continue
            raise RuntimeError(f"{layer_url}: {page['error'].get('message')}")
        batch = page.get("features", [])
        for f in batch:
            g = f.get("geometry") or {}
            geom = esri_rings_to_shapely(g["rings"]) if "rings" in g else None
            feats.append({"properties": f.get("attributes", {}), "geometry": geom})
        if not page.get("exceededTransferLimit") or not batch:
            return feats
        offset += len(batch)


def label_from_esito(esito_tot: str | None, desc_esito: str | None) -> str:
    """Map ARPA Piemonte's outcome codes to project labels."""
    desc = (desc_esito or "").lower()
    if esito_tot == "1":
        return "ac"
    if esito_tot == "3":
        return "removed" if desc.startswith("completamente") else "partial"
    if esito_tot == "6":
        return "candidate"
    return "unknown"
