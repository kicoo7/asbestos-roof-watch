"""Contact worksheet for strong candidates: roof -> cadastral parcel, address, business at the building.

Owner names are NOT fetched: look them up one by one (visura per immobile) with the parcel reference.
Phones and emails are deliberately left out: first contact by letter or visit (see docs/CONTACTING.md).
-> outputs/contact_worksheet.csv (private, git-ignored)
"""
import math
import sys
import urllib.parse
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
        (g if len(g) else gpd.GeoDataFrame({"ref": []}, geometry=[], crs=6706)).to_file(tmp, layer="parcels", driver="GPKG")
        tmp.replace(f)
    return gpd.read_file(f)


with ThreadPoolExecutor(max_workers=6) as ex:  # the WFS answers in ~15 s per request regardless of size
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
# Overture's Italian address points often lack postcode and city: complete with the roof's municipality
r["address"] = r["address"].str.replace(r",\s*$", "", regex=True)
r.loc[r["address"].notna() & ~r["address"].str.contains(r"\d{5}", na=False), "address"] += ", " + r["comune"].str.title()

# business located at the building (inside, or within 30 m), highest confidence first
pl = pd.read_parquet(RAW / "overture_places_pilot.parquet")
pl = gpd.GeoDataFrame(pl, geometry=shapely.from_wkb(pl.pop("wkb")), crs=4326).to_crs(r.crs)
pl = pl[pl["operating_status"].fillna("open") != "permanently_closed"]
jp = gpd.sjoin_nearest(r[["roof_id", "geometry"]], pl[["name", "category", "website", "address", "confidence", "geometry"]].rename(columns={"address": "business_address"}),
                       max_distance=30, distance_col="business_dist_m")
jp = jp.sort_values(["roof_id", "business_dist_m", "confidence"], ascending=[True, True, False]).drop_duplicates("roof_id")
r = r.merge(jp[["roof_id", "name", "category", "website", "business_address", "business_dist_m"]].rename(
    columns={"name": "business_name", "category": "business_category", "website": "business_website"}), on="roof_id", how="left")

# environmental permit holders (AIA: industry, intensive livestock, waste) within 200 m: company name at the site
aia_p = RAW / "aia_bs.gpkg"
if aia_p.exists():
    aia = gpd.read_file(aia_p).to_crs(r.crs)
    aia["aia_type"] = aia["TIPOLOGIA_"].map({"ALL": "livestock farm", "IND": "industrial plant", "RIF": "waste plant"}).fillna(aia["TIPOLOGIA_"])
    aia["aia_address"] = (aia["INDIRIZZO"].fillna("").str.title() + " " + aia["NUM_CIVICO"].fillna("").astype(str)).str.strip() + ", " + aia["CAP"].fillna("").astype(str) + " " + aia["COMUNE"].fillna("").str.title()
    ja = gpd.sjoin_nearest(r[["roof_id", "geometry"]], aia[["RAGIONE_SO", "aia_type", "aia_address", "geometry"]], max_distance=200, distance_col="aia_dist_m")
    r = r.merge(ja.drop_duplicates("roof_id")[["roof_id", "RAGIONE_SO", "aia_type", "aia_address", "aia_dist_m"]].rename(
        columns={"RAGIONE_SO": "aia_company"}), on="roof_id", how="left")
    r["aia_company"] = r["aia_company"].str.strip()
# named OSM features (farmyards, industrial sites, companies) within 150 m
osm_p = RAW / "osm_named_pilot.gpkg"
if osm_p.exists():
    osm = gpd.read_file(osm_p).to_crs(r.crs)
    jo = gpd.sjoin_nearest(r[["roof_id", "geometry"]], osm[["osm_name", "osm_operator", "osm_kind", "osm_website", "geometry"]], max_distance=150, distance_col="osm_dist_m")
    r = r.merge(jo.sort_values("osm_dist_m").drop_duplicates("roof_id")[["roof_id", "osm_name", "osm_operator", "osm_kind", "osm_website", "osm_dist_m"]],
                on="roof_id", how="left")
for c in ("aia_company", "aia_type", "aia_address", "aia_dist_m", "osm_name", "osm_operator", "osm_kind", "osm_website", "osm_dist_m"):
    if c not in r:
        r[c] = np.nan
# best guess at the company on site, by source reliability: business at the building, permit holder, OSM operator/name
r["known_company"] = r["business_name"].fillna(r["aia_company"]).fillna(r["osm_operator"]).fillna(r["osm_name"])
r["known_company_source"] = np.select(
    [r["business_name"].notna(), r["aia_company"].notna(), r["osm_operator"].notna(), r["osm_name"].notna()],
    ["Overture places", "AIA permit", "OSM operator", "OSM name"], default="")

# fallback: a business, permit or named site anywhere inside the roof's cadastral parcel (same property)
par = parcels.to_crs(r.crs)[["ref", "geometry"]].rename(columns={"ref": "parcel_ref"})
cands = [pl[["name", "geometry"]].assign(src="Overture places (same parcel)")]
if aia_p.exists():
    cands.append(aia[["RAGIONE_SO", "geometry"]].rename(columns={"RAGIONE_SO": "name"}).assign(src="AIA permit (same parcel)"))
if osm_p.exists():
    cands.append(osm[["osm_name", "geometry"]].rename(columns={"osm_name": "name"}).assign(src="OSM name (same parcel)"))
pts = gpd.GeoDataFrame(pd.concat(cands, ignore_index=True), geometry="geometry", crs=r.crs).dropna(subset=["name"])
inpar = gpd.sjoin(pts, par[par["parcel_ref"].isin(r["parcel_ref"].dropna())], predicate="within")
inpar = inpar.drop_duplicates("parcel_ref")[["parcel_ref", "name", "src"]].rename(columns={"name": "parcel_company", "src": "parcel_company_source"})
r = r.merge(inpar, on="parcel_ref", how="left")
miss = r["known_company"].isna() & r["parcel_company"].notna()
r.loc[miss, "known_company"] = r.loc[miss, "parcel_company"]
r.loc[miss, "known_company_source"] = r.loc[miss, "parcel_company_source"]

# solar panels visible in the 2024 photo (score from scripts/score_pv_pilot.py)
pvs = ROOT / "outputs/pilot_pv_scores.parquet"
if pvs.exists():
    r = r.merge(pd.read_parquet(pvs).rename(columns={"p_pv": "solar_panels_score"}), on="roof_id", how="left")
else:
    r["solar_panels_score"] = np.nan

pt = r.to_crs(4326).representative_point()
r["google_maps"] = [f"https://www.google.com/maps/@{p.y:.6f},{p.x:.6f},120m/data=!3m1!1e3" for p in pt]
# manual shortcuts: Street View (company signs on gates and facades) and a web search for the company found
r["street_view"] = [f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={p.y:.6f},{p.x:.6f}" for p in pt]
r["company_search"] = [f"https://www.google.com/search?q={urllib.parse.quote_plus(str(k) + ' ' + str(c))}" if isinstance(k, str) else ""
                       for k, c in zip(r["known_company"], r["comune"])]
r["lat"], r["lon"] = pt.y.round(6).values, pt.x.round(6).values
for c in ["owner_from_visura", "owner_type", "contact_method", "contact_date", "outcome", "do_not_contact", "notes"]:
    r[c] = ""
r = r.sort_values(["area_m2"], ascending=False).reset_index(drop=True)
r.insert(0, "priority", range(1, len(r) + 1))
cols = ["priority", "roof_id", "status", "comune", "area_m2", "p", "solar_panels_score", "comune_code", "foglio", "particella", "parcel_ref", "parcel_share",
        "other_parcels", "address", "address_dist_m", "business_name", "business_category", "business_website", "business_address",
        "business_dist_m", "aia_company", "aia_type", "aia_address", "aia_dist_m", "osm_name", "osm_operator", "osm_kind", "osm_website",
        "osm_dist_m", "known_company", "known_company_source", "lat", "lon", "google_maps", "street_view", "company_search", "owner_from_visura", "owner_type", "contact_method", "contact_date",
        "outcome", "do_not_contact", "notes"]
out = r[cols].rename(columns={"p": "model_score"})
out["area_m2"] = out["area_m2"].round(0)
out["model_score"] = out["model_score"].round(2)
out["solar_panels_score"] = out["solar_panels_score"].round(2)
for c in ("parcel_share", "address_dist_m", "business_dist_m", "aia_dist_m", "osm_dist_m"):
    out[c] = out[c].round(2 if c == "parcel_share" else 0)
out.to_csv(ROOT / "outputs/contact_worksheet.csv", index=False)
# owner-lookup queue: biggest roofs with no company found, one row per parcel, for manual visure per immobile
q = out[out["known_company"].isna() & out["parcel_ref"].notna()].drop_duplicates("parcel_ref").head(50)
q[["priority", "roof_id", "comune", "area_m2", "model_score", "comune_code", "foglio", "particella", "parcel_ref", "address", "street_view"]].assign(
    owner_from_visura="", owner_type="", vat_or_tax_code_company_only="", registered_office="", notes="").to_csv(
    ROOT / "outputs/visura_queue.csv", index=False)
print(f"visura queue: {len(q)} parcels (top roofs with no company found)")
print(f"worksheet: {len(out)} roofs | with parcel {out.parcel_ref.notna().mean():.0%} | with address {out.address.notna().mean():.0%} | "
      f"with business {out.business_name.notna().mean():.0%} | AIA {out.aia_company.notna().mean():.0%} | OSM {out.osm_name.notna().mean():.0%} | "
      f"any company {out.known_company.notna().mean():.0%}")
print("company source:", out["known_company_source"].replace("", "none").value_counts().to_dict())
print(out.head(8)[["priority", "status", "comune", "area_m2", "model_score", "comune_code", "foglio", "particella", "address", "business_name"]].to_string(index=False))
