from datetime import date
from pathlib import Path

from roofwatch.grants import grant_fit, grant_status, likely_owner_type, load_grants

DATA = Path(__file__).resolve().parents[1] / "data/grants_lombardia.json"


def g(opens=None, closes=None, **kw):
    return {"id": "x", "name": "X", "for": ["company"], "opens": opens, "closes": closes, **kw}


def test_grant_status_by_date():
    assert grant_status(g("2026-04-13", "2026-05-28"), date(2026, 11, 14)) == "closed"
    assert grant_status(g("2026-04-13", "2026-05-28"), date(2026, 5, 1)) == "open"
    assert grant_status(g("2026-04-13", "2026-05-28"), date(2026, 3, 1)) == "upcoming"
    assert grant_status(g(None, "2026-12-31"), date(2026, 11, 14)) == "open"
    assert grant_status(g(None, None), date(2026, 11, 14)) == "ask"


def test_likely_owner_type_from_evidence():
    assert likely_owner_type({"aia_type": "livestock farm"}) == ("farm", "environmental permit for a livestock farm")
    assert likely_owner_type({"osm_kind": "farmyard", "osm_name": "Cascina Bosco"})[0] == "farm"
    assert likely_owner_type({"legal_form": "public_body"})[0] == "public body"
    assert likely_owner_type({"aia_type": "industrial plant"})[0] == "company"
    assert likely_owner_type({"legal_form": "srl"})[0] == "company"
    assert likely_owner_type({"known_company": "Zincatura Bresciana SRL"})[0] == "company"
    assert likely_owner_type({"business_category": "agricultural_cooperative"})[0] == "farm"
    assert likely_owner_type({"area_m2": "180"})[0] == "home"
    assert likely_owner_type({"area_m2": "3000"}) == ("unknown", "")


def test_grant_fit_matches_owner_type_and_orders_open_first():
    grants = load_grants(DATA)
    today = date(2026, 11, 14)
    fit = grant_fit("farm", grants, today)
    names = [x["id"] for x in fit]
    assert "parco_agrisolare_2026" in names and "inail_isi_2025_asse3" in names and "bonus_ristrutturazioni_2026" not in names
    home = grant_fit("home", grants, today)
    assert home[0]["id"] == "bonus_ristrutturazioni_2026" and home[0]["status"] == "open"
    unknown = {x["id"] for x in grant_fit("unknown", grants, today)}
    assert {"bonus_ristrutturazioni_2026", "inail_isi_2025_asse3", "parco_agrisolare_2026"} <= unknown


def test_grants_file_is_well_formed():
    grants = load_grants(DATA)
    assert grants["region"] == "Lombardia" and grants["checked_on"]
    for x in grants["grants"]:
        assert {"id", "name", "for", "what", "opens", "closes", "next_call", "sources"} <= set(x)


def test_roof_grants_uses_worksheet_and_panel_company_records():
    from roofwatch.grants import roof_grants

    grants = load_grants(DATA)
    today = date(2026, 11, 14)
    # public body from a company record shown in the panel (human-readable legal form), at the building only
    d = {"companies": [{"legal_form": "Public body", "sole_trader": False, "next_door": False}]}
    assert roof_grants(d, {}, 2500, grants, today)["owner_type"] == "public body"
    # a next-door company does not decide the owner type
    d = {"companies": [{"legal_form": "Limited liability company (S.r.l.)", "sole_trader": False, "next_door": True}]}
    assert roof_grants(d, {}, 2500, grants, today)["owner_type"] == "unknown"
    # worksheet evidence: livestock permit
    out = roof_grants({}, {"aia_type": "livestock farm"}, 2500, grants, today)
    assert out["owner_type"] == "farm" and out["region"] == "Lombardia" and out["checked_on"] == "2026-11-14"
    assert any(x["id"] == "parco_agrisolare_2026" for x in out["fit"])
    # a business listed at the building in the panel
    assert roof_grants({"occupants": [{"name": "Officina Rossi"}]}, {}, 2500, grants, today)["owner_type"] == "company"
