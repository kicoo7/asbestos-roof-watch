"""Local map of the aerial asbestos model on the Brescia pilot -> outputs/aerial_map.html (private: building level)."""
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
THRESH = 0.7  # ~53-59% of flags are mapped AC for roofs >= 500 m2, finding 70-75% of them (held-out towns)
roofs = gpd.read_file(ROOT / "data/processed/lombardy_roofs.gpkg", layer="roofs")
oof = pd.read_parquet(ROOT / "outputs/aerial_oof.parquet")[["roof_id", "p"]]
r = roofs.merge(oof, on="roof_id", how="inner")
r["group"] = None
r.loc[(r.label == "ac") & (r.p >= THRESH), "group"] = "agree"
r.loc[(r.label == "ac") & (r.p < THRESH), "group"] = "mapped_not_seen"
r.loc[(r.label == "none") & (r.p >= THRESH), "group"] = "suspected_new"
r = r[r.group.notna()].to_crs(4326)
r["p"] = r["p"].round(2)
r["area_m2"] = r["area_m2"].round(0)
r["maps_link"] = [f"https://www.google.com/maps/@{p.y:.6f},{p.x:.6f},120m/data=!3m1!1e3" for p in r.representative_point()]
NAMES = {"agree": "On ARPA map, model agrees", "mapped_not_seen": "On ARPA map, model does not see AC (replaced?)",
         "suspected_new": "Not on ARPA map, model suspects AC"}
COLORS = {"agree": "#d7191c", "mapped_not_seen": "#2b83ba", "suspected_new": "#fdae61"}

c = r.union_all().centroid
m = folium.Map(location=[c.y, c.x], zoom_start=13, tiles=None)
folium.TileLayer(tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                 attr="Imagery &copy; Esri, Maxar, Earthstar Geographics", name="Satellite (Esri)", max_zoom=19).add_to(m)
folium.TileLayer(tiles="https://geoportale.agea.gov.it/image/rest/services/AgEA/Ortofoto_AgEA_2024/ImageServer/tile/{z}/{y}/{x}",
                 attr="Ortofoto AGEA 2024, CC BY 4.0", name="AGEA 2024 (what the model saw)", max_zoom=21, max_native_zoom=18).add_to(m)
folium.TileLayer("OpenStreetMap", name="Map (OpenStreetMap)").add_to(m)
for k in ["suspected_new", "mapped_not_seen", "agree"]:
    sub = r[r.group == k]
    folium.GeoJson(sub[["comune", "area_m2", "size_band", "p", "maps_link", "geometry"]], name=f"{NAMES[k]} ({len(sub)})",
                   style_function=lambda f, col=COLORS[k]: {"color": col, "weight": 3, "fillColor": col, "fillOpacity": 0.2},
                   tooltip=folium.GeoJsonTooltip(["comune", "area_m2", "size_band", "p"], aliases=["Municipality", "Roof m2", "Size", "Model score"]),
                   popup=folium.GeoJsonPopup(["maps_link"], aliases=["Google satellite view"])).add_to(m)
legend = ("<div style='position:fixed;bottom:24px;left:10px;z-index:9999;background:white;padding:8px;max-width:calc(100vw - 40px);"
          "box-sizing:border-box;border:1px solid #999;font:13px sans-serif'><b>Aerial asbestos model, Brescia plain</b><br>"
          "Model: DINOv2 + LightGBM on AGEA 2024 photos; scores from held-out municipalities.<br>"
          f"Labels: ARPA Lombardia 2020 mapping. Threshold: score >= {THRESH}. All results are suspected only.<br>"
          + "".join(f"<span style='color:{COLORS[k]}'>&#9632;</span> {NAMES[k]} ({(r.group == k).sum()})<br>" for k in COLORS)
          + "</div>")
m.get_root().html.add_child(folium.Element(legend))
folium.LayerControl(collapsed=False).add_to(m)
m.save(str(ROOT / "outputs/aerial_map.html"))
print(r.group.value_counts().to_dict(), "| by size:", pd.crosstab(r.group, r.size_band).to_dict())
