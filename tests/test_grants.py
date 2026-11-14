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


def test_confirmed_owner_type_from_what_the_user_typed():
    from roofwatch.grants import confirmed_owner_type
    assert confirmed_owner_type("Azienda agricola Rossi") == "farm"
    assert confirmed_owner_type("srl") == "company"
    assert confirmed_owner_type("Società") == "company"
    assert confirmed_owner_type("privato") == "home"
    assert confirmed_owner_type("private person") == "home"
    assert confirmed_owner_type("Comune di Leno") == "public body"
    assert confirmed_owner_type("") is None and confirmed_owner_type("boh") is None


def test_contact_stage_from_worksheet_entries():
    from roofwatch.grants import contact_stage
    assert contact_stage({}) == "to identify"
    assert contact_stage({}, company_record=True) == "owner identified"
    assert contact_stage({"owner_from_visura": "Rossi Srl"}) == "owner identified"
    assert contact_stage({"contact_date": "2026-11-20"}) == "contacted"
    assert contact_stage({"outcome": "interessato, vuole preventivo"}) == "interested"
    assert contact_stage({"outcome": "non interessato"}) == "not interested"
    assert contact_stage({"outcome": "not interested"}) == "not interested"
    assert contact_stage({"do_not_contact": "yes", "outcome": "interessato"}) == "do not contact"
    assert contact_stage({"do_not_contact": "no"}) == "to identify"


def test_grant_eligibility_and_score():
    from roofwatch.grants import roof_grant_scores
    grants = load_grants(DATA)
    base = {"area_m2": 2000, "p": 0.9, "p_pv": 0.05, "stage": "to identify"}
    farm_guess = roof_grant_scores({**base, "owner_type": "farm", "confirmed_type": None}, grants)
    farm_conf = roof_grant_scores({**base, "owner_type": "farm", "confirmed_type": "farm"}, grants)
    assert set(farm_guess) >= {"parco_agrisolare_2026", "inail_isi_2025_asse3", "comune"} and "bonus_ristrutturazioni_2026" not in farm_guess
    assert farm_conf["parco_agrisolare_2026"]["certainty"] == "confirmed" and farm_guess["parco_agrisolare_2026"]["certainty"] == "guessed"
    assert farm_conf["parco_agrisolare_2026"]["score"] > farm_guess["parco_agrisolare_2026"]["score"]
    # a confirmed type overrides the guess: a farm the user found to be a private home is no longer Agrisolare-eligible
    home = roof_grant_scores({**base, "owner_type": "farm", "confirmed_type": "home"}, grants)
    assert "parco_agrisolare_2026" not in home and "bonus_ristrutturazioni_2026" in home
    # unknown owner: every scheme, low certainty
    unk = roof_grant_scores({**base, "owner_type": "unknown", "confirmed_type": None}, grants)
    assert unk["parco_agrisolare_2026"]["certainty"] == "unknown"
    assert unk["parco_agrisolare_2026"]["score"] < farm_guess["parco_agrisolare_2026"]["score"]
    # panels already on the roof make a solar scheme much less attractive; not interested scores zero
    pv = roof_grant_scores({**base, "p_pv": 0.95, "owner_type": "farm", "confirmed_type": None}, grants)
    assert pv["parco_agrisolare_2026"]["score"] < farm_guess["parco_agrisolare_2026"]["score"] / 2
    no = roof_grant_scores({**base, "stage": "not interested", "owner_type": "farm", "confirmed_type": None}, grants)
    assert no["parco_agrisolare_2026"]["score"] == 0
    assert roof_grant_scores({**base, "stage": "do not contact", "owner_type": "farm", "confirmed_type": None}, grants) == {}


def test_grant_lists_rank_eligible_roofs_and_pick_owner_lookups():
    import pandas as pd
    from roofwatch.grants import grant_lists

    ws = pd.DataFrame([
        {"roof_id": "1", "comune": "Verolanuova", "provincia": "Brescia", "area_m2": "9000", "model_score": "0.9", "solar_panels_score": "0.05",
         "likely_owner_type": "farm", "owner_type": "", "known_company": "", "owner_from_visura": "", "outcome": "", "do_not_contact": "",
         "foglio": "15", "particella": "124", "parcel_ref": "L777_001500.124", "address": "Via X 1"},
        {"roof_id": "2", "comune": "Leno", "provincia": "Brescia", "area_m2": "3000", "model_score": "0.8", "solar_panels_score": "0.05",
         "likely_owner_type": "unknown", "owner_type": "azienda agricola", "known_company": "", "owner_from_visura": "Rossi",
         "outcome": "", "do_not_contact": "", "foglio": "3", "particella": "7", "parcel_ref": "E526_000300.7", "address": ""},
        {"roof_id": "3", "comune": "Offlaga", "provincia": "Brescia", "area_m2": "20000", "model_score": "0.9", "solar_panels_score": "0.05",
         "likely_owner_type": "farm", "owner_type": "", "known_company": "", "owner_from_visura": "", "outcome": "", "do_not_contact": "si",
         "foglio": "1", "particella": "1", "parcel_ref": "G006_000100.1", "address": ""},
    ])
    lists = grant_lists(ws, load_grants(DATA))
    agri, lookups = lists["parco_agrisolare_2026"]
    assert agri["roof_id"].tolist() == ["1", "2"]                       # do-not-contact roof 3 left out; ranked by score
    assert agri.iloc[1]["owner_type_certainty"] == "confirmed" and agri.iloc[0]["owner_type_certainty"] == "guessed"
    assert lookups["roof_id"].tolist() == ["1"]                          # roof 2 already has an owner typed
    assert list(lookups.columns[:7]) == ["ade_provincia", "ade_comune", "ade_catasto", "ade_sezione", "ade_foglio", "ade_particella", "ade_subalterno"]
    assert lookups.iloc[0]["ade_comune"] == "VEROLANUOVA"
