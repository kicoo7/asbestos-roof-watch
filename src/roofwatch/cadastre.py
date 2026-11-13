"""Agenzia delle Entrate cadastral parcels (INSPIRE WFS, CC BY 4.0): roof -> town / sheet (foglio) / parcel.

The parcel reference is what a manual owner lookup (visura per immobile) needs. Owner names are not
open data and are not fetched here.
"""
from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import Polygon

WFS = "https://wfs.cartografia.agenziaentrate.gov.it/inspire/wfs/owfs01.php"
ATTRIBUTION = "Cartografia catastale, Agenzia delle Entrate, CC BY 4.0"
NS = {"gml": "http://www.opengis.net/gml/3.2", "wfs": "http://www.opengis.net/wfs/2.0",
      "CP": "http://mapserver.gis.umn.edu/mapserver"}
_REF = re.compile(r"^(?P<comune>[A-Z]\d{3})_(?P<foglio>\d{4})(?P<allegato>\w)(?P<sviluppo>\w)\.(?P<particella>.+)$")


def parse_reference(ref: str) -> dict:
    """'L777_001600.103' -> town code L777, sheet 16, annex '0', development '0', parcel '103'."""
    m = _REF.match(ref)
    if not m:
        return {"comune": None, "foglio": None, "allegato": None, "sviluppo": None, "particella": None}
    d = m.groupdict()
    d["foglio"] = int(d["foglio"])
    return d


def _ring(el) -> list[tuple[float, float]]:
    v = [float(t) for t in el.find(".//gml:posList", NS).text.split()]
    return [(v[i + 1], v[i]) for i in range(0, len(v), 2)]  # GML is lat lon -> (lon, lat)


def parse_parcels_gml(data: bytes) -> gpd.GeoDataFrame:
    root = ET.fromstring(data)
    rows, geoms = [], []
    for p in root.iter(f"{{{NS['CP']}}}CadastralParcel"):
        ref = p.findtext("CP:NATIONALCADASTRALREFERENCE", namespaces=NS)
        polys = []
        for pg in p.iter(f"{{{NS['gml']}}}Polygon"):
            ext = pg.find("gml:exterior", NS)
            holes = [_ring(i) for i in pg.findall("gml:interior", NS)]
            polys.append(Polygon(_ring(ext), holes))
        if not polys:
            continue
        geom = shapely.make_valid(polys[0] if len(polys) == 1 else shapely.MultiPolygon(polys))
        rows.append({"ref": ref, **parse_reference(ref)})
        geoms.append(geom)
    return gpd.GeoDataFrame(pd.DataFrame(rows, columns=["ref", "comune", "foglio", "allegato", "sviluppo", "particella"]),
                            geometry=geoms, crs="EPSG:6706")


def fetch_parcels(bbox_lonlat, page: int = 1000, retries: int = 4) -> gpd.GeoDataFrame:
    """All parcels intersecting (minlon, minlat, maxlon, maxlat), with WFS paging."""
    minx, miny, maxx, maxy = bbox_lonlat
    frames, start = [], 0
    while True:
        q = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": "CP:CadastralParcel",
             "count": page, "startIndex": start, "bbox": f"{miny},{minx},{maxy},{maxx},urn:ogc:def:crs:EPSG::6706"}
        req = urllib.request.Request(WFS + "?" + urllib.parse.urlencode(q), headers={"User-Agent": "roofwatch-prototype (research)"})
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = r.read()
                break
            except Exception:
                if attempt == retries - 1:
                    raise
                time.sleep(3 * (attempt + 1))
        g = parse_parcels_gml(data)
        frames.append(g)
        if len(g) < page:
            break
        start += page
    out = pd.concat(frames, ignore_index=True) if frames else gpd.GeoDataFrame()
    return gpd.GeoDataFrame(out, geometry="geometry", crs="EPSG:6706").drop_duplicates("ref")


def assign_parcels(roofs: gpd.GeoDataFrame, parcels: gpd.GeoDataFrame, min_share: float = 0.2) -> pd.DataFrame:
    """Per roof: the parcel covering most of it (`parcel_ref`, `parcel_share`) and other parcels covering >= min_share."""
    parcels = parcels.to_crs(roofs.crs)
    ov = gpd.overlay(roofs[["roof_id", "geometry"]], parcels[["ref", "geometry"]], how="intersection", keep_geom_type=True)
    ov["share"] = ov.area / ov["roof_id"].map(roofs.set_index("roof_id").area)
    rows = []
    for rid in roofs["roof_id"]:
        g = ov[ov["roof_id"] == rid].sort_values("share", ascending=False)
        if g.empty:
            rows.append({"roof_id": rid, "parcel_ref": None, "parcel_share": 0.0, "other_parcels": ""})
            continue
        others = g.iloc[1:][g.iloc[1:]["share"] >= min_share]["ref"]
        rows.append({"roof_id": rid, "parcel_ref": g.iloc[0]["ref"], "parcel_share": float(g.iloc[0]["share"]),
                     "other_parcels": ";".join(others)})
    return pd.DataFrame(rows)
