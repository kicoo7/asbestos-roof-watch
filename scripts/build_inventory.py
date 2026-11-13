"""T1.2 + T1.3 (label join): pilot roof inventory with tiers, labels, municipality, home tile and pixel lookup."""
import pickle
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pystac_client import Client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from roofwatch.extract import COLLECTION, STAC_URL, assign_home_tiles, build_lookup, tile_code, tile_extent
from roofwatch.footprints import join_labels, load_overture
from roofwatch.inventory import build_inventory

pa = gpd.read_file(ROOT / "data/pilot_area.geojson").to_crs(32632)
fp = load_overture(ROOT / "data/raw/overture_buildings.parquet")
fp = fp[fp.representative_point().within(pa.union_all())]
inv = build_inventory(fp, inner_buffer_m=10)
cent = gpd.GeoDataFrame(inv[["roof_id"]], geometry=inv.representative_point(), crs=inv.crs)
inv["comune"] = gpd.sjoin(cent, pa[["comune_nom", "geometry"]], predicate="within").drop_duplicates("roof_id").set_index("roof_id")["comune_nom"].reindex(inv["roof_id"]).values

lab = gpd.read_file(ROOT / "data/raw/piemonte_labels.gpkg", layer="checked")
cand = gpd.read_file(ROOT / "data/raw/piemonte_labels.gpkg", layer="candidates")
cand["label"] = "candidate"
cand["data_sopr"] = pd.NaT
d = lab["data_sopr"]
lab["data_sopr"] = pd.to_datetime(d.where((d >= pd.Timestamp("2000-01-01")) & (d <= pd.Timestamp.now())).dt.strftime("%Y-%m-%d"))
allp = pd.concat([lab[["label", "mq_stimati", "data_sopr", "geometry"]], cand[["label", "mq_stimati", "data_sopr", "geometry"]]], ignore_index=True)
allp = gpd.GeoDataFrame(allp, geometry="geometry", crs=32632)
inv = inv.merge(join_labels(inv, allp, max_dist_m=15), on="roof_id")

# Home tile per roof, from the full MGRS square of a recent item per tile
items = Client.open(STAC_URL).search(collections=[COLLECTION], bbox=list(pa.to_crs(4326).total_bounds), datetime="2026-10-01/2026-11-13", max_items=50).items()
tiles = {}
for it in items:
    tiles.setdefault(tile_code(it), tile_extent(it).buffer(-30))
priority = sorted(tiles)
roofs_sample = inv.set_geometry("sample_geom")
inv["tile"] = assign_home_tiles(roofs_sample, tiles, priority).values
print("tiles:", priority, inv["tile"].value_counts(dropna=False).to_dict())

lookups = {}
for code, g in inv[inv["tile"].notna()].groupby("tile"):
    lookups[code] = build_lookup(g)
    b, t, shape, lut = lookups[code]
    print(code, "window", shape, "roofs", len(lut), "median px/roof", int(pd.Series([len(r) for r, c in lut.values()]).median()))
# Roofs with no pixel centre inside the sampling polygon cannot be observed: drop them.
n_px = {rid: len(rc[0]) for code in lookups for rid, rc in lookups[code][3].items()}
inv["n_px"] = inv["roof_id"].map(n_px).fillna(0).astype(int)
print("dropping roofs with 0 pixels:", int((inv["n_px"] == 0).sum()))
inv = inv[inv["n_px"] > 0].copy()
for code in lookups:
    b, t, shape, lut = lookups[code]
    lookups[code] = (b, t, shape, {rid: rc for rid, rc in lut.items() if len(rc[0]) > 0})
with open(ROOT / "data/processed/lookups.pkl", "wb") as f:
    pickle.dump(lookups, f)

out = inv.drop(columns=["sample_geom"]).copy()
out["label_date"] = out["label_date"].astype("datetime64[ns]")
out.to_file(ROOT / "data/processed/roofs_inventory.gpkg", layer="roofs", driver="GPKG")
inv.set_geometry("sample_geom")[["roof_id", "sample_geom"]].rename_geometry("geometry").to_file(ROOT / "data/processed/roofs_inventory.gpkg", layer="sample", driver="GPKG")
print("roofs:", len(inv), inv["tier"].value_counts().to_dict())
print(pd.crosstab(inv["label"], inv["tier"]))
print("municipalities:", inv["comune"].nunique(), "missing comune:", inv["comune"].isna().sum())
