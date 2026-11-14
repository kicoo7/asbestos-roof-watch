"""Roof detail panel for the aerial map: what is publicly known about who owns or occupies a roof.

build_details() merges the optional inputs in outputs/ (contact worksheet, roof occupants, roof addresses, company
register) into one compact record per clickable roof. panel_html() renders the panel (CSS, container, embedded data,
JavaScript) that scripts/aerial_map.py adds to the folium map; each roof layer calls REGISTER_JS per feature.

Privacy (docs/CONTACTING.md): only whitelisted fields pass, so phone, email and PEC columns never reach the page; e-mail
addresses and phone-like numbers inside free text are blanked too, and every website or source link goes through
roofwatch.urls.public_website. Owner names come only from the user's own visura. An objection (do_not_contact) belongs to
the property: it is read from every worksheet row and from the orphans file, covers every candidate roof on the parcel, and
hides the owner, notes, outcome and sole-trader records, also next door. The panel renders every string as text, never
HTML (the map's tooltips are folium's and insert HTML: scripts/aerial_map.py escapes their properties).
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path

import pandas as pd

from roofwatch.occupants import (_rid as _sheet_rid, business_domain, is_objection, load_suppressed_domains, read_orphans,
                                 read_worksheet, scrub_contact)
from roofwatch.urls import normalize_domain, public_website

INPUTS = {"worksheet": "contact_worksheet.csv", "occupants": "roof_occupants.csv", "addresses": "roof_addresses.csv",
          "companies": "company_register.csv", "orphans": "contact_worksheet_orphans.csv", "suppress": "company_suppress.csv"}
# files the user edits in a spreadsheet: read exactly as the worksheet script reads them (any separator, required columns);
# the suppression list becomes the set of suppressed domains, together with the register's `suppressed` rows
HAND_EDITED = {"worksheet": read_worksheet, "orphans": read_orphans,
               "suppress": lambda path: load_suppressed_domains(path, path.with_name(INPUTS["companies"]))}
LEGAL_FORMS = {"srl": "Limited liability company (S.r.l.)", "srls": "Simplified limited liability company (S.r.l.s.)",
               "spa": "Joint-stock company (S.p.A.)", "sapa": "Partnership limited by shares (S.a.p.a.)",
               "snc": "General partnership (S.n.c.)", "sas": "Limited partnership (S.a.s.)", "ss": "Simple partnership (S.s.)",
               "coop": "Cooperative", "consortium": "Consortium", "association_or_foundation": "Association or foundation",
               "public_body": "Public body", "sole_trader_or_unknown": "Sole trader or unknown legal form"}
OWNER_FIELDS = {"owner_from_visura": "owner", "owner_type": "type", "contact_method": "method", "contact_date": "date",
                "outcome": "outcome", "notes": "notes"}
MODEL_GROUPS = {"agree", "suspected_new", "mapped_not_seen"}
MIN_TESTED_M2 = 500  # the aerial model passed its accuracy test only for roofs this big (docs/RESULTS.md)
SAME_PARCEL_MAX = 10
PARCEL_RE = re.compile(r"^(?P<code>[A-Z]\d{3})_(?P<foglio>\d{4})\w\w\.(?P<part>.+)$")  # e.g. B157_0012A0.345
# e-mail / PEC addresses, also written 'info [at] rossi.it' or 'info(at)rossi(dot)it'
EMAIL_RE = re.compile(r"[\w.+-]+\s*(?:@|\[at\]|\(at\)|\{at\})\s*[\w-]+(?:\s*(?:\.|\[dot\]|\(dot\))\s*[\w-]+)*"
                      r"\s*(?:\.|\[dot\]|\(dot\))\s*[a-z]{2,24}\b", re.I)
# a run of digits with phone-style separators: '(030) 1234567', '030 / 1234567', '+39 (0)30 1234567'
DIGIT_RUN = re.compile(r"(?<!\w)[+(]?\d[\d ()./-]*\d")
GENERIC_WORDS = {"societa", "azienda", "agricola", "impresa", "ditta", "cooperativa", "consorzio", "gruppo", "group", "italia",
                 "italy", "service", "services", "servizi", "studio", "centro", "center", "holding", "limited", "company",
                 "sociale", "semplice", "responsabilita", "limitata", "unipersonale", "collettivo", "accomandita", "azioni"}
CHAIN_NAMES = 3  # a website listed under this many differently named businesses is a chain's or a shared one
NAME_STOP = GENERIC_WORDS | {"srl", "srls", "spa", "snc", "sas", "sapa", "coop", "della", "delle", "degli", "dei", "del", "per", "con",
                             "and", "the"}
REGISTER_JS = "function (feature, layer) { window.roofPanel.register(feature, layer); }"  # folium on_each_feature


def load_inputs(out: Path) -> dict[str, pd.DataFrame | None]:
    """The optional panel inputs in `out`, every column as text. None for a missing file.

    The worksheet, its orphans file and the suppression list are hand-edited: they are read with the worksheet script's own
    readers (roofwatch.occupants), so a re-saved file means the same thing to both, an empty file counts as missing, and a
    file without its required columns raises instead of silently dropping objections. `suppress` is a set of domains.
    """
    res = {}
    for key, name in INPUTS.items():
        path = Path(out) / name
        if key in HAND_EDITED:
            res[key] = HAND_EDITED[key](path)
            continue
        try:
            res[key] = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
        except (FileNotFoundError, pd.errors.EmptyDataError):
            res[key] = None
    return res


def _is_phone(run: str) -> bool:
    digits = re.sub(r"\D", "", run)
    if run.startswith(("+", "00")):
        return len(digits) >= 10  # country code + number
    lead = run.lstrip("(")[:1]  # Italian numbers: landlines start with 0, mobiles with 3 (10 digits); house numbers never with 0
    return (lead == "0" and len(digits) >= 9) or (lead == "3" and len(digits) >= 10)


def _scrub_phones(text: str) -> str:
    def repl(m):
        run = m.group(0)
        # 'Via Roma 12 030 1234567': the phone may start after a house number, so try each digit group as a start
        for i in [0] + [j for j in range(1, len(run)) if run[j - 1] in " /.-" and run[j] in "0123456789(+"]:
            if _is_phone(run[i:]):
                return run[:i] + "[removed]"
        return run
    return DIGIT_RUN.sub(repl, text)


def _s(v, scrub: bool = True, address: bool = False) -> str | None:
    """Clean text or None. `scrub` removes e-mail addresses and phone numbers: in names and free text with the worksheet
    script's scrub_contact; in an address (`address`) with the address-safe pattern above, which keeps road numbers and
    postcodes ('Via Roma 1234 25010 Brescia') that scrub_contact would cut."""
    if not isinstance(v, str) or not v.strip():
        return None
    v = " ".join(v.split())
    if scrub:
        v = _scrub_phones(EMAIL_RE.sub("[removed]", v)) if address else scrub_contact(v)
    return v or None


def _num(v, nd: int = 0) -> float | int | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return int(round(x)) if nd == 0 else round(x, nd)


def _words(v) -> str | None:
    s = _s(v)
    return s.replace("_", " ") if s else None


def _compact(d: dict) -> dict:
    """Drop empty fields: keeps the embedded data small and lets the panel hide lines with no data."""
    return {k: v for k, v in d.items() if v is not None and v is not False and v != "" and v != [] and v != {}}


def _rid(v) -> int | None:
    """An integer roof_id, or None, parsed by the worksheet script's own parser so both read a typed id the same way
    ('12', '12.0' -> 12; '2.6' or a non-ASCII digit -> None)."""
    t = "" if isinstance(v, bool) else _sheet_rid(v)
    return int(t) if t.isascii() and t.isdigit() else None


def _rows(df: pd.DataFrame | None) -> dict[int, list[dict]]:
    """roof_id -> rows (as dicts) in file order, skipping rows without a whole-number roof_id."""
    if df is None or "roof_id" not in df.columns:
        return {}
    out: dict[int, list[dict]] = {}
    for row in df.to_dict("records"):
        rid = _rid(row.get("roof_id"))
        if rid is not None:
            out.setdefault(rid, []).append(row)
    return out


def _parcel(w: dict) -> dict:
    ref = _s(w.get("parcel_ref"), scrub=False)
    m = PARCEL_RE.match(ref or "")
    foglio = _num(w.get("foglio"))
    others = []
    for o in (_s(w.get("other_parcels"), scrub=False) or "").split(";"):
        mo = PARCEL_RE.match(o.strip())
        if o.strip():
            others.append(f"{mo['code']} sheet {int(mo['foglio'])} parcel {mo['part']}" if mo else o.strip())
    return _compact({"ref": ref, "code": _s(w.get("comune_code"), scrub=False) or (m["code"] if m else None),
                     "foglio": foglio if foglio is not None else (int(m["foglio"]) if m else None),
                     "particella": _s(w.get("particella"), scrub=False) or (m["part"] if m else None),
                     "share": _num(w.get("parcel_share"), 2) if ref else None, "others": others})


def _true(v) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


def _relation(o: dict) -> str:
    """at_building (also when the column is missing or empty), next_door, or another value that is not shown."""
    return (_s(o.get("relation"), scrub=False) or "at_building").lower().replace("-", "_").replace(" ", "_")


def _occupant(o: dict, next_door: bool = False) -> dict:
    src = _words(o.get("source"))  # Overture's upstream dataset (meta, BrightQuery, ...): say it came through Overture
    src = src if not src or "overture" in src.lower() else f"Overture places ({src})"
    web = public_website(o.get("website"))  # a wa.me "website" is a phone number: no link, no domain, no company record
    return _compact({"name": _s(o.get("name")), "category": _words(o.get("category")), "website": web,
                     "domain": normalize_domain(web), "address": _s(o.get("address"), address=True),
                     # a next-door listing is at another building: say whether that building touches this roof
                     "adjoining" if next_door else "inside": _true(o.get("adjoining" if next_door else "inside")),
                     "dist_m": _num(o.get("dist_m")), "source": src, "confidence": _num(o.get("confidence"), 2)})


def _sole_trader(c: dict) -> bool:
    return (_s(c.get("entity_type"), scrub=False) or "") != "company"  # not positively a company: may be a person's name


def _fold(text) -> str:
    """Lower case without accents; '' for anything that is not text (an empty CSV cell is NaN)."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower() if isinstance(text, str) else ""


def _words4(text: str | None) -> set[str]:
    """Distinctive words of 4+ letters, accents folded: 'Società Agricola Rossi S.r.l.' -> {'rossi'}."""
    return set(re.findall(r"[a-z0-9]{4,}", _fold(text))) - GENERIC_WORDS


def _company(c: dict, business: str | None, next_door: bool = False, chain: bool = False) -> dict:
    form = _s(c.get("legal_form"), scrub=False)
    vies = str(c.get("vies_valid", "")).strip().lower() == "true"
    company = (_s(c.get("entity_type"), scrub=False) or "") == "company"
    dom = _s(c.get("domain"), scrub=False)
    legal = _s(c.get("legal_name"))
    # a VIES name sharing no distinctive word with the listing or the website may belong to another business
    differs = bool(legal and _words4(legal)) and not (_words4(legal) & (_words4(business) | _words4((dom or "").rsplit(".", 1)[0])))
    return _compact({"domain": dom, "business": business, "next_door": next_door, "legal_name": legal,
                     "vat": _s(c.get("vat"), scrub=False), "legal_form": LEGAL_FORMS.get(form, form.replace("_", " ") if form else None),
                     "sole_trader": not company,
                     "office": _s(c.get("registered_office"), address=True) if company else None,  # never next to a person's name
                     "vies": vies, "vies_on": _s(c.get("vies_checked_on"), scrub=False) if vies else None,
                     "rea": _s(c.get("rea"), scrub=False), "source_url": public_website(c.get("source_url")),
                     "fetched_on": _s(c.get("fetched_on"), scrub=False), "name_differs": differs, "chain": chain,
                     "tls_unverified": _true(c.get("tls_unverified"))})


def _dnc(w: dict) -> bool:
    return is_objection(w.get("do_not_contact"))  # the one definition, shared with the worksheet script


def _owner(rows: list[dict], dnc: bool) -> dict:
    """Hand-filled fields over all of a roof's worksheet rows, per field the last row with a value (a duplicated row logs a
    second contact). do_not_contact keeps only what says not to contact them: no name, notes or free-text outcome."""
    hidden = ("owner_from_visura", "notes", "outcome") if dnc else ()
    out = {}
    for col, short in OWNER_FIELDS.items():
        vals = [v for r in rows if (v := _s(r.get(col)))]
        if vals and col not in hidden:
            out[short] = vals[-1]
    return out


def _name_key(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(name))


def _name_words(text: str | None) -> set[str]:
    """Name words of 3+ letters, accents folded, without legal forms and generic words: 'Rossi Mario S.n.c.' -> {rossi, mario}."""
    return set(re.findall(r"[a-z]{3,}", _fold(text))) - NAME_STOP


def _listing_keys(o: dict) -> set[tuple[str, str]]:
    """How the same listing is recognised on another roof: its own website domain (not a shared host such as facebook.com,
    which would tie every listing with a Facebook page together) or its name."""
    return {k for k in (("dom", business_domain(o.get("website"))), ("name", _name_key(o.get("name")))) if k[1]}


OTHER_NAMES = ("aia_company", "osm_name", "osm_operator")  # names from public registers that may be a person's (a one-person farm)


def _other_sources(w: dict) -> dict:
    """The worksheet's other public sources for a roof (each optional): the environmental permit holder (Regione Lombardia
    AIA), the named OpenStreetMap site, and the business found on the same parcel when nothing was at the building."""
    web = public_website(w.get("osm_website"))
    aia = _compact({"name": _s(w.get("aia_company")), "type": _words(w.get("aia_type")), "address": _s(w.get("aia_address"), address=True),
                    "dist_m": _num(w.get("aia_dist_m"))})
    osm = _compact({"name": _s(w.get("osm_name")), "operator": _s(w.get("osm_operator")), "kind": _words(w.get("osm_kind")),
                    "website": web, "domain": normalize_domain(web), "dist_m": _num(w.get("osm_dist_m"))})
    parcel = _compact({"name": _s(w.get("parcel_company")), "source": _s(w.get("parcel_company_source"))})
    if "name" not in parcel and (_s(w.get("known_company_source")) or "").endswith("(same parcel)"):  # today's worksheet
        parcel = _compact({"name": _s(w.get("known_company")), "source": _s(w.get("known_company_source"))})
    return _compact({"aia": aia if "name" in aia else None, "osm": osm if osm.keys() & {"name", "operator"} else None,
                     "parcel": parcel if "name" in parcel else None})


def build_details(roofs: pd.DataFrame, worksheet: pd.DataFrame | None = None, occupants: pd.DataFrame | None = None,
                  addresses: pd.DataFrame | None = None, companies: pd.DataFrame | None = None,
                  orphans: pd.DataFrame | None = None, suppress=None) -> dict[int, dict]:
    """One compact, JSON-serialisable record per roof in `roofs`, keyed by roof_id.

    roofs: roof_id plus any of group, label_txt, comune, area_m2, p, p_pv, lat, lon (one row per clickable roof).
    worksheet, occupants, addresses, companies, orphans: the CSVs in outputs/ read as text (load_inputs), each optional;
    suppress: suppressed domains (load_inputs reads them with occupants.load_suppressed_domains).
    Addresses come from roof_addresses, else the worksheet. Businesses come from roof_occupants when that file exists
    (`occupants` at the building, `next_door` listed at a neighbouring building within 30 m; no `relation` column means
    all at the building), else from the worksheet's single business_* listing. Company records join on
    normalize_domain(public_website) == domain and only status ok records are kept. Candidate roofs list the other
    candidates on the same parcel (`same_parcel`, at most SAME_PARCEL_MAX, the rest counted in `same_parcel_more`).

    do_not_contact belongs to the property: a roof is marked when any of its worksheet rows is, when an orphaned row
    (contact_worksheet_orphans.csv) is about it, or when its current parcel is objecting. Objecting parcels are the
    parcel_ref of every objecting row (worksheet or orphans) plus the current parcel of every roof marked by its own row
    or by an orphan. Sole-trader records of every business at or next door to an objecting roof are hidden on every roof,
    and a listing there that carries the person's name is shown only as hidden, wherever it appears.

    Suppressed businesses (`domain` in company_suppress.csv, or a register row with status `suppressed`, which also covers
    suppression by VAT number) disappear: their listings, and the same listing under another website, are shown only as
    hidden on every roof, with no company record. A suppressed domain covers its subdomains.
    """
    ws, ads, occ = _rows(worksheet), _rows(addresses), _rows(occupants)
    by_parcel: dict[str, list[dict]] = {}  # parcel_ref -> candidate roofs on it, largest first: contact one property at a time
    for rid, rows in ws.items():
        ref = _s(rows[0].get("parcel_ref"), scrub=False)
        if ref:
            by_parcel.setdefault(ref, []).append(_compact({"id": rid, "area_m2": _num(rows[0].get("area_m2")),
                                                           "p": _num(rows[0].get("model_score"), 2)}))
    for v in by_parcel.values():
        v.sort(key=lambda x: (-x.get("area_m2", 0), x["id"]))
    # objections: every worksheet row and every orphaned row counts; the parcel of each marks the whole property
    marked = [r for rows in ws.values() for r in rows if _dnc(r)]
    marked_orphans = [r for r in (orphans.to_dict("records") if orphans is not None else []) if _dnc(r)]
    orphan_roofs = {rid for r in marked_orphans if (rid := _rid(r.get("roof_id"))) is not None}
    own_roofs = {rid for rid, rows in ws.items() if any(_dnc(r) for r in rows)}
    # the property is the parcel: each objecting row's parcel_ref, and the current parcel of each roof with its own mark
    dnc_parcels = {ref for r in marked + marked_orphans if (ref := _s(r.get("parcel_ref"), scrub=False))}
    dnc_parcels |= {ref for rid in own_roofs | orphan_roofs if rid in ws and (ref := _s(ws[rid][0].get("parcel_ref"), scrub=False))}
    dnc_kind: dict[int, str] = {}  # roof_id -> own | orphan | parcel: where the mark comes from, for the banner
    for rid, rows in ws.items():
        if any(_dnc(r) for r in rows):
            dnc_kind[rid] = "own"
        elif rid in orphan_roofs:
            dnc_kind[rid] = "orphan"
        elif _s(rows[0].get("parcel_ref"), scrub=False) in dnc_parcels:
            dnc_kind[rid] = "parcel"
    for rid in orphan_roofs:  # also a roof that is no longer a candidate
        dnc_kind.setdefault(rid, "orphan")

    def listings(rid: int) -> tuple[list[dict], list[dict]]:
        w = (ws.get(rid) or [None])[0]
        if occupants is not None:  # rank restarts within each relation, so sort first and split after
            rows = sorted(occ.get(rid, []), key=lambda o: (_num(o.get("rank"), 3) is None, _num(o.get("rank"), 3) or 0))
            return ([_occupant(o) for o in rows if _relation(o) == "at_building"],
                    [_occupant(o, next_door=True) for o in rows if _relation(o) == "next_door"])
        if w and _s(w.get("business_name")):
            return [_occupant({"name": w.get("business_name"), "category": w.get("business_category"),
                               "website": w.get("business_website"), "address": w.get("business_address"),
                               "dist_m": w.get("business_dist_m"), "source": "Overture places",
                               "inside": str(_num(w.get("business_dist_m")) == 0)})], []
        return [], []

    sup = {d for v in (suppress or ()) if (d := normalize_domain(v))}
    if companies is not None and {"domain", "status"} <= set(companies.columns):
        sup |= {d for c in companies.to_dict("records")
                if _s(c.get("status"), scrub=False) == "suppressed" and (d := normalize_domain(c.get("domain")))}

    def under(dom: str | None, doms) -> bool:  # the domain or a subdomain of it: shop.x.example is covered by x.example
        return bool(dom) and any(dom == x or dom.endswith("." + x) for x in doms)

    def suppressed(dom: str | None) -> bool:
        return under(dom, sup)
    cos = {}
    if companies is not None and "domain" in companies.columns:
        for c in companies.to_dict("records"):
            dom = normalize_domain(c.get("domain"))
            if dom and _s(c.get("status"), scrub=False) == "ok" and not suppressed(dom):
                cos.setdefault(dom, c)
    # a sole trader's company name is a person's name: never shown for any business at or next door to an objecting property,
    # on any roof, so it does not come back as the "next door" business of a neighbour (over-hiding is fine)
    near_objection = [o for rid in dnc_kind for o in sum(listings(rid), [])]
    hidden_domains = {o["domain"] for o in near_objection if o.get("domain")}
    person_doms = {d for d in hidden_domains if d in cos and _sole_trader(cos[d])}
    person_names = [w for d in person_doms if len(w := _name_words(cos[d].get("legal_name"))) >= 2]

    def carries_person(o: dict) -> bool:  # suppressed, on a sole trader's site (or a subdomain of it), or named like one
        dom = o.get("domain")
        if suppressed(dom) or under(dom, person_doms):
            return True
        if dom in cos:
            return _sole_trader(cos[dom])
        return any(len(_name_words(o.get("name")) & w) >= 2 for w in person_names)
    hidden_keys = set().union(*(_listing_keys(o) for o in near_objection if carries_person(o)))
    if sup:  # a suppressed business disappears from every roof, also where it is listed under another website
        everywhere = [o for rid in set(occ) | set(ws) for o in sum(listings(rid), [])]
        hidden_keys |= set().union(*(_listing_keys(o) for o in everywhere if suppressed(o.get("domain"))))
    # permit holders and named sites at an objecting property may be the person: their names (and site) go wherever they appear
    for rid in dnc_kind:
        for row in ws.get(rid, []):
            o = _other_sources(row)
            names = [row.get(c) for c in OTHER_NAMES]
            if "Overture" not in (o.get("parcel", {}).get("source") or ""):
                names.append(o.get("parcel", {}).get("name"))
            hidden_keys |= {("name", k) for n in names if (k := _name_key(_s(n)))}
            hidden_keys |= {("dom", k) for k in [business_domain(public_website(row.get("osm_website")))] if k}  # even with no site name
    hidden_names = {k for kind_, k in hidden_keys if kind_ == "name"}

    def entry_hidden(e: dict, *names) -> bool:  # a permit, site or same-parcel entry named like a hidden listing, or on its site
        dom = e.get("domain")
        return (any(_name_key(n) in hidden_names for n in names if n) or suppressed(dom) or under(dom, person_doms)
                or ("dom", business_domain(e.get("website"))) in hidden_keys)

    def hidden(o: dict) -> bool:
        return suppressed(o.get("domain")) or under(o.get("domain"), person_doms) or bool(_listing_keys(o) & hidden_keys)

    def shown(o: dict) -> dict:  # the listing as the page may show it: no name, website or address when it carries the person's
        if not hidden(o):
            return o
        return _compact({"hidden": True, "category": o.get("category"), "inside": o.get("inside"), "adjoining": o.get("adjoining"),
                         "dist_m": o.get("dist_m")})
    names_by_domain: dict[str, set[str]] = {}
    for rows in occ.values():
        for o in rows:
            if dom := normalize_domain(public_website(o.get("website"))):
                names_by_domain.setdefault(dom, set()).add(_name_key(o.get("name")))
    chains = {dom for dom, names in names_by_domain.items() if len(names - {""}) >= CHAIN_NAMES}

    details = {}
    for r in roofs.to_dict("records"):
        rid = _rid(r.get("roof_id"))
        if rid is None:
            continue
        rows_w = ws.get(rid) or []
        w = rows_w[0] if rows_w else None  # the generated columns: the first row (later ones are the user's copies)
        a = (ads.get(rid) or [w or {}])[0]
        kind = dnc_kind.get(rid)
        d = {"group": _s(r.get("group"), scrub=False), "label": _s(r.get("label_txt"), scrub=False), "comune": _s(r.get("comune")),
             "area_m2": _num(r.get("area_m2")), "p": _num(r.get("p"), 2), "p_pv": _num(r.get("p_pv"), 2),
             "lat": _num(r.get("lat"), 6), "lon": _num(r.get("lon"), 6),
             "address": _s(a.get("address"), address=True), "address_dist_m": _num(a.get("address_dist_m")) if _s(a.get("address")) else None}
        d["small_roof"] = d["group"] in MODEL_GROUPS and d["area_m2"] is not None and d["area_m2"] < MIN_TESTED_M2
        listing, nextdoor = listings(rid)
        seen, companies_here = set(), []
        for nd, group in ((False, listing), (True, nextdoor)):  # at-building records first, one per domain
            for o in group:
                dom = o.get("domain")
                if dom in cos and dom not in seen:
                    seen.add(dom)
                    hide = hidden(o)
                    if _sole_trader(cos[dom]) and (kind or dom in hidden_domains or hide):
                        continue
                    companies_here.append(_company(cos[dom], None if hide else o.get("name"), next_door=nd, chain=dom in chains))
        d["occupants"] = [shown(o) for o in listing if o]
        d["next_door"] = [shown(o) for o in nextdoor if o]
        d["companies"] = companies_here
        if w is not None:  # upstream's other public sources and best guess (optional worksheet columns)
            other = _other_sources(w)
            known = _compact({"name": _s(w.get("known_company")), "source": _s(w.get("known_company_source"))})
            known = known if "name" in known and not d["occupants"] else {}
            if kind:  # an objecting property: nothing from these registers, which may name the person
                other, known = ({"hidden": True} if other else {}), ({"hidden": True} if known else {})
            else:
                gone = {key for key, e in other.items() if entry_hidden(e, e.get("name"), e.get("operator"))}
                # the best guess is one of these names: it goes when the entry it came from goes, or when it carries any name
                # hidden on this roof (permit holder, site name or operator, same-parcel name, hidden listing)
                src = (known.get("source") or "")
                came_from = ("parcel" if src.endswith("(same parcel)") else "aia" if src.startswith("AIA") else
                             "osm" if src.startswith("OSM") else None)
                gone_names = {_name_key(n) for key in gone for n in (other[key].get("name"), other[key].get("operator")) if n}
                gone_names |= {_name_key(o.get("name")) for o in listing + nextdoor if o.get("name") and hidden(o)}
                if known and (came_from in gone or _name_key(known["name"]) in gone_names or entry_hidden(known, known["name"])):
                    known = {"hidden": True}
                for key in gone:
                    e = other[key]
                    other[key] = _compact({"hidden": True, "type": e.get("type"), "kind": e.get("kind"), "dist_m": e.get("dist_m")})
            d.update(other=other, known=known)
        if kind:
            d.update(do_not_contact=True, dnc_parcel=kind == "parcel", dnc_orphan=kind == "orphan")
        if w is not None:
            ref = _s(w.get("parcel_ref"), scrub=False) or ""
            same = [x for x in by_parcel.get(ref, []) if x["id"] != rid]
            d.update(candidate=True, parcel=_parcel(w), owner=_owner(rows_w, bool(kind)), same_parcel=same[:SAME_PARCEL_MAX],
                     same_parcel_more=max(len(same) - SAME_PARCEL_MAX, 0) or None)
        details[rid] = _compact(d)
    return details


def script_json(obj) -> str:
    """JSON that is safe inside an HTML <script> element: '<', '>', '&' and U+2028/U+2029 are written as \\u escapes,
    so a value such as '</script>' or '<!--' cannot end the element or open a comment."""
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    for ch in "<>&\u2028\u2029":
        s = s.replace(ch, f"\\u{ord(ch):04x}")
    return s


CSS = """
.rw-panel{position:fixed;top:10px;right:10px;bottom:10px;width:380px;max-width:calc(100vw - 20px);z-index:10000;box-sizing:border-box;
 background:#fff;border:1px solid #999;box-shadow:0 2px 10px rgba(0,0,0,.3);display:flex;flex-direction:column;overflow:hidden;
 font:13px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;color:#222}
.rw-panel[hidden]{display:none}
.rw-close{position:absolute;top:6px;right:18px;width:32px;height:32px;padding:0;border:0;border-radius:16px;background:#eee;
 font-size:20px;line-height:32px;cursor:pointer;color:#333;z-index:1}
.rw-close:hover,.rw-close:focus{background:#ddd}
#rw-body{flex:1;overflow:auto;overscroll-behavior:contain;padding:10px 14px 14px}
.rw-name{font-weight:600}
.rw-panel h2{font-size:15px;font-weight:600;margin:4px 40px 2px 0;line-height:1.3}
.rw-panel h3{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#555;margin:14px 0 6px;
 padding-top:10px;border-top:1px solid #e3e3e3}
.rw-panel p{margin:4px 0}
.rw-panel a{color:#0b5cad}
.rw-sw{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.rw-sub,.rw-muted{color:#666}
.rw-row{margin:2px 0}
.rw-k{color:#666}
.rw-chip{display:block;width:180px;height:180px;margin:8px 0 4px;background:#000}
.rw-note{font-size:12px;color:#555}
.rw-caveat{font-size:12px;background:#fff7e0;border-left:3px solid #e6a700;padding:4px 8px}
.rw-warn{font-size:12px;background:#fff1f0;border-left:3px solid #d7191c;padding:4px 8px}
.rw-dnc{background:#c62828;color:#fff;font-weight:700;padding:8px 10px;margin:0 40px 8px 0;border-radius:3px}
.rw-dnc small{display:block;font-weight:400}
.rw-item{margin:6px 0 10px;padding-left:8px;border-left:3px solid #e3e3e3}
.rw-links a{margin-right:10px;white-space:nowrap}
.rw-panel h4{font-size:12px;font-weight:600;color:#444;margin:12px 0 2px}
.rw-tag{display:inline-block;margin-right:6px;padding:0 5px;border-radius:3px;background:#e6ecf5;color:#28456b;font-size:10px;
 font-weight:700;letter-spacing:.04em;text-transform:uppercase;vertical-align:1px}
.rw-same{list-style:none}
.rw-jump{padding:0;border:0;background:none;color:#0b5cad;text-decoration:underline;font:inherit;cursor:pointer;text-align:left}
.rw-panel ul{margin:2px 0;padding-left:18px}
.rw-copy{margin-left:6px;font-size:11px;padding:1px 7px;border:1px solid #999;border-radius:3px;background:#f6f6f6;cursor:pointer}
.rw-foot{font-size:11px;color:#666;margin-top:14px;padding-top:8px;border-top:1px solid #e3e3e3}
.rw-search input{width:100%;box-sizing:border-box;margin:0 0 6px;padding:3px 6px;border:1px solid #999;border-radius:3px;
 font:12px system-ui,sans-serif}
.rw-results{list-style:none;margin:0 0 4px;padding:0;max-height:40vh;overflow:auto}
.rw-results button{display:block;width:100%;padding:2px 4px;border:0;background:none;text-align:left;font:12px system-ui,sans-serif;
 color:#0b5cad;cursor:pointer}
.rw-results button:hover,.rw-results button:focus{background:#eef4fb}
body.rw-open .leaflet-right{right:392px}
@media (max-width:699px){
 .rw-panel{top:auto;left:0;right:0;bottom:0;width:auto;max-width:none;height:55vh;border-radius:12px 12px 0 0;border-width:1px 0 0}
 body.rw-open .leaflet-right{right:0}
 body.rw-open .leaflet-bottom{bottom:55vh}
 /* a phone has room for the sheet and the roof only: the boxes come back when the sheet closes */
 body.rw-open .rw-legend,body.rw-open .rw-sources,body.rw-open .leaflet-control-layers{display:none}
}
"""

JS = r"""
(function () {
  "use strict";
  var CFG = JSON.parse(document.getElementById("rw-config").textContent);
  var DATA = JSON.parse(document.getElementById("rw-data").textContent);
  var panel = document.getElementById("rw-panel"), body = document.getElementById("rw-body");
  var layers = {}, map = null, current = null, mark = null;

  // DOM helpers: every data string goes in as a text node, links only as http(s) with noopener
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function add(parent) {
    for (var i = 1; i < arguments.length; i++) {
      var c = arguments[i];
      if (c != null && c !== "") parent.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return parent;
  }
  function link(href, text) {
    if (!/^https?:\/\//i.test(href || "")) return el("span", null, text);
    var a = el("a", null, text);
    a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
    return a;
  }
  function row(parent, label, value) {
    if (value == null || value === "") return;
    add(parent, add(el("div", "rw-row"), el("span", "rw-k", label + ": "), typeof value === "object" ? value : String(value)));
  }
  function section(title) { return add(body, add(el("section"), el("h3", null, title))).lastChild; }
  function q(s) { return encodeURIComponent(s); }
  function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : s; }

  function copyText(text, btn) {
    function done() { btn.textContent = "Copied"; setTimeout(function () { btn.textContent = "Copy"; }, 1500); }
    function fallback() {
      var t = el("textarea"); t.value = text; t.style.position = "fixed"; t.style.opacity = "0";
      document.body.appendChild(t); t.select();
      try { if (document.execCommand("copy")) done(); } catch (e) { /* nothing more to try */ }
      document.body.removeChild(t);
    }
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(done, fallback);
    else fallback();
  }

  function business(s, o, nextDoor) {
    var it = add(s, el("div", "rw-item")).lastChild;
    if (o.hidden) add(it, el("div", "rw-name rw-muted", "Business listing hidden: do not contact"));
    else add(it, el("div", "rw-name", o.name || "(listing without a name)"));
    if (o.category) add(it, el("div", "rw-muted", cap(o.category)));
    if (o.website) row(it, "Website", link(o.website, o.domain || o.website));
    row(it, "Listing address", o.address);
    var where = nextDoor ? (o.adjoining ? "adjoining building" : null) : (o.inside ? "inside the building" : null);
    if (!where && o.dist_m != null) where = o.dist_m + " m away";
    add(it, el("div", "rw-muted", [where, o.source ? "source: " + o.source : null,
      o.confidence != null ? "confidence " + o.confidence.toFixed(2) : null].filter(Boolean).join(" \u00b7 ")));
  }

  function otherNames(d) {  // names from the other public sources (hidden entries have none)
    var ot = d.other || {};
    return [(d.known || {}).name, (ot.aia || {}).name, (ot.osm || {}).name, (ot.osm || {}).operator, (ot.parcel || {}).name]
      .filter(Boolean);
  }

  function render(id, d) {
    body.textContent = "";
    if (d.do_not_contact) add(body, add(el("div", "rw-dnc", "Do not contact"),
      el("small", null, (d.dnc_parcel ? "Marked on another roof of the same parcel (same property). " :
        d.dnc_orphan ? "Marked in contact_worksheet_orphans.csv (a row that could not be carried over). " :
        "Marked in the contact worksheet. ") + "The owner, notes and outcome are hidden.")));

    // A. header
    var g = CFG.groups[d.group || ""] || CFG.groups[""], h = el("h2");
    var sw = el("span", "rw-sw"); sw.style.background = g.color;
    add(body, add(h, sw, cap(g.name)));
    add(body, el("div", "rw-sub", [d.comune, d.area_m2 != null ? d.area_m2.toLocaleString("en") + " m\u00b2 roof" : null]
      .filter(Boolean).join(" \u00b7 ")));
    add(body, el("div", null, [d.p != null ? "Asbestos score " + d.p.toFixed(2) : null,
      d.p_pv != null ? "Solar panels score " + d.p_pv.toFixed(2) : null].filter(Boolean).join(" \u00b7 ")));
    if (d.label) add(body, el("div", "rw-muted", "ARPA 2020 label: " + d.label));
    var img = el("img", "rw-chip"); img.alt = "Aerial image the model saw"; img.width = 180; img.height = 180;
    img.onerror = function () { img.remove(); };
    img.src = CFG.chips + id + ".jpg";
    add(body, img, el("p", "rw-caveat", "Suspected only: an inspection or lab test confirms asbestos."));
    if (d.small_roof) add(body, el("p", "rw-caveat", "Under 500 m2: below the size where the model passed its accuracy test."));

    // B. property
    var s = section("Property");
    if (d.address) row(s, "Address", d.address + (d.address_dist_m >= 1 ? " (about " + d.address_dist_m + " m from the roof)" : ""));
    var p = d.parcel;
    if (!d.candidate) add(s, el("div", "rw-muted", "Parcel not looked up (not a candidate roof)."));
    else if (!p || !p.ref) add(s, el("div", "rw-muted", "No cadastral parcel found under this roof."));
    else {
      var ref = [p.code ? "town code " + p.code : null, p.foglio != null ? "sheet (foglio) " + p.foglio : null,
                 p.particella ? "parcel (particella) " + p.particella : null].filter(Boolean).join(", ");
      var btn = el("button", "rw-copy", "Copy"); btn.type = "button"; btn.title = "Copy the cadastral reference";
      var text = [p.code, p.foglio != null ? "foglio " + p.foglio : null, p.particella ? "particella " + p.particella : null]
        .filter(Boolean).join(" ");
      btn.onclick = function () { copyText(text, btn); };
      row(s, "Cadastral parcel", add(el("span"), cap(ref), btn));
      if (p.share != null) row(s, "Share of the roof on it", Math.round(p.share * 100) + "%");
      if (p.others && p.others.length) row(s, "Other parcels under the roof", p.others.join("; "));
      add(s, el("div", "rw-muted", "Reference " + p.ref));
      var same = d.same_parcel || [], n = same.length + (d.same_parcel_more || 0);
      if (n) {  // other candidates on this parcel: one property, contacted once
        row(s, "Same parcel", n + " other candidate roof" + (n > 1 ? "s" : ""));
        var sl = add(s, el("ul", "rw-same")).lastChild;
        same.forEach(function (x) {
          var b = el("button", "rw-jump", [x.area_m2 != null ? x.area_m2.toLocaleString("en") + " m\u00b2 roof" : "roof " + x.id,
            x.p != null ? "asbestos score " + x.p.toFixed(2) : null, (DATA[x.id] || {}).do_not_contact ? "do not contact" : null]
            .filter(Boolean).join(" \u00b7 "));
          b.type = "button"; b.title = "Show this roof";
          b.onclick = function () { select(x.id, {zoom: true}); };
          add(sl, add(el("li"), b));
        });
        if (d.same_parcel_more) add(sl, el("li", "rw-muted", "and " + d.same_parcel_more + " more"));
      }
    }
    if (d.lat != null && d.lon != null) {
      var ll = d.lat + "," + d.lon;
      add(s, add(el("div", "rw-links"),
        link("https://www.google.com/maps/@" + ll + ",120m/data=!3m1!1e3", "Google satellite"),
        link("https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=" + ll, "Street View"),
        link("https://www.openstreetmap.org/?mlat=" + d.lat + "&mlon=" + d.lon + "#map=19/" + d.lat + "/" + d.lon, "OpenStreetMap")));
    }

    // C. businesses at the building, then the ones listed at a neighbouring building
    var occ = d.occupants || [], nd = d.next_door || [], kn = d.known;
    if (occ.length || nd.length || kn) {
      s = section("Businesses at this building (public listings)");
      if (kn) row(s, "Best guess for the company on site", kn.hidden ? "Hidden: do not contact" :
        kn.name + (kn.source ? " (" + kn.source + ")" : ""));
      add(s, occ.length ? el("p", "rw-note", "Often the tenant, not the owner.") : el("p", "rw-muted", "No business is listed at this building."));
      occ.forEach(function (o) { business(s, o, false); });
      if (nd.length) {
        add(s, el("h4", null, "Next door (within 30 m)"),
          el("p", "rw-note", "Listed at a neighbouring building: may be the same site or an unrelated neighbour."));
        nd.forEach(function (o) { business(s, o, true); });
      }
    }

    // C2. other public sources: environmental permit holder, named OpenStreetMap site, business on the same parcel
    var ot = d.other;
    if (ot) {
      s = section("Other public sources");
      if (ot.hidden) add(s, el("p", "rw-muted", "Hidden: do not contact"));
      if (ot.aia) {
        var a = ot.aia, it = add(s, el("div", "rw-item")).lastChild;
        add(it, el("div", null, "Environmental permit (AIA): " + (a.hidden ? "hidden: do not contact" : [a.name, a.type, a.address,
          a.dist_m >= 1 ? "about " + a.dist_m + " m away" : null].filter(Boolean).join(", "))),
          el("div", "rw-muted", "source: Regione Lombardia AIA permits"));
      }
      if (ot.osm) {
        var m = ot.osm, it2 = add(s, el("div", "rw-item")).lastChild;
        add(it2, el("div", null, "OpenStreetMap: " + (m.hidden ? "hidden: do not contact" : [m.name, m.operator ? "operated by " + m.operator : null,
          m.kind, m.dist_m >= 1 ? "about " + m.dist_m + " m away" : null].filter(Boolean).join(", "))));
        if (m.website && !m.hidden) row(it2, "Website", link(m.website, m.domain || m.website));
        add(it2, el("div", "rw-muted", "source: OpenStreetMap"));
      }
      if (ot.parcel) add(s, el("div", "rw-item", "On the same parcel: " + (ot.parcel.hidden ? "hidden: do not contact" :
        ot.parcel.name + (ot.parcel.source ? " (" + ot.parcel.source + ")" : ""))));
    }

    // D. company public records
    var cos = d.companies || [];
    if (cos.length) {
      s = section("Company public record");
      cos.forEach(function (c) {
        var it = add(s, el("div", "rw-item")).lastChild;
        add(it, add(el("div", "rw-name"), c.next_door ? el("span", "rw-tag", "Next door") : null, c.legal_name || "(legal name not found)"));
        if (c.business || c.domain) add(it, el("div", "rw-muted", "Website of " + [c.business, c.domain ? "(" + c.domain + ")" : null]
          .filter(Boolean).join(" ")));
        if (c.sole_trader) add(it, el("p", "rw-warn", "Sole trader or unknown legal form: the name may be a private person's. " +
          "Treat as personal data."));
        if (c.chain) add(it, el("div", "rw-muted", "Chain or shared website: the record may be the franchisor's, not this business's."));
        if (c.name_differs) add(it, el("div", "rw-muted", "Legal name differs from the listing: check it is the same business."));
        row(it, "VAT number (Partita IVA)", c.vat);
        row(it, "Legal form", c.legal_form);
        row(it, "Registered office", c.office);
        if (c.vat) add(it, el("div", null, c.vies ? "Confirmed by VIES" + (c.vies_on ? " on " + c.vies_on : "") :
          "Not in the EU VIES register (usual for a business that does not trade within the EU)"));
        row(it, "REA", c.rea);
        if (c.source_url) add(it, add(el("div", "rw-muted"), link(c.source_url, "Page it was read from"),
          c.fetched_on ? " (read on " + c.fetched_on + ")" : null));
        if (c.tls_unverified) add(it, el("div", "rw-muted", "Read over a connection with an invalid certificate."));
      });
    }

    // D2. grants for the region (data/grants_lombardia.json; status computed when the map was built)
    var gr = d.grants;
    if (gr && gr.fit && gr.fit.length) {
      s = section("Grants" + (gr.region ? " (" + gr.region + ")" : ""));
      row(s, "Likely owner", gr.owner_type === "unknown" ? "not known: every scheme listed" :
        gr.owner_type + (gr.why ? " (" + gr.why + ")" : ""));
      gr.fit.forEach(function (x) {
        var it = add(s, el("div", "rw-item")).lastChild;
        var tag = {open: "OPEN", upcoming: "UPCOMING", closed: "Closed", ask: "Ask"}[x.status] || x.status;
        add(it, add(el("div", "rw-name"), el("span", "rw-tag", tag), x.name));
        add(it, el("div", null, x.what));
        if (x.status === "open" && x.closes) add(it, el("div", null, "Until " + x.closes + (x.close_meaning ? ": " + x.close_meaning : "")));
        if (x.status === "upcoming" && x.opens) add(it, el("div", null, "Opens " + x.opens));
        if (x.status === "closed") add(it, el("div", "rw-muted", "Closed " + (x.closes || "") + (x.close_meaning ? ": " + x.close_meaning : "")));
        if (x.next_call && x.status !== "open") add(it, el("div", "rw-muted", "Next: " + x.next_call));
        if (x.source) add(it, add(el("div", "rw-muted"), link(x.source, "Source")));
      });
      if (gr.obligations) add(s, el("p", "rw-note", gr.obligations));
      add(s, el("p", "rw-muted", "Status as of " + (gr.built_on || "") + "; grant data checked on " + (gr.checked_on || "") +
        ". Owner type is a guess from public evidence; each call sets its own eligibility rules."));
    }

    // E. owner, from the user's own visura
    var o = d.owner || {};
    s = section("Owner (from your own visura)");
    row(s, "Owner", o.owner); row(s, "Owner type", o.type); row(s, "Contact method", o.method);
    row(s, "Contact date", o.date); row(s, "Outcome", o.outcome); row(s, "Notes", o.notes);
    if (d.do_not_contact) add(s, el("p", "rw-muted", "Owner, notes and outcome hidden: marked do not contact."));
    else if (!o.owner) add(s, el("p", "rw-note", "Owner names are not open data. Look them up one property at a time with a " +
      "visura per immobile (Agenzia delle Entrate), using " + (p && p.ref ? "the cadastral reference above." :
      "the property's cadastral reference (town, sheet and parcel).")));

    // F. look-ups, one at a time
    s = section("Look up (one at a time)");
    var ul = add(s, el("ul")).lastChild, names = {};
    occ.concat(nd, otherNames(d).map(function (n) { return {name: n}; })).forEach(function (b, i) {
      if (!b.name || names[b.name]) return;
      names[b.name] = 1;
      add(ul, add(el("li"), link(CFG.links.search + q(b.name + " " + (d.comune || "")),
        "Web search: " + b.name + ", " + (d.comune || "") + (i >= occ.length && i < occ.length + nd.length ? " (next door)" : ""))));
    });
    add(ul, add(el("li"), link(CFG.links.visura, "Cadastral lookup (visura): Agenzia delle Entrate")));
    add(ul, add(el("li"), link(CFG.links.register, "Italian business register: registroimprese.it")));
    add(ul, add(el("li"), link(CFG.links.vies, "Check a VAT number: EU VIES")));

    // G. footer
    add(body, add(el("div", "rw-foot"), "Sources: cadastral parcels, Agenzia delle Entrate (CC BY 4.0); addresses and places, " +
      "Overture Maps; company records, the businesses' own websites and EU VIES. ", el("b", null, "Private: do not share."),
      " See docs/CONTACTING.md."));
  }

  function pad() {  // keep the selected roof clear of the panel and the attribution line above it
    return window.innerWidth < 700 ? [20, panel.offsetHeight + 80] : [panel.offsetWidth + 30, 40];
  }
  function setHash(id) {
    var url = location.href.split("#")[0] + (id ? "#roof=" + id : "");
    if (location.href === url) return;
    try { history.replaceState(null, "", url); } catch (e) { location.hash = id ? "roof=" + id : ""; }
  }
  function highlight(id) {
    if (mark) { map.removeLayer(mark); mark = null; }
    var ls = layers[id];
    if (ls && ls.length) mark = L.geoJSON(ls[0].feature, {interactive: false,
      style: {color: "#ff00ff", weight: 4, opacity: 1, fill: false}}).addTo(map);
  }
  function select(id, opts) {
    id = String(id);
    var d = DATA[id];
    if (!d || !map) return false;
    current = id;
    render(id, d);
    panel.hidden = false;
    body.scrollTop = 0;
    document.body.classList.add("rw-open");
    highlight(id);
    setHash(id);
    var ls = layers[id];
    if (opts && opts.zoom) {
      if (ls && ls.length) map.fitBounds(ls[0].getBounds(), {maxZoom: 19, paddingTopLeft: [20, 20], paddingBottomRight: pad()});
      else if (d.lat != null) map.setView([d.lat, d.lon], 18);
    } else if (opts && opts.reveal && ls && ls.length) {
      map.panInside(ls[0].getBounds().getCenter(), {paddingTopLeft: [20, 20], paddingBottomRight: pad()});
    }
    return true;
  }
  function close() {
    if (current === null) return;
    current = null;
    panel.hidden = true;
    document.body.classList.remove("rw-open");
    if (mark) { map.removeLayer(mark); mark = null; }
    setHash(null);
  }
  function fromHash() {
    var m = /^#roof=(\d+)$/.exec(location.hash);
    if (m && m[1] !== current) select(m[1], {zoom: true});
  }

  // search: find a roof by business name, address or parcel reference, in the embedded data only
  function initSearch(box) {
    var input = el("input"), list = el("ul", "rw-results"), index = null;
    input.type = "search"; input.placeholder = "Find a roof: business, address or parcel";
    input.setAttribute("aria-label", "Find a roof by business name, address or parcel reference");
    add(box, list, input);
    function build() {  // per roof: searchable fields, next-door ones flagged so they rank after the roof's own
      index = [];
      Object.keys(DATA).forEach(function (id) {
        var d = DATA[id], p = d.parcel || {}, f = [];
        if (d.do_not_contact) return;  // an objecting property is not looked up by name or address
        function put(x, nd) { if (x) f.push({x: x, t: x.toLowerCase(), nd: nd}); }
        (d.occupants || []).forEach(function (o) { put(o.name, false); });
        (d.next_door || []).forEach(function (o) { put(o.name, true); });
        (d.companies || []).forEach(function (c) { put(c.legal_name, !!c.next_door); });
        otherNames(d).forEach(function (n) { put(n, false); });
        put(d.address, false); put(p.ref, false); put(p.ref ? [p.code, p.foglio, p.particella].join(" ") : null, false);
        if (f.length) index.push({id: id, f: f, own: f.filter(function (x) { return !x.nd; }).map(function (x) { return x.t; }).join(" | "),
                                  all: f.map(function (x) { return x.t; }).join(" | ")});
      });
    }
    function has(text, terms) { return terms.every(function (t) { return text.indexOf(t) >= 0; }); }
    function field(h, q) {  // the field that best explains the hit: the roof's own fields before next-door ones
      var f = h.f.filter(function (x) { return x.t.indexOf(q) >= 0; });
      return f.filter(function (x) { return !x.nd; })[0] || f[0];
    }
    function show() {
      list.textContent = "";
      var q = input.value.trim().toLowerCase(), terms = q.split(/\s+/).filter(Boolean);
      if (q.length < 3) return;
      if (!index) build();
      var hits = index.filter(function (h) { return has(h.all, terms); });
      function rank(h) { return (has(h.own, terms) ? 0 : 2) + (field(h, q) ? 0 : 1); }  // next-door-only matches last
      hits.sort(function (a, b) { return rank(a) - rank(b); });
      if (!hits.length) add(list, el("li", "rw-muted", "No roof found"));
      hits.slice(0, 8).forEach(function (h) {
        var f = field(h, q) || field(h, terms[0]) || h.f[0];
        var town = DATA[h.id].comune || "", label = f.x + (f.nd ? " \u00b7 next door" : "");
        var b = el("button", null, label + (town && label.toLowerCase().indexOf(town.toLowerCase()) < 0 ? " \u00b7 " + town : ""));
        b.type = "button";
        b.onclick = function () { list.textContent = ""; select(h.id, {zoom: true}); };
        add(list, add(el("li"), b));
      });
    }
    input.addEventListener("input", show);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { var b = list.querySelector("button"); if (b) b.click(); }
      if (e.key === "Escape") { input.value = ""; list.textContent = ""; e.stopPropagation(); }
    });
  }

  window.roofPanel = {
    register: function (feature, layer) {  // called by folium for every roof feature, also in layers hidden at load
      var id = String(feature.properties.roof_id);
      (layers[id] = layers[id] || []).push(layer);
      layer.on("click", function (e) { L.DomEvent.stopPropagation(e); select(id, {reveal: true}); });
    },
    select: select, close: close
  };
  document.getElementById("rw-close").onclick = close;
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  document.addEventListener("DOMContentLoaded", function () {
    map = window[CFG.map];
    if (!map) return;
    map.on("click", close);
    map.on("overlayadd", function (e) {  // an input layer switched on goes under the roofs so they stay clickable
      var first = e.layer.getLayers ? e.layer.getLayers()[0] : null;
      if (first && first.feature && first.feature.properties.roof_id == null) e.layer.bringToBack();
      if (mark) mark.bringToFront();
    });
    window.addEventListener("hashchange", fromHash);
    var box = document.getElementById("rw-search");
    if (box) initSearch(box);
    fromHash();
  });
})();
"""

LINKS = {"search": "https://www.google.com/search?q=",  # deep links checked in Chromium (DuckDuckGo blocks automated checks)
         "visura": "https://www.agenziaentrate.gov.it/portale/schede/fabbricatiterreni/visura-catastale",
         "register": "https://www.registroimprese.it/", "vies": "https://ec.europa.eu/taxation_customs/vies/"}


def panel_html(details: dict[int, dict], map_name: str, groups: dict[str, tuple[str, str]] | None = None,
               chip_dir: str = "../data/processed/chips/") -> str:
    """The panel for a folium map: CSS, container, the details as JSON (once, keyed by roof_id) and the JavaScript.

    map_name: the folium map's JavaScript name (Map.get_name()). groups: result group -> (words, colour); the key ""
    is used for roofs in no group. chip_dir: URL of the image chips relative to the html file.
    An element with id "rw-search" anywhere in the page becomes a search box (business name, address, parcel reference).
    """
    groups = {"": ("No asbestos result for this roof", "#1a9641"), **(groups or {})}
    cfg = {"map": map_name, "chips": chip_dir, "links": LINKS, "groups": {k: {"name": n, "color": c} for k, (n, c) in groups.items()}}
    return (f"<style>{CSS}</style>\n"
            '<aside id="rw-panel" class="rw-panel" role="dialog" aria-label="Roof details" hidden>'
            '<button id="rw-close" class="rw-close" type="button" aria-label="Close" title="Close (Esc)">&times;</button>'
            '<div id="rw-body"></div></aside>\n'
            f'<script type="application/json" id="rw-config">{script_json(cfg)}</script>\n'
            f'<script type="application/json" id="rw-data">{script_json({str(k): v for k, v in details.items()})}</script>\n'
            f"<script>{JS}</script>\n")
