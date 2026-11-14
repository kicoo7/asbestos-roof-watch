"""Local map of the aerial pilot showing every input and output -> outputs/aerial_map.html (private: building level).

Base layers: AGEA 2024 orthophoto (what the model saw), Sentinel-2 cloudless 2024 (10 m, for comparison), Esri, OSM.
Overlays: ARPA Lombardia 2020 AC polygons and surveyed area (labels), Overture footprints, cadastral parcels of
candidate roofs, model results, solar. Clicking a roof opens a side panel (src/roofwatch/panel.py) with the exact chip
the model saw (relative link to data/processed/chips, so open the file from outputs/), the property, the businesses at the
building and their public company record. The panel data is embedded once, keyed by roof_id; the map features carry only
roof_id and their tooltip fields. `#roof=<roof_id>` in the URL opens that roof. The panel renders data as text; folium's
tooltips insert feature properties as HTML, so geojson() HTML-escapes every string property first.
"""
import glob
import os
import sys
from html import escape
from pathlib import Path

import folium
import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from roofwatch.panel import REGISTER_JS, build_details, load_inputs, panel_html  # noqa: E402

THRESH = 0.7  # ~53-59% of flags are mapped AC for roofs >= 500 m2, finding 70-75% of them (held-out towns)
RAW, PROC, OUT = ROOT / "data/raw", ROOT / "data/processed", ROOT / "outputs"
NAMES = {"agree": "Result: on ARPA map, model agrees", "mapped_not_seen": "Result: on ARPA map, model does not see AC (replaced?)",
         "suspected_new": "Result: not on ARPA map, model suspects AC"}
COLORS = {"agree": "#d7191c", "mapped_not_seen": "#2b83ba", "suspected_new": "#fdae61"}
SOLAR = "#1a9641"
LABELS = {"ac": "mapped AC (>=50% of roof)", "ac_part": "AC on part of roof", "none": "not mapped"}


def load_roofs(proc: Path, out: Path) -> gpd.GeoDataFrame:
    """Roofs in EPSG:4326 with asbestos score p, solar score p_pv, ARPA label text, result group and a point inside."""
    roofs = gpd.read_file(proc / "lombardy_roofs.gpkg", layer="roofs")
    roofs = roofs.merge(pd.read_parquet(out / "aerial_oof.parquet")[["roof_id", "p"]], on="roof_id", how="left")
    roofs = roofs.merge(pd.read_parquet(out / "pilot_pv_scores.parquet"), on="roof_id", how="left")
    roofs = roofs.to_crs(4326)
    roofs["p_raw"] = roofs["p"]  # threshold on the unrounded score
    for c in ("p", "p_pv"):
        roofs[c] = roofs[c].round(2)
    roofs["area_m2"] = roofs["area_m2"].round(0).astype(int)
    roofs["label_txt"] = roofs["label"].map(LABELS)
    pt = roofs.representative_point()
    roofs["lat"], roofs["lon"] = pt.y.round(6), pt.x.round(6)
    roofs["group"] = None
    roofs.loc[(roofs.label == "ac") & (roofs.p_raw >= THRESH), "group"] = "agree"
    roofs.loc[(roofs.label == "ac") & (roofs.p_raw < THRESH), "group"] = "mapped_not_seen"
    roofs.loc[(roofs.label == "none") & (roofs.p_raw >= THRESH), "group"] = "suspected_new"
    return roofs


def geojson(gdf: gpd.GeoDataFrame, cols: list[str]) -> dict:
    """GeoJSON with coordinates rounded to 1e-6 degrees (~0.1 m, below the photo's pixel) and no per-feature bbox:
    the coordinates are most of the html, and full float precision doubled them. String properties are HTML-escaped
    because folium's GeoJsonTooltip puts them into the page with innerHTML: a town or parcel name stays text."""
    g = gdf[cols + ["geometry"]].copy()
    for c in cols:
        g[c] = g[c].map(lambda v: escape(v) if isinstance(v, str) else v)
    g["geometry"] = shapely.transform(g.geometry.values, lambda c: np.round(c, 6))
    return g.to_geo_dict(na="null", show_bbox=False, drop_id=True)


def tooltip(gdf: gpd.GeoDataFrame, fields: list[str], aliases: list[str]):
    return folium.GeoJsonTooltip(fields, aliases=aliases) if len(gdf) else None  # folium needs a feature to check fields


def add_inputs(m: folium.Map, roofs: gpd.GeoDataFrame, raw: Path, parcel_refs: set) -> None:
    surveyed = gpd.read_file(raw / "lombardy_pilot.gpkg", layer="surveyed_2020").to_crs(4326)
    folium.GeoJson(geojson(surveyed, []), name="Input: area surveyed by ARPA in 2020", show=False,
                   style_function=lambda f: {"color": "#ffffff", "weight": 2, "dashArray": "6 4", "fillOpacity": 0}).add_to(m)
    ac = gpd.read_file(raw / "lombardy_pilot.gpkg", layer="ac_2020")
    ac["area_m2"] = ac.area.round(0).values
    folium.GeoJson(geojson(ac.to_crs(4326), ["area_m2"]), name=f"Input: ARPA Lombardia asbestos polygons 2020 ({len(ac)})", show=False,
                   style_function=lambda f: {"color": "#e31a1c", "weight": 1.5, "fillColor": "#e31a1c", "fillOpacity": 0.35},
                   tooltip=tooltip(ac, ["area_m2"], ["ARPA AC polygon m2"])).add_to(m)
    fp = roofs[roofs.area_m2 >= 500]
    folium.GeoJson(geojson(fp, ["area_m2", "label_txt"]), name=f"Input: building footprints >= 500 m2, Overture ({len(fp)})", show=False,
                   style_function=lambda f: {"color": "#ffff00", "weight": 1, "fillOpacity": 0},
                   tooltip=tooltip(fp, ["area_m2", "label_txt"], ["Roof m2", "ARPA label"])).add_to(m)
    files = glob.glob(str(raw / "cadastre/[0-9]*.gpkg"))
    if parcel_refs and files:
        parts = [gpd.read_file(f) for f in files]
        parcels = pd.concat([p[p["ref"].isin(parcel_refs)] for p in parts if len(p)], ignore_index=True).drop_duplicates("ref")
        parcels = gpd.GeoDataFrame(parcels, geometry="geometry", crs=6706).to_crs(4326)
        if len(parcels):
            folium.GeoJson(geojson(parcels, ["ref", "comune", "foglio", "particella"]),
                           name=f"Input: cadastral parcels of candidate roofs ({len(parcels)})",
                           show=False, style_function=lambda f: {"color": "#00e5ff", "weight": 2, "fillOpacity": 0},
                           tooltip=folium.GeoJsonTooltip(["ref", "comune", "foglio", "particella"],
                                                         aliases=["Parcel", "Town code", "Sheet (foglio)", "Parcel (particella)"])).add_to(m)


def add_results(m: folium.Map, roofs: gpd.GeoDataFrame) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """The clickable roof layers. Each feature carries roof_id and its tooltip fields; a click opens the panel."""
    solar = roofs[(roofs.area_m2 >= 500) & (roofs.p_pv < 0.2)]
    folium.GeoJson(geojson(solar, ["roof_id", "comune", "area_m2", "p_pv"]),
                   name=f"Result: roofs >= 500 m2 with no solar panels in 2024 ({len(solar)})", show=False,
                   style_function=lambda f: {"color": SOLAR, "weight": 2, "fillColor": SOLAR, "fillOpacity": 0.15},
                   tooltip=tooltip(solar, ["comune", "area_m2", "p_pv"], ["Municipality", "Roof m2", "Solar panels score"]),
                   on_each_feature=folium.JsCode(REGISTER_JS)).add_to(m)
    r = roofs[roofs.group.notna()]
    for k in ["suspected_new", "mapped_not_seen", "agree"]:
        sub = r[r.group == k]
        folium.GeoJson(geojson(sub, ["roof_id", "comune", "area_m2", "p", "p_pv"]), name=f"{NAMES[k]} ({len(sub)})",
                       style_function=lambda f, col=COLORS[k]: {"color": col, "weight": 3, "fillColor": col, "fillOpacity": 0.15},
                       tooltip=tooltip(sub, ["comune", "area_m2", "p", "p_pv"], ["Municipality", "Roof m2", "Asbestos score", "Solar panels score"]),
                       on_each_feature=folium.JsCode(REGISTER_JS)).add_to(m)
    return r, solar


def add_boxes(m: folium.Map, r: gpd.GeoDataFrame, companies: bool) -> None:
    company = "<li><b>Company records</b>: the businesses' own websites (legal notice), VAT checked with EU VIES.</li>" if companies else ""
    sources = f"""
<details open class='rw-sources' style='position:fixed;top:10px;left:50px;z-index:9999;background:white;padding:8px 10px;
 max-width:min(420px,calc(100vw - 70px));max-height:70vh;overflow:auto;box-sizing:border-box;border:1px solid #999;font:12px sans-serif'>
<summary style='font-weight:bold;cursor:pointer'>Data used (click to hide)</summary>
<ul style='padding-left:16px;margin:6px 0'>
<li><b>Aerial photo</b>: Ortofoto AGEA 2024, 20 cm product served as ~0.4 m tiles. CC BY 4.0. Input to the model.</li>
<li><b>Sentinel-2 2024</b>: EOX cloudless mosaic, 10 m. CC BY-NC-SA 4.0. Shown for comparison: what free satellites see.</li>
<li><b>Asbestos labels</b>: Regione/ARPA Lombardia, mapping 2020 (layer 1) and surveyed area. Licence not stated.</li>
<li><b>Roof outlines</b>: Overture Maps 2026-10 (OpenStreetMap ODbL + Microsoft ML CDLA).</li>
<li><b>Model</b>: DINOv2 image features + LightGBM, scored on municipalities held out of training.</li>
<li><b>Solar labels</b>: Lombardy mapping layer 4 (roofs replaced with PV by 2018).</li>
<li><b>Cadastral parcels</b>: Agenzia delle Entrate, INSPIRE WFS, CC BY 4.0.</li>
<li><b>Addresses, businesses</b>: Overture addresses and places.</li>{company}
</ul>
Click a roof for the image the model saw, the property and who occupies it. All asbestos results are <b>suspected</b>;
only an inspection or lab test confirms.
</details>"""
    m.get_root().html.add_child(folium.Element(sources))
    legend = ("<div class='rw-legend' style='position:fixed;bottom:24px;left:10px;z-index:9999;background:white;padding:8px;"
              "max-width:calc(100vw - 40px);box-sizing:border-box;border:1px solid #999;font:12px sans-serif'>"
              "<div id='rw-search' class='rw-search'></div>"  # filled by the roof panel's script
              + "".join(f"<span style='color:{COLORS[k]}'>&#9632;</span> {NAMES[k].replace('Result: ', '')} ({(r.group == k).sum()})<br>" for k in COLORS)
              + f"Threshold: asbestos score &ge; {THRESH}</div>")
    m.get_root().html.add_child(folium.Element(legend))


def main(raw: Path = RAW, proc: Path = PROC, out: Path = OUT, html: Path | None = None) -> dict:
    """Write the map. raw, proc, out: data/raw, data/processed and outputs; html defaults to out/aerial_map.html."""
    raw, proc, out = Path(raw), Path(proc), Path(out)
    html = Path(html) if html else out / "aerial_map.html"
    roofs = load_roofs(proc, out)
    inputs = load_inputs(out)
    ws = inputs["worksheet"] if inputs["worksheet"] is not None else pd.DataFrame(columns=["roof_id"])

    c = roofs.union_all().centroid
    m = folium.Map(location=[c.y, c.x], zoom_start=14, tiles=None, max_zoom=21)
    folium.TileLayer(tiles="https://geoportale.agea.gov.it/image/rest/services/AgEA/Ortofoto_AgEA_2024/ImageServer/tile/{z}/{y}/{x}",
                     attr="Ortofoto AGEA 2024 (CC BY 4.0)", name="Aerial photo AGEA 2024, ~0.4 m (what the model saw)",
                     max_zoom=21, max_native_zoom=18).add_to(m)
    folium.TileLayer(tiles="https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg",
                     attr="Sentinel-2 cloudless 2024 by EOX IT Services GmbH (contains modified Copernicus Sentinel data 2024), CC BY-NC-SA 4.0",
                     name="Sentinel-2 2024, 10 m (free satellite, too coarse for roofs)", max_zoom=21, max_native_zoom=15).add_to(m)
    folium.TileLayer(tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                     attr="Imagery &copy; Esri, Maxar, Earthstar Geographics", name="Esri satellite (other date)", max_zoom=19).add_to(m)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", max_zoom=19).add_to(m)

    add_inputs(m, roofs, raw, set(ws["parcel_ref"].dropna()) if "parcel_ref" in ws.columns else set())
    r, solar = add_results(m, roofs)
    clickable = roofs[roofs.roof_id.isin(set(r.roof_id) | set(solar.roof_id))]
    details = build_details(pd.DataFrame(clickable.drop(columns="geometry")), **inputs)
    groups = {k: (NAMES[k].replace("Result: ", ""), COLORS[k]) for k in COLORS}
    groups[""] = ("Roof >= 500 m2 without solar panels, no asbestos result", SOLAR)  # clickable only in the solar layer
    chips = Path(os.path.relpath(proc / "chips", html.parent)).as_posix() + "/"
    m.get_root().html.add_child(folium.Element(panel_html(details, m.get_name(), groups, chips)))
    add_boxes(m, r, inputs["companies"] is not None)
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(str(html))
    print("groups:", r.group.value_counts().to_dict(), "| worksheet joined:", "parcel_ref" in ws.columns)
    summary = {"roofs_in_panel": len(details), "with_address": sum("address" in d for d in details.values()),
               "with_businesses": sum("occupants" in d for d in details.values()),
               "with_next_door": sum("next_door" in d for d in details.values()),
               "with_company_record": sum("companies" in d for d in details.values()),
               "sharing_parcel": sum("same_parcel" in d for d in details.values()),
               "small_model_roofs": sum("small_roof" in d for d in details.values()),
               "do_not_contact": sum("do_not_contact" in d for d in details.values()), "html_mb": round(html.stat().st_size / 1e6, 1)}
    print("panel:", summary, "| inputs:", {k: (len(v) if v is not None else "missing") for k, v in inputs.items()})
    return summary


if __name__ == "__main__":
    main()
