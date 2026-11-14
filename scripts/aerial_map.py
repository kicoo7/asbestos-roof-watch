"""Local map of the aerial pilot showing every input and output -> outputs/aerial_map.html (private: building level).

Base layers: AGEA 2024 orthophoto (what the model saw), Sentinel-2 cloudless 2024 (10 m, for comparison), Esri, OSM.
Overlays: ARPA Lombardia 2020 AC polygons and surveyed area (labels), Overture footprints, cadastral parcels of
candidate roofs, model results, solar. Roof popups show the exact chip the model saw (relative link to
data/processed/chips, so open the file from outputs/).
"""
import glob
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
THRESH = 0.7  # ~53-59% of flags are mapped AC for roofs >= 500 m2, finding 70-75% of them (held-out towns)
RAW, PROC, OUT = ROOT / "data/raw", ROOT / "data/processed", ROOT / "outputs"

roofs = gpd.read_file(PROC / "lombardy_roofs.gpkg", layer="roofs")
roofs = roofs.merge(pd.read_parquet(OUT / "aerial_oof.parquet")[["roof_id", "p"]], on="roof_id", how="left")
roofs = roofs.merge(pd.read_parquet(OUT / "pilot_pv_scores.parquet"), on="roof_id", how="left")
ws_path = OUT / "contact_worksheet.csv"
ws = pd.read_csv(ws_path) if ws_path.exists() else pd.DataFrame(columns=["roof_id"])
keep_ws = [c for c in ["roof_id", "parcel_ref", "comune_code", "foglio", "particella", "address", "business_name",
                       "business_category", "business_website", "known_company", "known_company_source", "street_view"] if c in ws.columns]
roofs = roofs.merge(ws[keep_ws], on="roof_id", how="left")
roofs = roofs.to_crs(4326)
roofs["p_raw"] = roofs["p"]  # threshold on the unrounded score
for c in ("p", "p_pv"):
    roofs[c] = roofs[c].round(2)
roofs["area_m2"] = roofs["area_m2"].round(0)
roofs["label_txt"] = roofs["label"].map({"ac": "mapped AC (>=50% of roof)", "ac_part": "AC on part of roof", "none": "not mapped"})
pt = roofs.representative_point()
roofs["chip"] = [f'<img src="../data/processed/chips/{r}.jpg" width="180" height="180" style="background:#000">' for r in roofs["roof_id"]]
roofs["links"] = [f'<a href="https://www.google.com/maps/@{p.y:.6f},{p.x:.6f},120m/data=!3m1!1e3" target="_blank">Google satellite</a> · '
                  f'<a href="https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={p.y:.6f},{p.x:.6f}" target="_blank">Street View</a>'
                  for p in pt]
roofs = roofs.fillna({c: "" for c in keep_ws if c != "roof_id"})

roofs["group"] = None
roofs.loc[(roofs.label == "ac") & (roofs.p_raw >= THRESH), "group"] = "agree"
roofs.loc[(roofs.label == "ac") & (roofs.p_raw < THRESH), "group"] = "mapped_not_seen"
roofs.loc[(roofs.label == "none") & (roofs.p_raw >= THRESH), "group"] = "suspected_new"
NAMES = {"agree": "Result: on ARPA map, model agrees", "mapped_not_seen": "Result: on ARPA map, model does not see AC (replaced?)",
         "suspected_new": "Result: not on ARPA map, model suspects AC"}
COLORS = {"agree": "#d7191c", "mapped_not_seen": "#2b83ba", "suspected_new": "#fdae61"}
POP = ["chip", "label_txt", "p", "p_pv", "area_m2", "comune", "parcel_ref", "address", "known_company", "known_company_source",
       "business_website", "links"]
POP = [c for c in POP if c in roofs.columns]
POP_ALIAS = {"chip": "What the model saw", "label_txt": "ARPA label", "p": "Asbestos score", "p_pv": "Solar panels score",
             "area_m2": "Roof m2", "comune": "Municipality", "parcel_ref": "Cadastral parcel", "address": "Address",
             "business_name": "Business at building", "known_company": "Company on site (best guess)",
             "known_company_source": "Company source", "business_website": "Website", "links": "Check"}

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

# --- inputs -----------------------------------------------------------------------------------
surveyed = gpd.read_file(RAW / "lombardy_pilot.gpkg", layer="surveyed_2020").to_crs(4326)
folium.GeoJson(surveyed[["geometry"]], name="Input: area surveyed by ARPA in 2020", show=False,
               style_function=lambda f: {"color": "#ffffff", "weight": 2, "dashArray": "6 4", "fillOpacity": 0}).add_to(m)
ac = gpd.read_file(RAW / "lombardy_pilot.gpkg", layer="ac_2020").to_crs(4326)
ac["area_m2"] = gpd.read_file(RAW / "lombardy_pilot.gpkg", layer="ac_2020").area.round(0).values
folium.GeoJson(ac[["area_m2", "geometry"]], name=f"Input: ARPA Lombardia asbestos polygons 2020 ({len(ac)})", show=False,
               style_function=lambda f: {"color": "#e31a1c", "weight": 1.5, "fillColor": "#e31a1c", "fillOpacity": 0.35},
               tooltip=folium.GeoJsonTooltip(["area_m2"], aliases=["ARPA AC polygon m2"])).add_to(m)
fp = roofs[roofs.area_m2 >= 500]
folium.GeoJson(fp[["area_m2", "label_txt", "geometry"]], name=f"Input: building footprints >= 500 m2, Overture ({len(fp)})", show=False,
               style_function=lambda f: {"color": "#ffff00", "weight": 1, "fillOpacity": 0},
               tooltip=folium.GeoJsonTooltip(["area_m2", "label_txt"], aliases=["Roof m2", "ARPA label"])).add_to(m)
if "parcel_ref" in ws.columns and len(glob.glob(str(RAW / "cadastre/*.gpkg"))):
    refs = set(ws["parcel_ref"].dropna())
    parts = [gpd.read_file(f) for f in glob.glob(str(RAW / "cadastre/[0-9]*.gpkg"))]
    parcels = pd.concat([p[p["ref"].isin(refs)] for p in parts if len(p)], ignore_index=True).drop_duplicates("ref")
    parcels = gpd.GeoDataFrame(parcels, geometry="geometry", crs=6706).to_crs(4326)
    if len(parcels):
        folium.GeoJson(parcels[["ref", "comune", "foglio", "particella", "geometry"]], name=f"Input: cadastral parcels of candidate roofs ({len(parcels)})",
                       show=False, style_function=lambda f: {"color": "#00e5ff", "weight": 2, "fillOpacity": 0},
                       tooltip=folium.GeoJsonTooltip(["ref", "comune", "foglio", "particella"],
                                                     aliases=["Parcel", "Town code", "Sheet (foglio)", "Parcel (particella)"])).add_to(m)

# --- outputs ----------------------------------------------------------------------------------
solar = roofs[(roofs.area_m2 >= 500) & (roofs.p_pv < 0.2)]
folium.GeoJson(solar[POP + ["geometry"]], name=f"Result: roofs >= 500 m2 with no solar panels in 2024 ({len(solar)})", show=False,
               style_function=lambda f: {"color": "#1a9641", "weight": 2, "fillColor": "#1a9641", "fillOpacity": 0.15},
               tooltip=folium.GeoJsonTooltip(["comune", "area_m2", "p_pv"], aliases=["Municipality", "Roof m2", "Solar panels score"]),
               popup=folium.GeoJsonPopup(POP, aliases=[POP_ALIAS[x] for x in POP], max_width=320)).add_to(m)
r = roofs[roofs.group.notna()]
for k in ["suspected_new", "mapped_not_seen", "agree"]:
    sub = r[r.group == k]
    folium.GeoJson(sub[POP + ["geometry"]], name=f"{NAMES[k]} ({len(sub)})",
                   style_function=lambda f, col=COLORS[k]: {"color": col, "weight": 3, "fillColor": col, "fillOpacity": 0.15},
                   tooltip=folium.GeoJsonTooltip(["comune", "area_m2", "p", "p_pv"], aliases=["Municipality", "Roof m2", "Asbestos score", "Solar panels score"]),
                   popup=folium.GeoJsonPopup(POP, aliases=[POP_ALIAS[x] for x in POP], max_width=320)).add_to(m)

sources = """
<details open style='position:fixed;top:10px;left:50px;z-index:9999;background:white;padding:8px 10px;max-width:min(420px,calc(100vw - 70px));
 max-height:70vh;overflow:auto;box-sizing:border-box;border:1px solid #999;font:12px sans-serif'>
<summary style='font-weight:bold;cursor:pointer'>Data used (click to hide)</summary>
<ul style='padding-left:16px;margin:6px 0'>
<li><b>Aerial photo</b>: Ortofoto AGEA 2024, 20 cm product served as ~0.4 m tiles. CC BY 4.0. Input to the model.</li>
<li><b>Sentinel-2 2024</b>: EOX cloudless mosaic, 10 m. CC BY-NC-SA 4.0. Shown for comparison: what free satellites see.</li>
<li><b>Asbestos labels</b>: Regione/ARPA Lombardia, mapping 2020 (layer 1) and surveyed area. Licence not stated.</li>
<li><b>Roof outlines</b>: Overture Maps 2026-10 (OpenStreetMap ODbL + Microsoft ML CDLA).</li>
<li><b>Model</b>: DINOv2 image features + LightGBM, scored on municipalities held out of training.</li>
<li><b>Solar labels</b>: Lombardy mapping layer 4 (roofs replaced with PV by 2018).</li>
<li><b>Cadastral parcels</b>: Agenzia delle Entrate, INSPIRE WFS, CC BY 4.0.</li>
<li><b>Addresses, businesses</b>: Overture addresses and places.</li>
</ul>
Click a roof to see the image the model saw. All asbestos results are <b>suspected</b>; only an inspection or lab test confirms.
</details>"""
m.get_root().html.add_child(folium.Element(sources))
legend = ("<div style='position:fixed;bottom:24px;left:10px;z-index:9999;background:white;padding:8px;max-width:calc(100vw - 40px);"
          "box-sizing:border-box;border:1px solid #999;font:12px sans-serif'>"
          + "".join(f"<span style='color:{COLORS[k]}'>&#9632;</span> {NAMES[k].replace('Result: ', '')} ({(r.group == k).sum()})<br>" for k in COLORS)
          + f"Threshold: asbestos score &ge; {THRESH}</div>")
m.get_root().html.add_child(folium.Element(legend))
folium.LayerControl(collapsed=False).add_to(m)
m.save(str(OUT / "aerial_map.html"))
print("groups:", r.group.value_counts().to_dict(), "| worksheet joined:", "parcel_ref" in ws.columns)
