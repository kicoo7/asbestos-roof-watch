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
