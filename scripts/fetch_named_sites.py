"""Named farm, industrial and commercial sites around the pilot -> data/raw/osm_named_pilot.gpkg.

Source: Overture Maps (land use and building names, which come from OpenStreetMap, ODbL), read from Overture's S3
release. OpenStreetMap's own Overpass API and tile servers reject this client (406 / "Access blocked"), so they are not
used. Output columns match what roofwatch.occupants.prepare_osm reads: osm_name, osm_operator, osm_kind, osm_website, point
geometry (a point on each feature).
"""
import sys
from pathlib import Path

import duckdb
import geopandas as gpd
import shapely

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "s3://overturemaps-us-west-2/release/2026-10-21.0"
LAND_USE = ("farmyard", "industrial", "commercial", "retail")
SKIP_BUILDINGS = ("church", "chapel", "school", "kindergarten", "university", "house", "residential", "apartments",
                  "detached", "semidetached_house", "terrace", "hospital", "cathedral", "religious")

mun = gpd.read_file(ROOT / "data/raw/lombardy_pilot.gpkg", layer="municipalities").to_crs(4326)
w, s, e, n = mun.total_bounds
bb = f"bbox.xmin < {e} AND bbox.xmax > {w} AND bbox.ymin < {n} AND bbox.ymax > {s}"
con = duckdb.connect()
con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
tag = lambda k: f"map_extract(source_tags, '{k}')[1]"
q_land = f"""SELECT 'overture:' || id AS osm_id, names.primary AS osm_name, {tag('operator')} AS osm_operator, class AS osm_kind,
      coalesce({tag('website')}, {tag('contact:website')}) AS osm_website, ST_AsWKB(ST_PointOnSurface(geometry)) AS wkb
    FROM read_parquet('{RELEASE}/theme=base/type=land_use/*', hive_partitioning=1)
    WHERE {bb} AND (names.primary IS NOT NULL OR {tag('operator')} IS NOT NULL) AND class IN {LAND_USE}"""
skip = ",".join(f"'{c}'" for c in SKIP_BUILDINGS)
q_bld = f"""SELECT 'overture:' || id AS osm_id, names.primary AS osm_name, NULL AS osm_operator, coalesce(class, 'building') AS osm_kind,
      NULL AS osm_website, ST_AsWKB(ST_PointOnSurface(geometry)) AS wkb
    FROM read_parquet('{RELEASE}/theme=buildings/type=building/*', hive_partitioning=1)
    WHERE {bb} AND names.primary IS NOT NULL AND (class IS NULL OR class NOT IN ({skip}))"""
df = con.execute(f"{q_land} UNION ALL {q_bld}").df()
g = gpd.GeoDataFrame(df.drop(columns="wkb"), geometry=shapely.from_wkb([bytes(b) for b in df["wkb"]]), crs=4326)
out = ROOT / "data/raw/osm_named_pilot.gpkg"
tmp = out.with_name(".osm_named_pilot.tmp.gpkg")
g.to_file(tmp, layer="named_sites", driver="GPKG")
tmp.replace(out)
print(f"{len(g)} named sites -> {out.relative_to(ROOT)} | kinds: {g['osm_kind'].value_counts().head(8).to_dict()} | "
      f"with operator {g['osm_operator'].notna().sum()}, with website {g['osm_website'].notna().sum()}")
