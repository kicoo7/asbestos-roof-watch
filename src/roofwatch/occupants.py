"""Who is at a roof: the businesses there (Overture places) and the nearest address, plus carrying hand-filled columns
from an old contact worksheet into a regenerated one.

Pure functions, no network access; only read_table / read_worksheet / save_worksheet touch files (the contact worksheet
and its orphans file). Only websites are kept, through roofwatch.urls.public_website: phone numbers, email addresses and
owner names are never handled here (docs/CONTACTING.md).
"""
from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from roofwatch.urls import normalize_domain, public_website

WORKSHEET_MANUAL = ["owner_from_visura", "owner_type", "contact_method", "contact_date", "outcome", "do_not_contact", "notes"]
# every column scripts/contact_worksheet.py writes or ever wrote. Never remove a name: a retired generated column left in an
# old worksheet must not pass for one the user added (its stale values would be carried, backed up and orphaned forever)
WORKSHEET_GENERATED = ("priority", "roof_id", "status", "comune", "provincia", "area_m2", "model_score", "solar_panels_score", "comune_code",
                       "foglio", "particella", "parcel_ref", "parcel_share", "other_parcels", "address", "address_dist_m",
                       "business_name", "business_category", "business_website", "business_address", "business_dist_m",
                       "n_businesses", "n_nearby_businesses", "nearby_business_name", "nearby_business_website", "aia_company",
                       "aia_type", "aia_address", "aia_dist_m", "osm_name", "osm_operator", "osm_kind", "osm_website", "osm_dist_m",
                       "known_company", "known_company_source", "lat", "lon", "google_maps", "street_view", "company_search")
REPEATED = "{key} repeated in old worksheet"  # orphan_reason of the second row typed for the same roof
OCCUPANT_COLS = ["roof_id", "relation", "rank", "place_id", "name", "category", "website", "address", "dist_m", "inside", "confidence",
                 "source", "adjoining"]
ADDRESS_COLS = ["roof_id", "address", "address_dist_m"]
ADJOINING_M = 1.0  # buildings this close share a wall or a complex
# hosts shared by unrelated businesses (social pages, link pages, maps, directories): not evidence of the same business
SHARED_HOSTS = ("facebook.com", "instagram.com", "tiktok.com", "youtube.com", "twitter.com", "x.com", "linkedin.com", "linktr.ee",
                "wa.me", "goo.gl", "bit.ly", "google.com", "google.it", "italiaonline.it", "paginegialle.it", "paginebianche.it",
                "tripadvisor.it", "tripadvisor.com", "booking.com")
# Italian legal-form tokens dropped from the end of a name: "Rossi Mario & C. S.n.c." == "ROSSI MARIO"
LEGAL_FORMS = {"srl", "srls", "spa", "snc", "sas", "sapa", "scarl", "scrl", "sc", "ss", "c"}

# do_not_contact values that mean "may contact": empty, or a spreadsheet's way of saying no
NOT_AN_OBJECTION = {"", "0", "0.0", "no", "n", "false", "none"}


def is_objection(value) -> bool:
    """True when a do_not_contact cell holds an objection: anything except empty, 0, no, n, false, none (any case)."""
    return _text(value).strip().lower() not in NOT_AN_OBJECTION


def _text(v) -> str:
    """A cell as the text the user typed: '' when missing, '1' (not '1.0') for a whole float, never 'nan'."""
    if v is None or (not isinstance(v, str) and np.ndim(v) == 0 and pd.isna(v)):
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


_ROOF_ID = re.compile(r"[0-9]+(?:\.0+)?")  # ASCII digits only: '12', '012', '12.0'; never '2.6', '1e3' or '١٢'


def parse_roof_id(v) -> int | None:
    """The one roof-id parser for hand-edited files (worksheet, orphans; roofwatch.panel too): 12 for '12', ' 012 ', '12.0',
    12 or 12.0; None for anything else."""
    s = _text(v).strip()
    return int(s.split(".")[0]) if _ROOF_ID.fullmatch(s) else None


def _clean(s: pd.Series) -> pd.Series:
    return s.map(_text).str.strip()


def name_key(name) -> str:
    """Business name for duplicate detection: no accents, case, punctuation or trailing legal form ('' when empty)."""
    s = unicodedata.normalize("NFKD", _text(name)).encode("ascii", "ignore").decode().lower().replace(".", "")
    toks = re.sub(r"[^a-z0-9]+", " ", s).split()
    while len(toks) > 1 and toks[-1] in LEGAL_FORMS:
        toks.pop()
    return " ".join(toks)


# contact details typed into a listing's text ("Autofficina Rossi Tel 333 1234567"): never stored (docs/CONTACTING.md).
# scrub_contact is the one implementation; roofwatch.panel may use it for names.
_EMAIL_IN_TEXT = re.compile(r"[\w.+-]+\s*(?:@|\[at\]|\(at\)|\{at\})\s*[\w-]+(?:\s*(?:\.|\[dot\]|\(dot\))\s*[\w-]+)*"
                            r"\s*(?:\.|\[dot\]|\(dot\))\s*[a-z]{2,24}\b", re.I)
_DIGIT_RUN = re.compile(r"(?<!\w)[+(]?\d[\d ()./-]*\d")  # '(030) 1234567', '030 / 1234567', '+39 (0)30 1234567'
_LABELS = r"tel|telefono|fax|cell|cellulare|mob|mobile|phone|whatsapp|e-?mail|mail|pec|p\.?\s?iva"
_LABEL_GLUED = re.compile(rf"\b({_LABELS})(\.?:?)(?=[+(]?\d(?:[ ()./-]*\d){{8,}})", re.I)  # 'Tel3331234567'
_LABEL_CUT = re.compile(rf"(?:\b(?:{_LABELS})\b\.?\s*[:/]?\s*)*\x00", re.I)  # a label left in front of what was cut


def _is_phone(run: str) -> bool:
    """Italian numbers: landlines start with 0 (9+ digits), mobiles with 3 (10); with +39 or 0039, 10+ digits."""
    digits = re.sub(r"\D", "", run)
    if run.startswith(("+", "00")):
        return len(digits) >= 10
    lead = run.lstrip("(")[:1]
    return (lead == "0" and len(digits) >= 9) or (lead == "3" and len(digits) >= 10)


def _cut_phone(m: re.Match) -> str:
    run = m.group(0)
    # 'Bar 2000 030 1234567': the number may start after another figure, so try each digit group as a start
    for i in [0] + [j for j in range(1, len(run)) if run[j - 1] in " /.-" and run[j] in "0123456789(+"]:
        if _is_phone(run[i:]):
            return run[:i] + "\x00"
    return "\x00" if len(re.sub(r"\D", "", run)) >= 9 else run  # any other 9+ digit number: foreign phone, VAT number


def scrub_contact(text, phones: bool = True) -> str:
    """Text without e-mail addresses (also 'info [at] rossi.it') and, if phones, phone numbers ('(030) 1234567',
    '+39 (0)30 1234567', any 9+ digit run), each with a 'Tel'/'cell'/'e-mail' label in front of it. Other figures stay
    ('Bar 2000'). Addresses pass phones=False: there a long digit run is a road number plus postcode ('SP11 11-25022')."""
    s = _EMAIL_IN_TEXT.sub("\x00", _text(text).replace("\x00", ""))
    if phones:
        s = _DIGIT_RUN.sub(_cut_phone, _LABEL_GLUED.sub(r"\1\2 ", s))
    s = re.sub(r"[(\[]\s*[)\]]", "", _LABEL_CUT.sub("", s).replace("\x00", ""))  # 'Ditta (e-mail: ...)' -> 'Ditta'
    return re.sub(r"\s{2,}", " ", s).strip(" -–,;:|/")


def website(url) -> str | None:
    """A listing's website as it may be stored or shown (roofwatch.urls.public_website): None for e-mail, WhatsApp/phone links."""
    return public_website(url)


def business_domain(url) -> str | None:
    """Website domain usable as identity (None for shared hosts such as facebook.com)."""
    d = normalize_domain(website(url))
    return None if d is None or any(d == h or d.endswith("." + h) for h in SHARED_HOSTS) else d


def is_suppressed(url, domains) -> bool:
    """True when the website's domain (normalize_domain) is a suppressed domain or a subdomain of one."""
    d = normalize_domain(url)
    return d is not None and any(d == x or d.endswith("." + x) for x in domains)


def _business_key(name) -> str:
    return name_key(scrub_contact(name))  # as the name is written out: 'Rossi Mario Tel. 333...' is 'Rossi Mario'


def suppressed_names(df: pd.DataFrame | None, domains, website_col: str = "website", name_col="name") -> set:
    """Name keys of the rows of `df` whose website is on a suppressed domain or a subdomain of one (`name_col`: one or more)."""
    if df is None or df.empty or not domains or website_col not in df:
        return set()
    hit = df[website_col].map(lambda u: is_suppressed(u, domains)).astype(bool)
    cols = [c for c in ([name_col] if isinstance(name_col, str) else name_col) if c in df]
    return {k for c in cols for k in df.loc[hit.values, c].map(_business_key) if k}


def drop_suppressed(df: pd.DataFrame, domains, website_col: str = "website", name_col="name", names=frozenset()) -> pd.DataFrame:
    """`df` (Overture places, roof_occupants rows, AIA permit holders, OSM named features) without the suppressed businesses:
    rows whose website is on a suppressed domain or a subdomain of one, and any row whose name (`name_col`: one or more
    columns) is the name of a suppressed business: of this frame's suppressed rows, or in `names` (from the other sources).
    The same business listed without that website, or as a permit holder or an OSM site, would otherwise take its place."""
    if df is None or df.empty or not (domains or names):
        return df
    hit = df[website_col].map(lambda u: is_suppressed(u, domains)).astype(bool) if website_col in df else pd.Series(False, index=df.index)
    keys = set(names) | suppressed_names(df, domains, website_col, name_col)
    for c in ([name_col] if isinstance(name_col, str) else name_col):
        if c in df and keys:
            hit |= df[c].map(_business_key).isin(keys)
    return df[~hit.values]


def load_suppressed_domains(suppress_path, register_path) -> frozenset:
    """Domains whose businesses must disappear: the `domain` column of company_suppress.csv plus every domain whose
    company_register.csv row has status `suppressed` (that covers suppression by VAT number). Both files are optional. A
    non-empty suppression file with neither a `domain` nor a `vat` column, or a register without `domain` and `status`,
    raises ValueError: ignoring it would quietly undo an objection."""
    doms = set()
    sup = read_table(suppress_path)
    if sup is not None:
        cols = {c.lower(): c for c in sup.columns}
        if "domain" not in cols and "vat" not in cols:
            raise ValueError(f"{Path(suppress_path).name} needs a 'domain' and/or 'vat' column (header read as: "
                             f"{', '.join(map(str, sup.columns[:4]))}). Nothing was written.")
        doms |= {normalize_domain(v) for v in sup[cols["domain"]]} if "domain" in cols else set()
    reg = _check_key(read_table(register_path), Path(register_path).name, ("domain", "status"))
    if reg is not None:
        doms |= {normalize_domain(d) for d, st in zip(reg["domain"], reg["status"]) if st.strip().lower() == "suppressed"}
    return frozenset(doms - {None})


def _join(*parts, sep=", ") -> str:
    return sep.join(p for p in parts if p)


def _components(keys: list[set]) -> list[int]:
    """Connected components of rows that share any key (union-find), so A~B by name and B~C by domain is one business."""
    parent = list(range(len(keys)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    owner = {}
    for i, ks in enumerate(keys):
        for k in ks:
            if k in owner:
                parent[find(i)] = find(owner[k])
            else:
                owner[k] = i
    return [find(i) for i in range(len(keys))]


def _business_components(df: pd.DataFrame) -> list[int]:
    """Same business within a roof: same normalised name or same website domain."""
    return _components([{k for k in (("n", rid, name_key(n)), ("d", rid, business_domain(w))) if k[2]}
                        for rid, n, w in zip(df["roof_id"], df["name"], df["website"])])


def _one_per_business(df: pd.DataFrame, flag: str, max_per_roof: int) -> pd.DataFrame:
    """Rows labelled by business (_c) -> one per business, ranked within roof: `flag` (inside / adjoining) first, then
    distance, confidence, place_id. Details come from the highest-confidence listing, `flag` and dist_m from the closest,
    so a business that one source puts inside the building is not reported 16 m away by another."""
    df = df.sort_values(["roof_id", "confidence", flag, "dist_m", "place_id"], ascending=[True, False, False, True, True])
    place = df.groupby("_c").agg(**{flag: (flag, "max"), "dist_m": ("dist_m", "min")})
    df = df.drop_duplicates("_c").drop(columns=[flag, "dist_m"]).join(place, on="_c")
    df = df.sort_values(["roof_id", flag, "dist_m", "confidence", "place_id"], ascending=[True, False, True, False, True])
    df["rank"] = df.groupby("roof_id").cumcount() + 1
    return df[df["rank"] <= max_per_roof]


def assign_places(roofs: gpd.GeoDataFrame, places: gpd.GeoDataFrame, max_dist_m: float = 30, max_per_roof: int = 5,
                  next_door: bool = True) -> pd.DataFrame:
    """Businesses at and next to each roof, long format: one row per (roof, relation, business), columns OCCUPANT_COLS.

    `roofs`: roof_id + footprint polygons in a metric CRS. `places`: Overture place points (id, name, category, website,
    address, locality, postcode, confidence, operating_status, src). Permanently closed places are dropped.
    relation 'at_building': a place inside a footprint belongs to that roof; a place outside every footprint belongs only to
    the single nearest roof within max_dist_m, so the shop next door goes to its own building. Ranked inside first.
    relation 'next_door' (if next_door): a place within max_dist_m of this roof that belongs to another building. dist_m is
    to this roof; `adjoining` when its own building is within ADJOINING_M of this roof (shared wall, same complex). Ranked
    adjoining first; a business already listed at this building is not repeated.
    The same business listed by several sources (same normalised name or website domain) is kept once per roof and relation.
    Then by distance, confidence, place_id; rank restarts per (roof, relation), at most max_per_roof each.
    """
    empty = pd.DataFrame(columns=OCCUPANT_COLS)
    pl = places
    if "operating_status" in pl:
        pl = pl[pl["operating_status"].fillna("open") != "permanently_closed"]
    if pl.crs is not None and roofs.crs is not None and pl.crs != roofs.crs:
        pl = pl.to_crs(roofs.crs)
    pl = pl[pl.geometry.notna() & ~pl.geometry.is_empty].reset_index(drop=True)
    rf = roofs[["roof_id", "geometry"]].reset_index(drop=True)
    if pl.empty or rf.empty:
        return empty
    pts = gpd.GeoDataFrame(geometry=pl.geometry.values, crs=rf.crs)
    # owners: rows (_pi place position, index_right roof position, roof_id, dist_m, inside)
    ins = gpd.sjoin(pts, rf, predicate="intersects")[["index_right", "roof_id"]].assign(dist_m=0.0, inside=True)  # 2 overlapping footprints: both
    out = pts[~pts.index.isin(ins.index)]
    near = gpd.sjoin_nearest(out, rf, max_distance=max_dist_m, distance_col="dist_m")[["index_right", "roof_id", "dist_m"]]
    near = near.rename_axis("_pi").reset_index().sort_values(["_pi", "dist_m", "roof_id"]).drop_duplicates("_pi")
    own = pd.concat([ins.rename_axis("_pi").reset_index(), near.assign(inside=False)], ignore_index=True)
    if own.empty:
        return empty
    col = lambda c: _clean(pl[c]) if c in pl else pd.Series("", index=pl.index)
    conf = pd.to_numeric(pl["confidence"], errors="coerce") if "confidence" in pl else pd.Series(np.nan, index=pl.index)
    attrs = pd.DataFrame({"place_id": col("id"), "name": col("name").map(scrub_contact), "category": col("category"),
                          "website": col("website").map(website),
                          "address": [_join(scrub_contact(s, phones=False), _join(p, l, sep=" "))
                                      for s, p, l in zip(col("address"), col("postcode"), col("locality"))],
                          "confidence": conf.fillna(0.0), "source": col("src")})
    at = own.join(attrs, on="_pi")
    at["_c"] = _business_components(at)
    parts = [_one_per_business(at, "inside", max_per_roof).assign(relation="at_building", adjoining=False)]
    if next_door:
        nd = gpd.sjoin(pts, rf, predicate="dwithin", distance=max_dist_m)[["index_right", "roof_id"]].rename_axis("_pi").reset_index()
        pair = lambda d: pd.MultiIndex.from_frame(d[["_pi", "index_right"]])
        nd = nd[~pair(nd).isin(pair(own))]  # place within reach of this roof, but owned by another building
        g = lambda pos: rf.geometry.values[np.asarray(pos, dtype=int)]  # roof footprints by position
        nd["dist_m"] = shapely.distance(pts.geometry.values[nd["_pi"].to_numpy()], g(nd["index_right"]))
        o = nd[["_pi", "index_right"]].merge(own[["_pi", "index_right"]].rename(columns={"index_right": "_owner"}), on="_pi")
        o["adjoining"] = shapely.distance(g(o["_owner"]), g(o["index_right"])) <= ADJOINING_M
        nd = nd.merge(o.groupby(["_pi", "index_right"], as_index=False)["adjoining"].any(), on=["_pi", "index_right"], how="left")
        nd = nd.assign(adjoining=nd["adjoining"].fillna(False).astype(bool), inside=False).join(attrs, on="_pi")
        # a business listed at this building (any listing, before the cap) is not repeated as next door
        both = pd.concat([at.assign(_nd=False), nd.assign(_nd=True)], ignore_index=True)
        both["_c"] = _business_components(both)
        nd = both[both["_nd"] & ~both["_c"].isin(both.loc[~both["_nd"], "_c"])]
        if len(nd):
            parts.append(_one_per_business(nd, "adjoining", max_per_roof).assign(relation="next_door"))
    a = pd.concat(parts, ignore_index=True)
    a["dist_m"] = a["dist_m"].round(0).astype(int)
    a["confidence"] = a["confidence"].round(2)
    a[["inside", "adjoining"]] = a[["inside", "adjoining"]].astype(bool)
    for c in ("name", "category", "address", "source"):
        a[c] = a[c].replace("", None)
    return a.sort_values(["roof_id", "relation", "rank"])[OCCUPANT_COLS].reset_index(drop=True)


def nearest_address(roofs: gpd.GeoDataFrame, addresses: gpd.GeoDataFrame, max_dist_m: float = 60) -> pd.DataFrame:
    """Nearest Overture address point within max_dist_m of each roof -> roof_id, address, address_dist_m (roofs that have one).

    `roofs`: roof_id, geometry and optionally `comune`. `addresses`: points with street, number, postcode, postal_city.
    Overture's Italian address points often lack postcode and city: the roof's municipality then completes the address.
    Ties (several points inside a footprint) go to the point listed first in `addresses`.
    """
    ad = addresses.to_crs(roofs.crs) if addresses.crs is not None and roofs.crs is not None and addresses.crs != roofs.crs else addresses
    get = lambda c: _clean(ad[c]) if c in ad else pd.Series("", index=ad.index)
    ad = gpd.GeoDataFrame({"_street": (get("street") + " " + get("number")).str.strip(), "_postcode": get("postcode"),
                           "_city": get("postal_city"), "_ai": np.arange(len(ad))}, geometry=ad.geometry.values, crs=roofs.crs)
    ad = ad[(ad["_street"] != "") & ad.geometry.notna()]  # a city without a street is not an address
    rf = roofs[[c for c in ("roof_id", "comune") if c in roofs] + ["geometry"]].reset_index(drop=True)
    if ad.empty or rf.empty:
        return pd.DataFrame(columns=ADDRESS_COLS)
    j = gpd.sjoin_nearest(rf, ad, max_distance=max_dist_m, distance_col="address_dist_m")
    j = j.sort_values(["roof_id", "address_dist_m", "_ai"]).drop_duplicates("roof_id")
    comune = _clean(j["comune"]) if "comune" in j else pd.Series("", index=j.index)
    comune = comune.where(comune != comune.str.upper(), comune.str.title())  # 'LENO' -> 'Leno', but "Quinzano d'Oglio" stays
    city = j["_city"].where(j["_city"] != "", comune)
    j["address"] = [_join(s, _join(p, c, sep=" ")) for s, p, c in zip(j["_street"], j["_postcode"], city)]
    j["address_dist_m"] = j["address_dist_m"].round(0).astype(int)
    return j[ADDRESS_COLS].reset_index(drop=True)


def has_manual(df: pd.DataFrame, manual_cols) -> pd.Series:
    """True for rows holding any non-blank value in the manual columns."""
    cols = [c for c in manual_cols if c in df]
    if not cols:
        return pd.Series(False, index=df.index)
    return pd.concat([_clean(df[c]) != "" for c in cols], axis=1).any(axis=1)


def merge_manual(new: pd.DataFrame, old: pd.DataFrame | None, manual_cols, key: str = "roof_id", guard: str = "parcel_ref"):
    """Carry hand-filled columns from an old worksheet into a regenerated one -> (merged, orphans).

    A row's manual values move over only when `key` matches and `guard` matches too (or is missing on both sides): roof ids
    are positional and shift if the roof table is rebuilt, the parcel reference does not. Rows of `old` holding manual data
    that cannot be carried (roof gone, guard mismatch, repeated key) come back as `orphans`: all their columns as text plus
    `orphan_reason`, so nothing typed by hand is lost. Manual columns come back as text ('' when empty, never 'nan' or '1.0').
    """
    manual_cols = list(manual_cols)
    merged = new.copy()
    for c in manual_cols:
        merged[c] = merged[c].map(_text).astype(object) if c in merged else ""
    if old is None or old.empty:
        return merged, pd.DataFrame(columns=[*(old.columns if old is not None else []), "orphan_reason"])
    o = old[has_manual(old, manual_cols)].astype(object)
    o = o.apply(lambda s: s.map(_text)) if len(o) else o
    if o.empty:
        return merged, pd.DataFrame(columns=[*old.columns, "orphan_reason"])
    nk = merged[key].map(_rid)
    if nk.duplicated().any():
        raise ValueError(f"duplicate {key} in the new worksheet")
    ng = merged[guard].map(_text) if guard in merged else pd.Series("", index=merged.index)
    ok = o[key].map(_rid) if key in o else pd.Series("", index=o.index)
    og = o[guard] if guard in o else pd.Series("", index=o.index)
    target = ok.map(dict(zip(nk, ng)))  # guard of the same roof in the new worksheet, NaN when the roof is gone
    match = (ok != "") & target.notna() & (target == og)
    dup = ok[match].duplicated().reindex(ok.index, fill_value=False)  # a roof typed in twice: the first row wins
    reason = np.select([ok == "", target.isna(), ~match, dup],
                       [f"no {key}", "roof no longer a candidate", f"{guard} changed", REPEATED.format(key=key)], "")
    carry = o[reason == ""]
    rows = ok[reason == ""].map(dict(zip(nk, range(len(merged))))).tolist()  # positions; `old` may lack the key column
    for c in manual_cols:
        if c in carry and rows:
            merged.iloc[rows, merged.columns.get_loc(c)] = carry[c].values
    orphans = o[reason != ""].assign(orphan_reason=reason[reason != ""]).reset_index(drop=True)
    return merged, orphans


def write_csv(df: pd.DataFrame, path) -> None:
    """Write atomically: an interrupted run never leaves a half-written file."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(path)


def _parse_table(raw: bytes) -> pd.DataFrame | None:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):  # Excel on Windows saves "CSV" in the local code page; latin-1 never fails
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text.strip():
        return None
    head = text.split("\n", 1)[0]
    sep = max((",", ";", "\t"), key=head.count)  # Excel in an Italian locale saves with ';'
    df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False)
    return df.rename(columns=lambda c: str(c).strip())  # 'do_not_contact ' typed with a trailing blank is still the column


def read_table(path) -> pd.DataFrame | None:
    """A CSV as text, however a spreadsheet re-saved it: ',' ';' or tab separated, UTF-8 (with or without BOM) or Windows-1252.
    Every cell is a string ('' when empty: "NA" or "1" stay as typed). None when the file is missing or empty."""
    path = Path(path)
    return _parse_table(path.read_bytes()) if path.exists() else None


def _check_key(df: pd.DataFrame | None, name: str, key="roof_id") -> pd.DataFrame | None:
    """df, or ValueError naming the missing columns (`key`: one name or several) when df is not None."""
    missing = [k for k in ([key] if isinstance(key, str) else key) if df is not None and k not in df.columns]
    if missing:
        cols = ", ".join(map(str, df.columns[:4])) + (", ..." if len(df.columns) > 4 else "")
        raise ValueError(f"{name} has no {', '.join(repr(k) for k in missing)} column{'s' if len(missing) > 1 else ''} (header read "
                         f"as: {cols}). If a spreadsheet re-saved it, restore the header row or the latest backup and run again. "
                         f"Nothing was written.")
    return df


def read_worksheet(path, key: str = "roof_id") -> pd.DataFrame | None:
    """The existing contact worksheet (read_table), or None. ValueError with a readable message when it has no `key` column,
    so the script can stop before any slow step and before writing anything."""
    return _check_key(read_table(path), Path(path).name, key)


def read_orphans(path, key: str = "roof_id", guard: str = "parcel_ref", dnc: str = "do_not_contact") -> pd.DataFrame | None:
    """The orphans file (read_table), or None. ValueError when a non-empty file lacks roof_id, parcel_ref or do_not_contact:
    its objections would silently stop counting in the worksheet and the map."""
    return _check_key(read_table(path), Path(path).name, (key, guard, dnc))


def _rid(v) -> str:
    """roof_id as compared everywhere in this module: '12' for any valid form (parse_roof_id), '' otherwise."""
    i = parse_roof_id(v)
    return "" if i is None else str(i)


def _mark(df: pd.DataFrame, i, col: str, notes: str, value: str, note: str) -> None:
    """Give row i the objection with a note, unless it has its own objection (then it keeps it and gets no note)."""
    if is_objection(df.at[i, col]):
        return
    df.at[i, col] = value
    if note not in df.at[i, notes]:
        df.at[i, notes] = f"{df.at[i, notes]}; {note}" if df.at[i, notes].strip() else note


def _objection_frame(df: pd.DataFrame, key: str, guard: str, col: str, notes: str):
    df = df.copy()
    for c in (col, notes):
        df[c] = df[c].map(_text).astype(object) if c in df else ""
    ng = df[guard].map(_text).str.strip() if guard in df else pd.Series("", index=df.index)
    return df, df[key].map(_rid), ng


def spread_objections(merged: pd.DataFrame, key: str = "roof_id", guard: str = "parcel_ref", col: str = "do_not_contact",
                      notes: str = "notes") -> pd.DataFrame:
    """Every objection in the worksheet reaches all rows on the same parcel_ref, with the note "do_not_contact carried from
    roof N on the same parcel" (N: the parcel's first objecting row). Rows with their own objection are left as they are."""
    merged, nk, ng = _objection_frame(merged, key, guard, col, notes)
    src = merged.index[merged[col].map(is_objection).values & (ng != "").values]
    for parcel in dict.fromkeys(ng[src]):  # parcels in worksheet order: the same source on every run
        first = next(i for i in src if ng[i] == parcel)
        for i in merged.index[(ng == parcel).values]:
            _mark(merged, i, col, notes, merged.at[first, col].strip(), f"{col} carried from roof {nk[first]} on the same parcel")
    return merged


def carry_objections(merged: pd.DataFrame, objections: pd.DataFrame, orphans_name: str = "contact_worksheet_orphans.csv",
                     key: str = "roof_id", guard: str = "parcel_ref", col: str = "do_not_contact", notes: str = "notes"):
    """An objection belongs to the property -> (merged, reached). Same rule as the map (roofwatch.panel):

    each orphaned objection reaches the row of the same roof (its parcel may have changed) and every row on the orphan's
    parcel; then every objection in the worksheet reaches all rows on its parcel (spread_objections). Each marked row gets
    a note naming the roof the objection came from. A row with its own objection keeps it and gets no note, so re-running
    adds nothing. `reached[i]` tells whether objection i covers any row.
    """
    merged, nk, ng = _objection_frame(merged, key, guard, col, notes)
    reached = []
    for _, o in objections.iterrows():
        rid, parcel, value = _rid(o.get(key)), _text(o.get(guard)).strip(), _text(o.get(col)).strip()
        by_roof = (nk == rid) & (rid != "")
        hit = by_roof | ((ng == parcel) & (parcel != ""))
        for i in merged.index[hit.values]:
            _mark(merged, i, col, notes, value, f"{col} carried from an orphaned row of roof {rid} (see {orphans_name})" if by_roof[i]
                  else f"{col} carried from roof {rid or '?'} on the same parcel (see {orphans_name})")
        reached.append(bool(hit.any()))
    return spread_objections(merged, key, guard, col, notes), reached


def _backup(path: Path, raw: bytes, now: datetime) -> Path:
    """Write `raw` (the file as it was read) to <stem>.backup-YYYYmmdd-HHMMSS<suffix>, never over an earlier backup."""
    backup, n = path.with_name(f"{path.stem}.backup-{now:%Y%m%d-%H%M%S}{path.suffix}"), 1
    while backup.exists():  # two runs in the same second must not overwrite a backup
        n += 1
        backup = path.with_name(f"{path.stem}.backup-{now:%Y%m%d-%H%M%S}-{n}{path.suffix}")
    tmp = backup.with_name(f".{backup.name}.tmp")
    tmp.write_bytes(raw)
    tmp.replace(backup)
    return backup


def save_worksheet(new: pd.DataFrame, ws_path, orphans_path, manual_cols=WORKSHEET_MANUAL, generated_cols=WORKSHEET_GENERATED,
                   now: datetime | None = None, key: str = "roof_id", dnc: str = "do_not_contact") -> dict:
    """Write the regenerated worksheet `new` to ws_path without losing anything typed by hand. In this order:

    1. read the old worksheet and the orphans file as text (any separator); stop with ValueError, before writing anything,
       if the worksheet has no roof_id or a non-empty orphans file lacks roof_id, parcel_ref or do_not_contact;
    2. carry the manual columns, and columns the user added (anything not in generated_cols), over with merge_manual;
    3. objections belong to the property (carry_objections, the map's rule): every orphaned objection, this run's and those
       already in orphans_path, reaches its roof and its parcel; every objection in the worksheet reaches its parcel;
    4. back up the old file byte for byte when it held manual data (contact_worksheet.backup-YYYYmmdd-HHMMSS.csv);
    5. append this run's orphans to orphans_path, de-duplicated so a re-run after a crash adds nothing twice;
    6. write the new worksheet. All writes are atomic.

    Returns what the caller should print: backup path or None, rows carried, orphans by reason, rows that received an
    objection, and roof ids of orphaned objections (this run's and earlier ones) that reach no row: a hand check.
    """
    ws_path, orphans_path, now = Path(ws_path), Path(orphans_path), now or datetime.now()
    manual_cols = list(manual_cols)
    unknown = [c for c in new.columns if c not in generated_cols and c not in manual_cols]
    if unknown:
        raise ValueError(f"add {unknown} to occupants.WORKSHEET_GENERATED, or old copies would pass for hand-added columns")
    raw = ws_path.read_bytes() if ws_path.exists() else None
    old = _check_key(_parse_table(raw), ws_path.name, key) if raw is not None else None
    prev = read_orphans(orphans_path, key=key, dnc=dnc)
    user = [c for c in (old.columns if old is not None else []) if c not in generated_cols and c not in manual_cols
            and not (re.fullmatch(r"Unnamed: \d+", str(c)) and not has_manual(old, [c]).any())]  # an empty trailing column is not data
    manual = manual_cols + user
    merged, orphans = merge_manual(new, old, manual, key=key)
    if len(orphans):
        orphans.insert(0, "orphaned_at", now.strftime("%Y-%m-%d %H:%M:%S"))
    objecting = lambda t: t[t[dnc].map(is_objection).astype(bool)] if t is not None and dnc in t else pd.DataFrame()
    now_obj, prev_obj = objecting(orphans), objecting(prev)  # this run's first: `reached` lines up with them
    before = merged
    merged, reached = carry_objections(merged, pd.concat([now_obj, prev_obj], ignore_index=True), orphans_path.name, key=key, col=dnc)
    sig = lambda d: d.reindex(columns=[dnc, "notes"]).apply(lambda s: s.map(_text)).agg("\x1f".join, axis=1)
    touched = int((sig(merged) != sig(before)).sum())
    every = pd.concat([now_obj, prev_obj], ignore_index=True)  # earlier orphans too: warn on every run until it is resolved
    ids = every[key] if key in every else pd.Series("?", index=every.index)
    unreached = list(dict.fromkeys(_rid(r) or _text(r).strip() or "?" for r, ok in zip(ids, reached) if not ok))
    backup = None
    if old is not None and has_manual(old, manual).any():
        backup = _backup(ws_path, raw, now)
    if len(orphans):
        allo = pd.concat([prev, orphans], ignore_index=True).fillna("") if prev is not None else orphans
        write_csv(allo.drop_duplicates([c for c in allo.columns if c != "orphaned_at"]), orphans_path)
    write_csv(merged, ws_path)
    return {"backup": backup, "carried": int(has_manual(merged, manual).sum()),
            "orphans": orphans["orphan_reason"].value_counts().to_dict() if len(orphans) else {},
            "objection_rows": touched, "objections_unreached": unreached}


# --- the company on site: AIA permit holders, named OSM features, same-parcel fallback, owner-lookup queue -------------------
# A permit holder or an OSM site can be a farm run by one person: names are contact-scrubbed and suppression applies to them.
AIA_TYPES = {"ALL": "livestock farm", "IND": "industrial plant", "RIF": "waste plant"}
# best guess at the company on site, by source reliability (the business at the building is the rank-1 at_building listing)
COMPANY_SOURCES = (("business_name", "Overture places"), ("aia_company", "AIA permit"), ("osm_operator", "OSM operator"),
                   ("osm_name", "OSM name"))
# the Agenzia delle Entrate visura form, field by field: Provincia, Comune, Catasto, Sezione, Foglio, Particella, Subalterno
ADE_COLS = ["ade_provincia", "ade_comune", "ade_catasto", "ade_sezione", "ade_foglio", "ade_particella", "ade_subalterno"]
QUEUE_COLS = ["priority", *ADE_COLS, "roof_id", "comune", "area_m2", "model_score", "comune_code", "foglio", "particella", "parcel_ref",
              "address", "street_view"]
_PROVINCES = {12: "Varese", 13: "Como", 14: "Sondrio", 15: "Milano", 16: "Bergamo", 17: "Brescia", 18: "Pavia", 19: "Cremona",
              20: "Mantova", 97: "Lecco", 98: "Lodi", 108: "Monza e della Brianza"}  # Lombardy


def province_from_istat(code) -> str:
    """Province name from a municipality's ISTAT code (province code = code // 1000); '' when unknown."""
    try:
        return _PROVINCES.get(int(code) // 1000, "")
    except (TypeError, ValueError):
        return ""
QUEUE_FILL_IN = ("owner_from_visura", "owner_type", "vat_or_tax_code_company_only", "registered_office", "notes")  # an older version's


def _none_if_blank(s: pd.Series) -> pd.Series:
    return s.where(s != "", None)


def prepare_aia(aia: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Regione Lombardia AIA permit holders -> aia_company (contact-scrubbed), aia_type, aia_address (e-mails removed)."""
    col = lambda c: _clean(aia[c]) if c in aia else pd.Series("", index=aia.index)
    typ = col("TIPOLOGIA_")
    out = gpd.GeoDataFrame({
        "aia_company": _none_if_blank(col("RAGIONE_SO").map(scrub_contact)),
        "aia_type": _none_if_blank(typ.map(lambda t: AIA_TYPES.get(t, t))),
        "aia_address": _none_if_blank(pd.Series([scrub_contact(_join(_join(st.title(), n, sep=" "), _join(cap, c.title(), sep=" ")), phones=False)
                                                 for st, n, cap, c in zip(col("INDIRIZZO"), col("NUM_CIVICO"), col("CAP"), col("COMUNE"))],
                                                index=aia.index, dtype=object))},
        geometry=aia.geometry.values, crs=aia.crs)
    return out[out["aia_company"].notna().values]


def prepare_osm(osm: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Named OpenStreetMap features -> osm_name, osm_operator (contact-scrubbed), osm_kind, osm_website (public_website)."""
    col = lambda c: _clean(osm[c]) if c in osm else pd.Series("", index=osm.index)
    out = gpd.GeoDataFrame({"osm_name": _none_if_blank(col("osm_name").map(scrub_contact)),
                            "osm_operator": _none_if_blank(col("osm_operator").map(scrub_contact)),
                            "osm_kind": _none_if_blank(col("osm_kind")),
                            "osm_website": col("osm_website").map(public_website).astype(object)},
                           geometry=osm.geometry.values, crs=osm.crs)
    return out[(out["osm_name"].notna() | out["osm_operator"].notna()).values]


def nearest_point(roofs: gpd.GeoDataFrame, points: gpd.GeoDataFrame, max_dist_m: float, dist_col: str) -> pd.DataFrame:
    """Per roof, the nearest of `points` within max_dist_m -> roof_id, the points' columns, dist_col. Ties: the point listed first."""
    cols = [c for c in points.columns if c != points.geometry.name]
    if points.empty or roofs.empty:
        return pd.DataFrame(columns=["roof_id", *cols, dist_col])
    pts = (points.to_crs(roofs.crs) if points.crs != roofs.crs else points).reset_index(drop=True).assign(_pi=lambda d: range(len(d)))
    j = gpd.sjoin_nearest(roofs[["roof_id", "geometry"]].reset_index(drop=True), pts, max_distance=max_dist_m, distance_col=dist_col)
    return j.sort_values(["roof_id", dist_col, "_pi"]).drop_duplicates("roof_id")[["roof_id", *cols, dist_col]].reset_index(drop=True)


def parcel_companies(named, parcels: gpd.GeoDataFrame, refs) -> pd.DataFrame:
    """A business, permit holder or named site anywhere inside a candidate's cadastral parcel (the same property).
    `named`: (source label, GeoDataFrame with `name`) in order of preference; `parcels`: `ref` polygons; `refs`: the parcel
    references to look at. -> parcel_ref, parcel_company, parcel_company_source: one row per parcel, the first source wins."""
    cols = ["parcel_ref", "parcel_company", "parcel_company_source"]
    par = parcels[parcels["ref"].isin(set(refs))][["ref", "geometry"]].rename(columns={"ref": "parcel_ref"})
    parts = [gpd.GeoDataFrame({"parcel_company": g["name"].map(scrub_contact).values, "parcel_company_source": label, "_rank": k},
                              geometry=g.geometry.values, crs=g.crs).to_crs(par.crs) for k, (label, g) in enumerate(named) if len(g)]
    if par.empty or not parts:
        return pd.DataFrame(columns=cols)
    pts = pd.concat(parts, ignore_index=True)
    pts = gpd.GeoDataFrame(pts[pts["parcel_company"] != ""].assign(_pi=lambda d: range(len(d))), geometry="geometry", crs=par.crs)
    j = gpd.sjoin(pts, par, predicate="within")
    return j.sort_values(["parcel_ref", "_rank", "_pi"]).drop_duplicates("parcel_ref")[cols].reset_index(drop=True)


def known_company(df: pd.DataFrame, sources=COMPANY_SOURCES) -> tuple[pd.Series, pd.Series]:
    """Best guess at the company on site -> (known_company, known_company_source). The first non-blank of `sources` (business
    at the building, AIA permit holder, OSM operator, OSM name); else `parcel_company` with `parcel_company_source` when
    `df` has them (something inside the same cadastral parcel). None and '' when nothing is known."""
    company, source = pd.Series(None, index=df.index, dtype=object), pd.Series("", index=df.index, dtype=object)
    for col, label in [*sources, ("parcel_company", None)]:
        if col not in df:
            continue
        v = df[col].map(_text).str.strip()
        take = company.isna() & (v != "")
        company[take] = v[take]
        source[take] = label if label is not None else df.loc[take, "parcel_company_source"].map(_text)
    return company, source


def visura_queue(ws: pd.DataFrame, n: int = 50) -> pd.DataFrame:
    """Read only: type what a visura tells you into contact_worksheet.csv (owner_from_visura, owner_type, notes), which keeps it.

    The owner lookups (visura per immobile) to do next, rebuilt on every run from the worksheet as written: the n largest
    candidate parcels where no company was found on any roof of the parcel, no owner_from_visura is typed and nobody
    objected (occupants.is_objection, after the parcel-wide spread). One row per parcel: its largest roof (lowest priority).
    """
    ref = ws["parcel_ref"].map(_text).str.strip()
    blank = lambda c: ws[c].map(_text).str.strip() == "" if c in ws else pd.Series(True, index=ws.index)
    stop = ~blank("known_company") | ~blank("owner_from_visura") | ws.get("do_not_contact", pd.Series("", index=ws.index)).map(is_objection)
    stop_parcel = stop.groupby(ref).transform("any")
    q = ws[(ref != "").values & ~stop_parcel.values].assign(_prio=lambda d: pd.to_numeric(d["priority"], errors="coerce"))
    q = q.sort_values("_prio", kind="stable").drop_duplicates("parcel_ref").head(n).copy()
    col = lambda c: q[c].map(_text).str.strip() if c in q else pd.Series("", index=q.index)
    q["ade_provincia"] = col("provincia").str.upper()
    q["ade_comune"] = col("comune").str.upper()
    q["ade_catasto"] = "Fabbricati (se non trovato: Terreni)"
    q["ade_sezione"] = ""        # urban section: blank for these municipalities
    q["ade_foglio"] = col("foglio")
    q["ade_particella"] = col("particella")
    q["ade_subalterno"] = ""     # blank lists every unit of the building
    return q[[c for c in QUEUE_COLS if c in q]].reset_index(drop=True)


def save_visura_queue(queue: pd.DataFrame, path, now: datetime | None = None) -> Path | None:
    """Write the queue. An existing file holding anything typed in the fill-in columns an earlier version had
    (QUEUE_FILL_IN) is first saved as visura_queue.backup-YYYYmmdd-HHMMSS.csv: never overwritten silently. -> backup or None."""
    path, now = Path(path), now or datetime.now()
    raw = path.read_bytes() if path.exists() else None
    old = _parse_table(raw) if raw is not None else None
    backup = _backup(path, raw, now) if old is not None and has_manual(old, QUEUE_FILL_IN).any() else None
    write_csv(queue, path)
    return backup
