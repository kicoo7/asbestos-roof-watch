"""Contact worksheet for strong candidates: roof -> cadastral parcel, address, business at the building.

Owner names are NOT fetched: look them up one by one (visura per immobile) with the parcel reference.
Phones and emails are deliberately left out: first contact by letter or visit (see docs/CONTACTING.md).
-> outputs/contact_worksheet.csv (private, git-ignored)
"""
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from roofwatch.cadastre import assign_parcels, fetch_parcels

THRESH, MIN_M2, CELL_DEG = 0.7, 500, 0.005  # ~400-550 m cells for parcel requests
RAW = ROOT / "data/raw"
roofs = gpd.read_file(ROOT / "data/processed/lombardy_roofs.gpkg", layer="roofs")
oof = pd.read_parquet(ROOT / "outputs/aerial_oof.parquet")[["roof_id", "p"]]
r = roofs.merge(oof, on="roof_id")
r = r[(r.p >= THRESH) & (r.area_m2 >= MIN_M2) & r.label.isin(["ac", "none"])].copy()
r["status"] = np.where(r.label == "ac", "on ARPA map, model agrees", "suspected, not on ARPA map")
print(len(r), "candidate roofs", r.status.value_counts().to_dict(), flush=True)

# cadastral parcels, fetched per small cell around candidates and cached
cache = RAW / "cadastre"; cache.mkdir(exist_ok=True)
r4326 = r.to_crs(4326)
cells = sorted({(math.floor(p.x / CELL_DEG), math.floor(p.y / CELL_DEG)) for p in r4326.representative_point()})


def cell_parcels(c):
    f = cache / f"{c[0]}_{c[1]}.gpkg"
    if not f.exists():
        b = (c[0] * CELL_DEG - 0.001, c[1] * CELL_DEG - 0.001, (c[0] + 1) * CELL_DEG + 0.001, (c[1] + 1) * CELL_DEG + 0.001)
        g = fetch_parcels(b)
        tmp = f.with_name(f".{f.stem}.tmp.gpkg")  # atomic: an interrupted run never leaves a half-written cache file
        (g if len(g) else gpd.GeoDataFrame({"ref": []}, geometry=[], crs=6706)).to_file(tmp, driver="GPKG")
        tmp.replace(f)
    return gpd.read_file(f)


with ThreadPoolExecutor(max_workers=3) as ex:
    parts = list(ex.map(cell_parcels, cells))
parcels = pd.concat([p for p in parts if len(p)], ignore_index=True).drop_duplicates("ref")
parcels = gpd.GeoDataFrame(parcels, geometry="geometry", crs=6706)
print(len(cells), "cells,", len(parcels), "parcels", flush=True)
r = r.merge(assign_parcels(r[["roof_id", "geometry"]], parcels), on="roof_id", how="left")
ref = r["parcel_ref"].fillna("").str.extract(r"^(?P<comune_code>[A-Z]\d{3})_(?P<foglio>\d{4})\w\w\.(?P<particella>.+)$")
r = r.join(ref)
r["foglio"] = pd.to_numeric(r["foglio"], errors="coerce").astype("Int64")

# nearest address point within 60 m
ad = pd.read_parquet(RAW / "overture_addresses_pilot.parquet")
ad = gpd.GeoDataFrame(ad, geometry=shapely.from_wkb(ad.pop("wkb")), crs=4326).to_crs(r.crs)
ad["address"] = (ad["street"].fillna("") + " " + ad["number"].fillna("")).str.strip() + ", " + ad["postcode"].fillna("") + " " + ad["postal_city"].fillna("")
j = gpd.sjoin_nearest(r[["roof_id", "geometry"]], ad[["address", "geometry"]], max_distance=60, distance_col="address_dist_m")
r = r.merge(j.drop_duplicates("roof_id")[["roof_id", "address", "address_dist_m"]], on="roof_id", how="left")

# business located at the building (inside, or within 30 m), highest confidence first
pl = pd.read_parquet(RAW / "overture_places_pilot.parquet")
pl = gpd.GeoDataFrame(pl, geometry=shapely.from_wkb(pl.pop("wkb")), crs=4326).to_crs(r.crs)
pl = pl[pl["operating_status"].fillna("open") != "permanently_closed"]
jp = gpd.sjoin_nearest(r[["roof_id", "geometry"]], pl[["name", "category", "website", "address", "confidence", "geometry"]].rename(columns={"address": "business_address"}),
                       max_distance=30, distance_col="business_dist_m")
jp = jp.sort_values(["roof_id", "business_dist_m", "confidence"], ascending=[True, True, False]).drop_duplicates("roof_id")
r = r.merge(jp[["roof_id", "name", "category", "website", "business_address", "business_dist_m"]].rename(
    columns={"name": "business_name", "category": "business_category", "website": "business_website"}), on="roof_id", how="left")

pt = r.to_crs(4326).representative_point()
r["google_maps"] = [f"https://www.google.com/maps/@{p.y:.6f},{p.x:.6f},120m/data=!3m1!1e3" for p in pt]
r["lat"], r["lon"] = pt.y.round(6).values, pt.x.round(6).values
for c in ["owner_from_visura", "owner_type", "contact_method", "contact_date", "outcome", "do_not_contact", "notes"]:
    r[c] = ""
r = r.sort_values(["area_m2"], ascending=False).reset_index(drop=True)
r.insert(0, "priority", range(1, len(r) + 1))
cols = ["priority", "roof_id", "status", "comune", "area_m2", "p", "comune_code", "foglio", "particella", "parcel_ref", "parcel_share",
        "other_parcels", "address", "address_dist_m", "business_name", "business_category", "business_website", "business_address",
        "business_dist_m", "lat", "lon", "google_maps", "owner_from_visura", "owner_type", "contact_method", "contact_date",
        "outcome", "do_not_contact", "notes"]
out = r[cols].rename(columns={"p": "model_score"})
out["area_m2"] = out["area_m2"].round(0)
out["model_score"] = out["model_score"].round(2)
for c in ("parcel_share", "address_dist_m", "business_dist_m"):
    out[c] = out[c].round(2 if c == "parcel_share" else 0)
out.to_csv(ROOT / "outputs/contact_worksheet.csv", index=False)
print(f"worksheet: {len(out)} roofs | with parcel {out.parcel_ref.notna().mean():.0%} | with address {out.address.notna().mean():.0%} | "
      f"with business {out.business_name.notna().mean():.0%}")
print(out.head(8)[["priority", "status", "comune", "area_m2", "model_score", "comune_code", "foglio", "particella", "address", "business_name"]].to_string(index=False))
