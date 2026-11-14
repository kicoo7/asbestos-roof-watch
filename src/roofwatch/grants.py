"""Asbestos removal grants for the region (data/grants_lombardia.json) and which ones fit a roof.

The owner type is a best guess from public evidence already attached to a roof (environmental permit, named farm site,
business category, company legal form, roof size). It decides which schemes to show; it is not a legal assessment of
eligibility, which each call defines in detail.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

OWNER_TYPES = ("farm", "company", "home", "public body", "unknown")
_ORDER = {"open": 0, "upcoming": 1, "ask": 2, "closed": 3}
HOME_MAX_M2 = 300  # a roof this small with no business evidence is most likely a home


def load_grants(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _d(s):
    return date.fromisoformat(s) if s else None


def grant_status(grant: dict, today: date) -> str:
    """open / upcoming / closed by the grant's dates; 'ask' when it has none (e.g. municipal grants)."""
    opens, closes = _d(grant.get("opens")), _d(grant.get("closes"))
    if opens is None and closes is None:
        return "ask"
    if opens and today < opens:
        return "upcoming"
    if closes and today > closes:
        return "closed"
    return "open"


def _t(v) -> str:
    return "" if v is None or (isinstance(v, float) and v != v) else str(v).strip()


def likely_owner_type(e: dict) -> tuple[str, str]:
    """(owner type, why) from a roof's public evidence. Keys used, all optional: aia_type, osm_kind, osm_name,
    business_category, legal_form, known_company, business_name, area_m2."""
    aia, kind, cat = _t(e.get("aia_type")).lower(), _t(e.get("osm_kind")).lower(), _t(e.get("business_category")).lower()
    form = _t(e.get("legal_form")).lower()
    if form == "public_body":
        return "public body", "company record of a public body"
    if aia == "livestock farm":
        return "farm", "environmental permit for a livestock farm"
    if kind in ("farmyard", "farm", "farm_auxiliary", "barn", "cowshed", "sty"):
        return "farm", f"named farm site{': ' + _t(e.get('osm_name')) if _t(e.get('osm_name')) else ''}"
    if any(w in cat for w in ("agricultur", "farm")):
        return "farm", f"business category {cat}"
    if aia in ("industrial plant", "waste plant"):
        return "company", f"environmental permit for an {aia}" if aia[0] in "aeiou" else f"environmental permit for a {aia}"
    if form and form not in ("sole_trader_or_unknown", "association_or_foundation"):
        return "company", f"company record ({form})"
    if _t(e.get("known_company")) or _t(e.get("business_name")):
        return "company", "business listed at the site"
    try:
        if float(_t(e.get("area_m2")) or "nan") < HOME_MAX_M2:
            return "home", f"small roof (under {HOME_MAX_M2} m2) with no business listed"
    except ValueError:
        pass
    return "unknown", ""


def grant_fit(owner_type: str, grants: dict, today: date) -> list[dict]:
    """Schemes that apply to the owner type (all of them when unknown), open first, with their status today."""
    out = []
    for g in grants["grants"]:
        if owner_type != "unknown" and owner_type not in g["for"] and "unknown" not in g["for"]:
            continue
        out.append({"id": g["id"], "name": g["name"], "status": grant_status(g, today), "what": g["what"],
                    "opens": g.get("opens"), "closes": g.get("closes"), "close_meaning": g.get("close_meaning", ""),
                    "next_call": g.get("next_call", ""), "source": (g.get("sources") or [None])[0]})
    return sorted(out, key=lambda x: _ORDER[x["status"]])


def fit_summary(fit: list[dict]) -> str:
    """One line for a worksheet cell: open schemes first, e.g. 'OPEN: Bonus ... (until 2026-12-31); closed: INAIL ...'."""
    parts = []
    for x in fit:
        if x["id"] == "comune":
            continue
        tag = {"open": "OPEN", "upcoming": "UPCOMING", "closed": "closed", "ask": "ask"}[x["status"]]
        when = f" (until {x['closes']})" if x["status"] == "open" and x["closes"] else (
            f" (from {x['opens']})" if x["status"] == "upcoming" else "")
        parts.append(f"{tag}: {x['name']}{when}")
    return "; ".join(parts)


_FORM_KEYS = {"public body": "public_body", "sole trader or unknown legal form": "sole_trader_or_unknown",
              "association or foundation": "association_or_foundation"}


def roof_grants(detail: dict, ws_row: dict, area_m2, grants: dict, today: date) -> dict:
    """Grant block for one roof of the map panel.

    detail: the roof's panel record (companies at the building, occupants); ws_row: its worksheet row (permit, named
    site, business category, known company), possibly empty. Company records and listings next door are ignored.
    """
    ev = dict(ws_row or {})
    ev.setdefault("area_m2", area_m2)
    here = [c for c in detail.get("companies", []) if not c.get("next_door")]
    forms = [_FORM_KEYS.get(_t(c.get("legal_form")).lower(), "company") for c in here if not c.get("sole_trader")]
    if "public_body" in forms:
        ev["legal_form"] = "public_body"
    elif forms:
        ev["legal_form"] = next((f for f in forms if f == "company"), forms[0])
    if not _t(ev.get("business_name")) and detail.get("occupants"):
        ev["business_name"] = _t(detail["occupants"][0].get("name"))
    owner, why = likely_owner_type(ev)
    return {"region": grants.get("region", ""), "checked_on": grants.get("checked_on", ""), "owner_type": owner, "why": why,
            "fit": grant_fit(owner, grants, today), "obligations": grants.get("obligations", "")}


# --- grant-led workflow: confirmed owner type, contact stage, ranking -------------------------------------------------

_TYPED = [  # first match wins: farm words before company words ("azienda agricola" is a farm)
    ("farm", ("agricol", "farm", "allevament", "cascina", "zootecn", "agriturismo")),
    ("public body", ("comune", "pubblic", "public", "ente ", "provincia", "regione", "stato", "asl", "ats", "scuola")),
    ("company", ("azienda", "societ", "srl", "s.r.l", "spa", "s.p.a", "snc", "s.n.c", "sas", "s.a.s", "company", "impresa",
                 "ditta", "coop", "consorzio", "ltd", "business")),
    ("home", ("privat", "persona", "home", "famiglia", "condominio", "abitazione", "residenz")),
]
_NO = ("non interess", "not interest", "no grazie", "rifiut", "declin", "contrari")
_YES = ("interessat", "interested", "preventivo", "quote", "sopralluogo", "appuntamento", "yes", "sì", "si,")
CERTAINTY_WEIGHT = {"confirmed": 1.0, "guessed": 0.6, "unknown": 0.25}
STAGE_WEIGHT = {"to identify": 1.0, "owner identified": 1.1, "contacted": 1.0, "interested": 1.5, "not interested": 0.0}
STAGES = ("to identify", "owner identified", "contacted", "interested", "not interested")


def confirmed_owner_type(typed) -> str | None:
    """Grant owner type from what the user typed in the worksheet's owner_type (Italian or English), else None."""
    t = f" {_t(typed).lower()} "
    if not t.strip():
        return None
    for kind, words in _TYPED:
        if any(w in t for w in words):
            return kind
    return None


def contact_stage(ws_row: dict, company_record: bool = False) -> str:
    """Where a roof is in the outreach, from the worksheet's hand-filled columns. 'do not contact' overrides everything."""
    from .occupants import is_objection

    if is_objection(ws_row.get("do_not_contact")):
        return "do not contact"
    outcome = _t(ws_row.get("outcome")).lower()
    if any(w in outcome for w in _NO):
        return "not interested"
    if any(w in outcome for w in _YES):
        return "interested"
    if outcome or _t(ws_row.get("contact_date")) or _t(ws_row.get("contact_method")):
        return "contacted"
    if _t(ws_row.get("owner_from_visura")) or company_record:
        return "owner identified"
    return "to identify"


def roof_grant_scores(roof: dict, grants: dict) -> dict:
    """Per scheme this roof may be eligible for: certainty (confirmed / guessed / unknown) and a ranking score.

    roof: area_m2, p (asbestos score), p_pv (solar panels score), owner_type (guess), confirmed_type (from the worksheet or
    None), stage. Score = area x asbestos score x certainty weight x solar fit (schemes that also fund panels: x1.3 when
    the roof has none, x0.3 when it already has them) x stage weight. Do-not-contact roofs get nothing.
    """
    if roof.get("stage") == "do not contact":
        return {}
    conf = roof.get("confirmed_type")
    otype = conf or roof.get("owner_type") or "unknown"
    certainty = "confirmed" if conf else ("unknown" if otype == "unknown" else "guessed")
    try:
        area, p = float(roof.get("area_m2") or 0), float(roof.get("p") or 0)
    except (TypeError, ValueError):
        area, p = 0.0, 0.0
    try:
        ppv = float(roof.get("p_pv"))
    except (TypeError, ValueError):
        ppv = None
    out = {}
    for g in grants["grants"]:
        if otype != "unknown" and otype not in g["for"] and "unknown" not in g["for"]:
            continue
        solar = 1.0
        if g.get("solar") and ppv is not None:
            solar = 1.3 if ppv < 0.2 else (0.3 if ppv >= 0.9 else 1.0)
        score = area * p * CERTAINTY_WEIGHT[certainty] * solar * STAGE_WEIGHT.get(roof.get("stage", "to identify"), 1.0)
        out[g["id"]] = {"certainty": certainty, "score": round(score, 1)}
    return out


def grant_lists(ws, grants: dict, n_lookups: int = 50) -> dict:
    """Per scheme, from the contact worksheet: (eligible roofs ranked by score, owner lookups to do next).

    Eligible: roof_grant_scores with the worksheet's guess (likely_owner_type), the owner type the user typed (owner_type)
    and the contact stage; do-not-contact roofs are left out. Owner lookups: eligible roofs whose owner type is not
    confirmed and that have no owner typed and no company on site, best score first, with the Agenzia delle Entrate form
    fields in front.
    """
    import pandas as pd

    rows = ws.to_dict("records")
    base = []
    for w in rows:
        stage = contact_stage(w)
        roof = {"area_m2": w.get("area_m2"), "p": w.get("model_score"), "p_pv": w.get("solar_panels_score"),
                "owner_type": _t(w.get("likely_owner_type")) or "unknown", "confirmed_type": confirmed_owner_type(w.get("owner_type")),
                "stage": stage}
        base.append((w, roof, roof_grant_scores(roof, grants)))
    out = {}
    for g in grants["grants"]:
        recs = []
        for w, roof, sc in base:
            if g["id"] not in sc:
                continue
            otype = roof["confirmed_type"] or roof["owner_type"]
            recs.append({"roof_id": _t(w.get("roof_id")), "score": sc[g["id"]]["score"], "owner_type": otype,
                         "owner_type_certainty": sc[g["id"]]["certainty"], "owner_type_evidence": _t(w.get("owner_type_evidence")),
                         "stage": roof["stage"], "comune": _t(w.get("comune")), "provincia": _t(w.get("provincia")),
                         "area_m2": _t(w.get("area_m2")), "asbestos_score": _t(w.get("model_score")),
                         "solar_panels_score": _t(w.get("solar_panels_score")), "known_company": _t(w.get("known_company")),
                         "owner_from_visura": _t(w.get("owner_from_visura")), "foglio": _t(w.get("foglio")),
                         "particella": _t(w.get("particella")), "parcel_ref": _t(w.get("parcel_ref")), "address": _t(w.get("address")),
                         "status": _t(w.get("status")), "grant": g["name"]})
        df = pd.DataFrame(recs)
        if df.empty:
            out[g["id"]] = (df, df)
            continue
        df = df.sort_values("score", ascending=False, kind="stable").reset_index(drop=True)
        df.insert(0, "rank", range(1, len(df) + 1))
        lk = df[(df["owner_type_certainty"] != "confirmed") & (df["owner_from_visura"] == "") & (df["known_company"] == "")
                & (df["parcel_ref"] != "")].drop_duplicates("parcel_ref").head(n_lookups).copy()
        lk.insert(0, "ade_subalterno", "")
        lk.insert(0, "ade_particella", lk["particella"])
        lk.insert(0, "ade_foglio", lk["foglio"])
        lk.insert(0, "ade_sezione", "")
        lk.insert(0, "ade_catasto", "Fabbricati (se non trovato: Terreni)")
        lk.insert(0, "ade_comune", lk["comune"].str.upper())
        lk.insert(0, "ade_provincia", lk["provincia"].str.upper())
        out[g["id"]] = (df, lk.reset_index(drop=True))
    return out
