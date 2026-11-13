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
    """Return every feature of an ArcGIS layer as GeoJSON features, paging by offset."""
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
            raise RuntimeError(f"{layer_url}: {page['error'].get('message')}")
        batch = page.get("features", [])
        feats.extend(batch)
        more = page.get("exceededTransferLimit") or page.get("properties", {}).get(
            "exceededTransferLimit"
        )
        if not more or not batch:
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
