"""Brescia-plain aerial pilot: roofs (Overture) labelled by 2020 AC polygon cover -> data/processed/lombardy_roofs.gpkg."""
import sys
from pathlib import Path

import duckdb
import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from roofwatch.footprints import load_overture, polygon_cover
from roofwatch.labels import fetch_arcgis_layer
from roofwatch.lombardy import LAYER_AC_2020, LAYER_AREAS_2020, fetch_polygons

AGEA_COMUNI = "https://geoportale.agea.gov.it/server/rest/services/AgEA/Limiti_Amministrativi/FeatureServer/3"

CODES = ["03017088", "03017103", "03017125", "03017009", "03017195", "03017159", "03017020", "03017138",
         "03017196", "03017122", "03017066", "03017137", "03017146", "03017053", "03017013", "03017011"]
MIN_M2 = 100
RAW, PROC = ROOT / "data/raw", ROOT / "data/processed"

# the Lombardy service's municipality layer returns no geometry: use AGEA's administrative limits
feats = fetch_arcgis_layer(AGEA_COMUNI, where=f"pro_com IN ({','.join(str(int(c[2:])) for c in CODES)})")
mun = gpd.GeoDataFrame.from_features(feats, crs="EPSG:32632").rename(columns={"comune": "NOME_COM", "pro_com": "ISTAT"})
mun = mun[["NOME_COM", "ISTAT", "geometry"]]
quoted = ",".join(f"'{c}'" for c in CODES)
ac = fetch_polygons(LAYER_AC_2020, where=f"COD_ISTATN IN ({quoted})")
areas = gpd.clip(fetch_polygons(LAYER_AREAS_2020), mun.union_all())
print("municipalities", len(mun), "| AC polygons 2020", len(ac), "| surveyed area km2", round(areas.area.sum() / 1e6, 1))
mun.to_file(RAW / "lombardy_pilot.gpkg", layer="municipalities", driver="GPKG")
ac.to_file(RAW / "lombardy_pilot.gpkg", layer="ac_2020", driver="GPKG")
areas.to_file(RAW / "lombardy_pilot.gpkg", layer="surveyed_2020", driver="GPKG")

ov = RAW / "overture_lombardy_pilot.parquet"
if not ov.exists():
    w, s, e, n = mun.to_crs(4326).total_bounds
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    con.execute(f"""COPY (SELECT id, sources[1].dataset AS src, subtype, class, ST_AsWKB(geometry) AS wkb
      FROM read_parquet('s3://overturemaps-us-west-2/release/2026-10-21.0/theme=buildings/type=building/*', hive_partitioning=1)
      WHERE bbox.xmin < {e} AND bbox.xmax > {w} AND bbox.ymin < {n} AND bbox.ymax > {s}) TO '{ov}' (FORMAT parquet)""")
fp = load_overture(ov)
fp["area_m2"] = fp.area
fp = fp[fp["area_m2"] >= MIN_M2]
rp = gpd.GeoDataFrame(fp[[]], geometry=fp.representative_point(), crs=fp.crs)
inside = gpd.sjoin(rp, areas[["geometry"]], predicate="within").index.unique()
fp = fp.loc[inside]
fp = fp.join(gpd.sjoin(gpd.GeoDataFrame(fp[[]], geometry=fp.representative_point(), crs=fp.crs),
                       mun[["NOME_COM", "geometry"]], predicate="within")["NOME_COM"].groupby(level=0).first())
fp = fp.rename(columns={"NOME_COM": "comune"}).reset_index(drop=True)
fp["roof_id"] = range(len(fp))
fp["ac_cover"] = polygon_cover(fp, ac).values
fp["label"] = pd.cut(fp["ac_cover"], [-1, 0.05, 0.5, 2], labels=["none", "ac_part", "ac"]).astype(str)
fp["size_band"] = pd.cut(fp["area_m2"], [0, 500, 2000, 1e9], labels=["100-500", "500-2000", ">=2000"]).astype(str)

# how many AC polygons got a matching roof
acj = gpd.sjoin(ac[["geometry"]].assign(a=ac.area), fp[fp.label == "ac"][["geometry"]], predicate="intersects")
print(f"AC polygons >= {MIN_M2} m2 matched to an 'ac' roof: {acj.index.nunique()} of {(ac.area >= MIN_M2).sum()}")
print(pd.crosstab(fp["label"], fp["size_band"], margins=True))
print("municipalities with roofs:", fp["comune"].nunique(), "| sources:", fp["src"].value_counts().to_dict())
fp.drop(columns=["wkb"], errors="ignore").to_file(PROC / "lombardy_roofs.gpkg", layer="roofs", driver="GPKG")
