"""Contact worksheet for strong candidates: roof -> cadastral parcel, address, businesses at the building.

Owner names are NOT fetched: look them up one by one (visura per immobile) with the parcel reference.
Phones and emails are deliberately left out: first contact by letter or visit (see docs/CONTACTING.md).
-> outputs/contact_worksheet.csv  candidates; the hand-filled columns of the previous file are carried over, never wiped
-> outputs/roof_occupants.csv     businesses at every roof (all sizes) and next door to it, up to 5 each, for the map panel
-> outputs/roof_addresses.csv     nearest address of every roof within 60 m, for the map panel
-> outputs/visura_queue.csv       read only: the next owner lookups (biggest parcels with no company, owner or objection);
                                  type the results into contact_worksheet.csv, which keeps them
Company on site: the business at the building, else an AIA permit holder (data/raw/aia_bs.gpkg, within 200 m), else a named
OpenStreetMap feature (data/raw/osm_named_pilot.gpkg, within 150 m), else anything named inside the same cadastral parcel.
All private, git-ignored.
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
from datetime import date

from roofwatch.cadastre import assign_parcels, fetch_parcels
from roofwatch.grants import fit_summary, grant_fit, likely_owner_type, load_grants
from roofwatch.occupants import (WORKSHEET_MANUAL as MANUAL, assign_places, drop_suppressed, known_company, load_suppressed_domains,
                                 nearest_address, nearest_point, parcel_companies, prepare_aia, prepare_osm, read_orphans, read_worksheet,
                                 province_from_istat, save_visura_queue, save_worksheet, suppressed_names, visura_queue, write_csv)

THRESH, MIN_M2, CELL_DEG = 0.7, 500, 0.005  # ~400-550 m cells for parcel requests
RAW, OUT = ROOT / "data/raw", ROOT / "outputs"
WS, ORPHANS = OUT / "contact_worksheet.csv", OUT / "contact_worksheet_orphans.csv"
try:  # fail fast, before the slow parcel download and before any write, if a spreadsheet mangled one of the hand-edited files
    read_worksheet(WS)
    read_orphans(ORPHANS)
    # a business suppressed after an objection (company_suppress.csv, or `suppressed` in the register) must disappear
    SUPPRESSED = load_suppressed_domains(OUT / "company_suppress.csv", OUT / "company_register.csv")
except ValueError as e:
    sys.exit(f"ERROR: {e}")

roofs = gpd.read_file(ROOT / "data/processed/lombardy_roofs.gpkg", layer="roofs")
oof = pd.read_parquet(ROOT / "outputs/aerial_oof.parquet")[["roof_id", "p"]]
r = roofs.merge(oof, on="roof_id")
r = r[(r.p >= THRESH) & (r.area_m2 >= MIN_M2) & r.label.isin(["ac", "none"])].copy()
r["status"] = np.where(r.label == "ac", "on ARPA map, model agrees", "suspected, not on ARPA map")
# province for the Agenzia delle Entrate visura form, from each municipality's ISTAT code
_mun = gpd.read_file(RAW / "lombardy_pilot.gpkg", layer="municipalities")
r["provincia"] = r["comune"].map(dict(zip(_mun["NOME_COM"], _mun["ISTAT"].map(province_from_istat)))).fillna("")
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

# nearest address point within 60 m, for every roof (the map panel shows it for non-candidates too)
ad = pd.read_parquet(RAW / "overture_addresses_pilot.parquet")
ad = gpd.GeoDataFrame(ad, geometry=shapely.from_wkb(ad.pop("wkb")), crs=4326)
addr = nearest_address(roofs[["roof_id", "comune", "geometry"]], ad, max_dist_m=60)
r = r.merge(addr, on="roof_id", how="left")

# businesses at every roof: inside the footprint, or nearest to this roof (not to a neighbour) within 30 m;
# plus, labelled next_door, those within 30 m that belong to another building (often the same complex)
pl = pd.read_parquet(RAW / "overture_places_pilot.parquet")
pl = gpd.GeoDataFrame(pl, geometry=shapely.from_wkb(pl.pop("wkb")), crs=4326)
# optional company sources; a permit holder or an OSM site can be one person's farm: same suppression as a listing
aia_p, osm_p = RAW / "aia_bs.gpkg", RAW / "osm_named_pilot.gpkg"
aia = prepare_aia(gpd.read_file(aia_p)) if aia_p.exists() else None
osm = prepare_osm(gpd.read_file(osm_p)) if osm_p.exists() else None
names = suppressed_names(pl, SUPPRESSED) | suppressed_names(osm, SUPPRESSED, "osm_website", ("osm_name", "osm_operator"))
n0 = [len(x) if x is not None else 0 for x in (pl, aia, osm)]
pl = drop_suppressed(pl, SUPPRESSED, names=names)  # before the assignment: no row, no rank and no worksheet column keeps them
aia = drop_suppressed(aia, SUPPRESSED, name_col="aia_company", names=names)
osm = drop_suppressed(osm, SUPPRESSED, "osm_website", ("osm_name", "osm_operator"), names=names)
gone = [a - (len(x) if x is not None else 0) for a, x in zip(n0, (pl, aia, osm))]
if any(gone):
    print(f"suppressed: {gone[0]} listings, {gone[1]} permit holders, {gone[2]} OSM sites of {len(SUPPRESSED)} suppressed domains left out")
occ = assign_places(roofs[["roof_id", "geometry"]], pl, max_dist_m=30, max_per_roof=5, next_door=True)
at, nd = occ[occ["relation"] == "at_building"], occ[occ["relation"] == "next_door"]
top = at[at["rank"] == 1].rename(columns={"name": "business_name", "category": "business_category", "website": "business_website",
                                          "address": "business_address", "dist_m": "business_dist_m"})
r = r.merge(top[["roof_id", "business_name", "business_category", "business_website", "business_address", "business_dist_m"]],
            on="roof_id", how="left")
r["n_businesses"] = r["roof_id"].map(at.groupby("roof_id").size()).fillna(0).astype(int)
r["n_nearby_businesses"] = r["roof_id"].map(nd.groupby("roof_id").size()).fillna(0).astype(int)
nearby = nd[nd["rank"] == 1].rename(columns={"name": "nearby_business_name", "website": "nearby_business_website"})
r = r.merge(nearby[["roof_id", "nearby_business_name", "nearby_business_website"]], on="roof_id", how="left")

# environmental permit holders (AIA: industry, intensive livestock, waste) within 200 m, named OSM features within 150 m
if aia is not None:
    r = r.merge(nearest_point(r, aia, 200, "aia_dist_m"), on="roof_id", how="left")
if osm is not None:
    r = r.merge(nearest_point(r, osm, 150, "osm_dist_m"), on="roof_id", how="left")
for c in ("aia_company", "aia_type", "aia_address", "aia_dist_m", "osm_name", "osm_operator", "osm_kind", "osm_website", "osm_dist_m"):
    if c not in r:
        r[c] = np.nan
# fallback: a business, permit holder or named site anywhere inside the roof's cadastral parcel (same property)
open_pl = pl[pl["operating_status"].fillna("open") != "permanently_closed"]
named = [("Overture places (same parcel)", open_pl[["name", "geometry"]])]
named += [("AIA permit (same parcel)", aia[["aia_company", "geometry"]].rename(columns={"aia_company": "name"}))] if aia is not None else []
if osm is not None:
    named.append(("OSM name (same parcel)", osm[["osm_name", "geometry"]].rename(columns={"osm_name": "name"}).dropna(subset=["name"])))
r = r.merge(parcel_companies(named, parcels, r["parcel_ref"].dropna()), on="parcel_ref", how="left")
r["known_company"], r["known_company_source"] = known_company(r)

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
r["company_search"] = [f"https://www.google.com/search?q={urllib.parse.quote_plus(f'{k} {c}')}" if isinstance(k, str) else ""
                       for k, c in zip(r["known_company"], r["comune"])]
r["lat"], r["lon"] = pt.y.round(6).values, pt.x.round(6).values
for c in MANUAL:
    r[c] = ""
r = r.sort_values(["area_m2", "roof_id"], ascending=[False, True]).reset_index(drop=True)
r.insert(0, "priority", range(1, len(r) + 1))
# grants for the region: likely owner type from public evidence -> schemes that fit and their status today
_grants_path = ROOT / "data/grants_lombardia.json"
if _grants_path.exists():
    _grants, _today = load_grants(_grants_path), date.today()
    _ot = [likely_owner_type(row) for row in r.to_dict("records")]
    r["likely_owner_type"] = [t for t, _ in _ot]
    r["owner_type_evidence"] = [w for _, w in _ot]
    r["grant_fit"] = [fit_summary(grant_fit(t, _grants, _today)) for t, _ in _ot]
else:
    r["likely_owner_type"] = r["owner_type_evidence"] = r["grant_fit"] = ""

cols = ["priority", "roof_id", "status", "comune", "provincia", "likely_owner_type", "owner_type_evidence", "grant_fit", "area_m2", "p", "solar_panels_score", "comune_code", "foglio", "particella", "parcel_ref", "parcel_share",
        "other_parcels", "address", "address_dist_m", "business_name", "business_category", "business_website", "business_address",
        "business_dist_m", "n_businesses", "n_nearby_businesses", "nearby_business_name", "nearby_business_website", "aia_company",
        "aia_type", "aia_address", "aia_dist_m", "osm_name", "osm_operator", "osm_kind", "osm_website", "osm_dist_m", "known_company",
        "known_company_source", "lat", "lon", "google_maps", "street_view", "company_search", *MANUAL]
out = r[cols].rename(columns={"p": "model_score"})
out["area_m2"] = out["area_m2"].round(0)
out["model_score"] = out["model_score"].round(2)
out["solar_panels_score"] = out["solar_panels_score"].round(2)
for c in ("parcel_share", "address_dist_m", "business_dist_m", "aia_dist_m", "osm_dist_m"):
    out[c] = out[c].round(2 if c == "parcel_share" else 0)

# keep what was typed by hand (manual columns carried on roof_id + parcel_ref, old file backed up, orphans appended) and keep
# every objection on its property: see occupants.save_worksheet
info = save_worksheet(out, WS, ORPHANS)
if info["backup"]:
    print(f"backup: {info['backup'].name} | carried over {info['carried']} rows with manual data")
if info["orphans"]:
    print(f"orphans: {sum(info['orphans'].values())} rows with manual data could not be carried over -> {ORPHANS.name}", info["orphans"])
if info["objection_rows"]:
    print(f"do_not_contact: {info['objection_rows']} rows marked from an objection on the same roof or parcel (see their notes)")
if info["objections_unreached"]:  # an objection is a privacy obligation: it must not silently drop out of the worksheet
    print(f"WARNING: do_not_contact on roofs {', '.join(info['objections_unreached'])} reached no current row. Check {ORPHANS.name}.")
write_csv(occ, OUT / "roof_occupants.csv")
write_csv(addr, OUT / "roof_addresses.csv")
# owner-lookup queue, derived from the worksheet as written (objections spread, owners typed): read only, rebuilt every run
queue = visura_queue(read_worksheet(WS))
qb = save_visura_queue(queue, OUT / "visura_queue.csv")
if qb:
    print(f"visura queue: the old file held typed values, saved as {qb.name} (type lookups into contact_worksheet.csv instead)")
print(f"visura queue: {len(queue)} parcels (largest candidate parcels with no company, no owner typed and no objection)")
print(f"worksheet: {len(out)} roofs | with parcel {out.parcel_ref.notna().mean():.0%} | with address {out.address.notna().mean():.0%} | "
      f"with business {out.business_name.notna().mean():.0%} | 2+ businesses {(out.n_businesses >= 2).sum()} | "
      f"only next door {((out.n_businesses == 0) & (out.n_nearby_businesses > 0)).sum()}")
print(f"company on site: AIA {out.aia_company.notna().mean():.0%} | OSM {(out.osm_name.notna() | out.osm_operator.notna()).mean():.0%} | "
      f"any company {out.known_company.notna().mean():.0%}", out["known_company_source"].replace("", "none").value_counts().to_dict())
print(f"all roofs: {at.roof_id.nunique()} with a business at the building, {nd.roof_id.nunique()} next door "
      f"({len(at)} + {len(nd)} rows in roof_occupants.csv, {int(nd.adjoining.sum())} adjoining) | {len(addr)} with an address")
print(out.head(8)[["priority", "status", "comune", "area_m2", "model_score", "comune_code", "foglio", "particella", "address", "business_name"]].to_string(index=False))
