"""Local map of officially mapped asbestos roofs in the pilot -> outputs/asbestos_map.html (private: building level)."""
import sys
from pathlib import Path

import folium
import geopandas as gpd
import pandas as pd
from folium.plugins import MarkerCluster

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/asbestos_map.html"

pa = gpd.read_file(ROOT / "data/pilot_area.geojson").to_crs(32632)
pts = gpd.clip(gpd.read_file(ROOT / "data/raw/piemonte_labels.gpkg", layer="checked"), pa)
roofs = gpd.read_file(ROOT / "outputs/roofs.gpkg", layer="roofs")
AC_LABELS = {"ac": "Asbestos, most of roof", "ac_small_part": "Asbestos on part of roof",
             "partial": "Partly remediated", "ac_changed_since_label": "Asbestos at inspection, changed since"}
COLORS = {"ac": "#e41a1c", "ac_small_part": "#ff7f00", "partial": "#ffd92f", "ac_changed_since_label": "#984ea3"}

big = roofs[roofs["official_label"].isin(["ac", "ac_small_part", "partial"])].copy()
big["arpa_status"] = big["official_label"].map(AC_LABELS)
big.loc[big["status"] == "changed", "arpa_status"] = big["arpa_status"] + " (satellite: changed recently)"
big["kind"] = big["official_label"].where(big["status"] != "changed", "ac_changed_since_label")
big["label_date"] = pd.to_datetime(big["label_date"]).dt.strftime("%Y-%m-%d").fillna("unknown")
big["area_m2"] = big["area_m2"].round(0)
big["last_change_type"] = big["last_change_type"].fillna("")
g = big.to_crs(4326)
g["maps_link"] = [f"https://www.google.com/maps/@{p.y:.6f},{p.x:.6f},120m/data=!3m1!1e3" for p in g.representative_point()]

c = pa.to_crs(4326).union_all().centroid
m = folium.Map(location=[c.y, c.x], zoom_start=12, tiles=None)
folium.TileLayer(tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                 attr="Imagery &copy; Esri, Maxar, Earthstar Geographics", name="Satellite (Esri)", max_zoom=19).add_to(m)
# OpenStreetMap's tile servers block pages opened from a local file ('Access blocked'): keyless Esri street map
folium.TileLayer(tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
                     attr="Esri, HERE, Garmin, OpenStreetMap contributors", name="Street map (Esri)", max_zoom=19).add_to(m)

for kind in ["ac", "ac_small_part", "partial", "ac_changed_since_label"]:
    sub = g[g["kind"] == kind]
    if sub.empty:
        continue
    folium.GeoJson(
        sub[["roof_id", "comune", "tier", "area_m2", "arpa_status", "label_date", "last_change_type", "maps_link", "geometry"]],
        name=f"{AC_LABELS[kind]} &ge;500 m2 ({len(sub)})",
        style_function=lambda f, col=COLORS[kind]: {"color": col, "weight": 3, "fillColor": col, "fillOpacity": 0.25},
        tooltip=folium.GeoJsonTooltip(["comune", "area_m2", "arpa_status", "label_date", "last_change_type"],
                                      aliases=["Municipality", "Roof m2", "ARPA status", "ARPA inspection", "Satellite change"]),
        popup=folium.GeoJsonPopup(["maps_link"], aliases=["Google satellite view"]),
    ).add_to(m)

def point_layer(df, name, color, show):
    fg = folium.FeatureGroup(name=f"{name} ({len(df)})", show=show)
    cl = MarkerCluster(disableClusteringAtZoom=16).add_to(fg)
    d = df.to_crs(4326)
    for p, a in zip(d.geometry, d.itertuples()):
        when = pd.Timestamp(a.data_sopr).strftime("%Y-%m-%d") if pd.notna(a.data_sopr) and 2000 <= pd.Timestamp(a.data_sopr).year <= 2026 else "unknown"
        area = f"{a.mq_stimati:.0f} m2" if pd.notna(a.mq_stimati) and a.mq_stimati > 0 else "area unknown"
        tip = f"{a.desc_esito}<br>{a.desc_tipo_edif or 'building type unknown'}<br>{area}, inspected {when}"
        folium.CircleMarker([p.y, p.x], radius=4, color=color, fill=True, fill_opacity=0.9, weight=1, tooltip=tip).add_to(cl)
    fg.add_to(m)

point_layer(pts[pts["label"] == "ac"], "All ARPA asbestos points, any size", "#e41a1c", True)
point_layer(pts[pts["label"] == "removed"], "ARPA remediated points", "#4daf4a", False)

legend = (
    "<div style='position:fixed;bottom:24px;left:10px;z-index:9999;background:white;padding:8px;"
    "max-width:calc(100vw - 40px);box-sizing:border-box;border:1px solid #999;font:13px sans-serif'>"
    "<b>Asbestos roofs on record, Turin pilot</b><br>"
    "Source: ARPA Piemonte (CC BY 4.0). Records may be out of date;<br>only inspection or lab test confirms asbestos.<br>"
    + "".join(f"<span style='color:{COLORS[k]}'>&#9632;</span> {AC_LABELS[k]} ({(g.kind == k).sum()})<br>" for k in COLORS if (g.kind == k).any())
    + f"<span style='color:#e41a1c'>&#9679;</span> ARPA asbestos points, any size ({(pts.label == 'ac').sum()})<br>"
    "Click a roof for a Google satellite link.</div>")
m.get_root().html.add_child(folium.Element(legend))
folium.LayerControl(collapsed=False).add_to(m)
m.save(str(OUT))
print(OUT, "| roofs >=500 m2:", g["kind"].value_counts().to_dict(), "| points ac:", int((pts.label == "ac").sum()), "removed:", int((pts.label == "removed").sum()))
