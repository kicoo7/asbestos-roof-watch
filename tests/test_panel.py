import importlib.util
import json
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from roofwatch.occupants import _rid as sheet_rid, read_worksheet
from roofwatch.panel import _rid, _s, build_details, load_inputs, panel_html, script_json

ROOT = Path(__file__).resolve().parents[1]
HOSTILE = "</script><script>alert(1)</script><!-- \u2028\u2029 & <b>x</b>"


def csv(rows: list[dict]) -> pd.DataFrame:
    """A synthetic input as load_inputs would read it: every column text, empty cells missing."""
    return pd.DataFrame(rows).astype("string").astype(object).where(lambda d: d.notna() & (d != ""), None)


def roofs(ids=(1, 2, 3)) -> pd.DataFrame:
    return pd.DataFrame({"roof_id": list(ids), "group": ["agree", "suspected_new", None][:len(ids)],
                         "label_txt": ["mapped AC (>=50% of roof)", "not mapped", "not mapped"][:len(ids)],
                         "comune": ["Testville"] * len(ids), "area_m2": [1500, 820, 600][:len(ids)],
                         "p": [0.912, 0.75, float("nan")][:len(ids)], "p_pv": [0.01, 0.1, 0.05][:len(ids)],
                         "lat": [45.4, 45.41, 45.42][:len(ids)], "lon": [10.1, 10.11, 10.12][:len(ids)]})


def worksheet(**over) -> pd.DataFrame:
    row = {"roof_id": "1", "status": "on ARPA map, model agrees", "comune": "Testville", "area_m2": "1500.0", "model_score": "0.91",
           "comune_code": "Z999",
           "foglio": "12", "particella": "345", "parcel_ref": "Z999_0012A0.345", "parcel_share": "0.87",
           "other_parcels": "Z999_0012A0.346;Z999_0013A0.7", "address": "Via Prova 1, Testville", "address_dist_m": "25.0",
           "business_name": "Worksheet Biz", "business_category": "metal_works", "business_website": "http://ws-biz.example",
           "business_address": "Via Prova 1", "business_dist_m": "0.0", "lat": "45.4", "lon": "10.1",
           "owner_from_visura": "Rossi Immobiliare srl", "owner_type": "company", "contact_method": "letter",
           "contact_date": "2026-11-01", "outcome": "no answer yet", "do_not_contact": "", "notes": "call back in spring"}
    return csv([row | over])


def occupants() -> pd.DataFrame:
    return csv([
        {"roof_id": "1", "rank": "2", "place_id": "p2", "name": "Second Biz", "category": "auto_repair", "website": "",
         "address": "Via Prova 1", "dist_m": "12.4", "inside": "False", "confidence": "0.71", "source": "overture"},
        {"roof_id": "1", "rank": "1", "place_id": "p1", "name": "Acme Coperture", "category": "roofing_contractor",
         "website": "http://www.Acme-Coperture.example/contatti", "address": "Via Prova 1", "dist_m": "0", "inside": "True",
         "confidence": "0.93", "source": "overture"},
        {"roof_id": "1", "rank": "3", "place_id": "p3", "name": "Mario Bianchi", "category": "plumber", "website": "bianchi.example",
         "address": "", "dist_m": "20", "inside": "False", "confidence": "0.5", "source": "overture"},
        {"roof_id": "2", "rank": "1", "place_id": "p4", "name": "Other Roof Biz", "category": "", "website": "https://shop.other.example",
         "address": "", "dist_m": "3", "inside": "False", "confidence": "", "source": "overture"},
    ])


def occupants_v2() -> pd.DataFrame:
    """roof_occupants.csv with relation/adjoining: rank restarts within (roof, relation); unknown relations are not shown."""
    nd = {"relation": "next_door", "source": "meta", "inside": "False"}
    return pd.concat([occupants().assign(relation="at_building", adjoining="False"), csv([
        nd | {"roof_id": "1", "rank": "2", "name": "Far Neighbour", "dist_m": "18.2", "adjoining": "False", "category": "bakery"},
        nd | {"roof_id": "1", "rank": "1", "name": "Vicino", "website": "http://vicino.example", "dist_m": "2", "adjoining": "True"},
        nd | {"roof_id": "1", "rank": "3", "name": "Acme Coperture Magazzino", "website": "acme-coperture.example", "dist_m": "9",
              "adjoining": "True"},
        nd | {"roof_id": "3", "rank": "1", "name": "Lonely Neighbour", "dist_m": "25", "adjoining": "False"},
        {"roof_id": "3", "rank": "1", "name": "Unknown Relation", "relation": "elsewhere"},
    ])], ignore_index=True)


def companies() -> pd.DataFrame:
    base = {"status": "ok", "vies_valid": "true", "vies_checked_on": "2026-11-10", "rea": "ZZ-123456",
            "fetched_on": "2026-11-09", "registered_office": "Via Sede 5, 00000 Testville"}
    return csv([
        base | {"domain": "acme-coperture.example", "website": "https://acme-coperture.example", "vat": "IT00000000001",
                "legal_name": "Acme Coperture S.r.l.", "legal_form": "srl", "entity_type": "company",
                "source_url": "https://acme-coperture.example/privacy"},
        base | {"domain": "bianchi.example", "website": "http://bianchi.example", "vat": "IT00000000002", "legal_name": "Bianchi Mario",
                "legal_form": "sole_trader_or_unknown", "entity_type": "sole_trader_or_unknown", "vies_valid": "",
                "source_url": "javascript:alert(1)"},
        base | {"domain": "shop.other.example", "status": "no_vat_found", "legal_name": "Should Not Show"},
        base | {"domain": "unused.example", "legal_name": "Nobody Links Here", "entity_type": "company"},
        base | {"domain": "vicino.example", "legal_name": "Vicino S.r.l.", "entity_type": "company", "vat": "IT00000000003"},
    ])


def all_values(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from all_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from all_values(v)
    else:
        yield obj


def test_build_details_merges_inputs_and_omits_empty_fields():
    ad = csv([{"roof_id": "2", "address": "Via Altra 9, Testville", "address_dist_m": "0.4"}])
    d = build_details(roofs(), worksheet(), occupants(), ad, companies())
    assert set(d) == {1, 2, 3}
    r1, r2, r3 = d[1], d[2], d[3]
    assert r1["group"] == "agree" and r1["area_m2"] == 1500 and r1["p"] == 0.91 and r1["candidate"] is True
    assert r1["address"] == "Via Prova 1, Testville" and r1["address_dist_m"] == 25  # worksheet fallback when roof_addresses lacks it
    assert r1["parcel"] == {"ref": "Z999_0012A0.345", "code": "Z999", "foglio": 12, "particella": "345", "share": 0.87,
                            "others": ["Z999 sheet 12 parcel 346", "Z999 sheet 13 parcel 7"]}
    assert [o["name"] for o in r1["occupants"]] == ["Acme Coperture", "Second Biz", "Mario Bianchi"]  # rank order
    acme = r1["occupants"][0]
    assert acme["inside"] is True and acme["category"] == "roofing contractor" and acme["domain"] == "acme-coperture.example"
    assert "inside" not in r1["occupants"][1] and r1["occupants"][1]["dist_m"] == 12 and "website" not in r1["occupants"][1]
    assert r2["address"] == "Via Altra 9, Testville" and "candidate" not in r2 and "parcel" not in r2 and "owner" not in r2
    assert r2["occupants"] == [{"name": "Other Roof Biz", "website": "https://shop.other.example", "domain": "shop.other.example",
                                "dist_m": 3, "source": "overture"}]
    assert build_details(roofs([2]), occupants=occupants().assign(source="meta"))[2]["occupants"][0]["source"] == "Overture places (meta)"
    assert "companies" not in r2  # status no_vat_found is not shown
    assert r3 == {"label": "not mapped", "comune": "Testville", "area_m2": 600, "p_pv": 0.05, "lat": 45.42, "lon": 10.12}
    for v in all_values(d):
        assert v is not None and v != "" and v != [] and v != {}
    json.dumps(d, allow_nan=False)


def test_occupants_fall_back_to_the_worksheet_listing_only_when_the_file_is_missing():
    d = build_details(roofs([1]), worksheet())
    assert d[1]["occupants"] == [{"name": "Worksheet Biz", "category": "metal works", "website": "http://ws-biz.example",
                                  "domain": "ws-biz.example", "address": "Via Prova 1", "inside": True, "dist_m": 0,
                                  "source": "Overture places"}]
    assert "occupants" not in build_details(roofs([1]), worksheet(), occupants=csv([{"roof_id": "2", "name": "x"}]))[1]


def test_company_record_joins_on_normalized_domain_and_flags_sole_traders():
    d = build_details(roofs([1]), worksheet(), occupants(), None, companies())[1]
    acme, bianchi = d["companies"]  # occupant order, one per domain, only status ok
    assert acme == {"domain": "acme-coperture.example", "business": "Acme Coperture", "legal_name": "Acme Coperture S.r.l.",
                    "vat": "IT00000000001", "legal_form": "Limited liability company (S.r.l.)", "office": "Via Sede 5, 00000 Testville",
                    "vies": True, "vies_on": "2026-11-10", "rea": "ZZ-123456", "source_url": "https://acme-coperture.example/privacy",
                    "fetched_on": "2026-11-09"}
    assert bianchi["sole_trader"] is True and bianchi["legal_form"] == "Sole trader or unknown legal form"
    assert "vies" not in bianchi and "vies_on" not in bianchi and "source_url" not in bianchi  # unsafe link dropped
    assert "Nobody Links Here" not in json.dumps(d)


def test_do_not_contact_hides_owner_and_notes():
    for flag in ("", "0", "no", "False"):
        d = build_details(roofs([1]), worksheet(do_not_contact=flag))[1]
        assert "do_not_contact" not in d and d["owner"]["owner"] == "Rossi Immobiliare srl" and d["owner"]["notes"] == "call back in spring"
    for flag in ("yes", "1", "x", "2026-11-02 asked by phone"):
        d = build_details(roofs([1]), worksheet(do_not_contact=flag))[1]
        assert d["do_not_contact"] is True
        assert d["owner"] == {"type": "company", "method": "letter", "date": "2026-11-01"}  # no name, notes or free-text outcome
        assert "Rossi" not in json.dumps(d) and "spring" not in json.dumps(d) and "no answer" not in json.dumps(d)


def test_do_not_contact_covers_every_candidate_roof_on_the_same_parcel():
    # an objection is about the property: roof 2 shares roof 1's parcel, roof 3 is on another one
    ws = pd.concat([worksheet(do_not_contact="yes"),
                    worksheet(roof_id="2", owner_from_visura="Second Owner", notes="second note"),
                    worksheet(roof_id="3", parcel_ref="Z999_0012A0.999", owner_from_visura="Third Owner")], ignore_index=True)
    d = build_details(roofs(), ws)
    assert d[1]["do_not_contact"] is True and "dnc_parcel" not in d[1]
    assert d[2]["do_not_contact"] is True and d[2]["dnc_parcel"] is True
    assert "owner" not in d[2]["owner"] and "notes" not in d[2]["owner"] and "Second" not in json.dumps(d[2])
    assert "do_not_contact" not in d[3] and d[3]["owner"]["owner"] == "Third Owner"


def test_contact_details_never_reach_the_page():
    ws = worksheet(phone="030 1234567", email="info@ws-biz.example", pec="ws@pec.example", notes="ring +39 333 123 4567 or a@b.example")
    occ = occupants().assign(phone="0301111111", email="x@acme.example", pec="acme@pec.example")
    co = companies().assign(pec="acme@legalmail.example", phone="0302222222", email="admin@acme.example")
    # websites that are really a phone number or an e-mail address (B1): WhatsApp, user info, contact details in the query
    occ = pd.concat([occ, csv([
        {"roof_id": "2", "rank": "2", "name": "Chat Shop", "website": "https://wa.me/393331234567"},
        {"roof_id": "2", "rank": "3", "name": "Mail Shop", "website": "http://info@mailshop.example/"},
        {"roof_id": "3", "rank": "1", "name": "Query Shop", "website": "https://shop.example/?mail=info@shop.example&tel=0301234567"},
        {"roof_id": "3", "rank": "2", "name": "Path Shop", "website": "https://pathshop.example/tel/3339876543"}])], ignore_index=True)
    co = pd.concat([co, csv([{"domain": "wa.me", "status": "ok", "legal_name": "Whatsapp Owner", "entity_type": "company"},
                             {"domain": "shop.example", "status": "ok", "legal_name": "Query Shop S.r.l.", "entity_type": "company",
                              "source_url": "https://shop.example/contatti?email=boss@shop.example&cell=3337654321"}])])
    fallback = worksheet(business_website="https://api.whatsapp.com/send?phone=393401112223")
    page = json.dumps(build_details(roofs(), ws, occ, None, co)) + json.dumps(build_details(roofs([1]), fallback))
    for s in ("1234567", "@", "0301111111", "0302222222", "pec.example", "legalmail", "393331234567", "0301234567",
              "3339876543", "3337654321", "393401112223", "wa.me", "whatsapp", "Whatsapp Owner", "%40"):
        assert s not in page, s
    assert '"website": "https://shop.example/"' in page  # the query is dropped, the shop's own site stays
    assert '"notes": "ring or"' in page  # the worksheet script's scrub_contact cuts both
    assert "IT00000000001" in page and "2026-11-10" in page and "00000 Testville" in page  # VAT, dates and postcodes stay


def test_hostile_strings_stay_inert_in_the_page():
    occ = csv([{"roof_id": "1", "rank": "1", "name": HOSTILE, "website": "javascript:alert(1)", "address": "<img src=x onerror=alert(1)>"}])
    co = csv([{"domain": "evil.example", "status": "ok", "legal_name": HOSTILE, "source_url": "data:text/html,x"}])
    details = build_details(roofs([1]), worksheet(business_website="javascript://evil.example/%0aalert(1)"), occ, None, co)
    assert "website" not in details[1]["occupants"][0]
    html = panel_html(details, "map_test")
    for bad in ("</script><script>alert", "<!--", "\u2028", "\u2029", "<img src=x", "javascript:", "data:text"):
        assert bad not in html
    assert html.count("</script>") == 3  # config, data, code: nothing in the data closed a script element early
    data = re.search(r'<script type="application/json" id="rw-data">(.*?)</script>', html, re.S).group(1)
    assert json.loads(data) == {"1": details[1]} and json.loads(data)["1"]["occupants"][0]["name"] == " ".join(HOSTILE.split())
    # raw separators that skip build_details' whitespace clean-up are escaped too
    raw = panel_html({7: {"comune": HOSTILE}}, "map_test")
    assert r"\u2028" in raw and r"\u2029" in raw and r"\u003c/script\u003e" in raw and raw.count("</script>") == 3
    for bad in ("</script><script>alert", "<!--", "\u2028", "\u2029"):
        assert bad not in raw
    assert json.loads(script_json({"s": HOSTILE}))["s"] == HOSTILE
    assert "innerHTML" not in html and "insertAdjacentHTML" not in html and "document.write" not in html


def test_missing_and_empty_input_files(tmp_path):
    (tmp_path / "roof_addresses.csv").write_text("")
    inputs = load_inputs(tmp_path)
    assert inputs == {"worksheet": None, "occupants": None, "addresses": None, "companies": None, "orphans": None, "suppress": frozenset()}
    d = build_details(roofs(), **inputs)
    assert set(d) == {1, 2, 3} and all("occupants" not in r and "candidate" not in r for r in d.values())
    assert 'id="rw-panel"' in panel_html(d, "map_test")
    worksheet().to_csv(tmp_path / "contact_worksheet.csv", index=False)
    ws = load_inputs(tmp_path)["worksheet"]
    assert ws["foglio"].iloc[0] == "12" and ws["do_not_contact"].iloc[0] == ""  # text as typed, read by the worksheet script's reader


def test_do_not_contact_on_any_row_of_a_roof_and_owner_fields_from_the_last_row_with_a_value():
    # S2: the user duplicated roof 1's row to log a second contact, and the later row carries the objection
    later = worksheet(owner_from_visura="New Owner", notes="asked us to stop", do_not_contact="yes")
    d = build_details(roofs(), pd.concat([worksheet(), later], ignore_index=True))
    assert d[1]["do_not_contact"] is True and "Rossi" not in json.dumps(d[1]) and "New Owner" not in json.dumps(d[1])
    # every row marks its own parcel: roof 2 sits on the parcel named in roof 1's second row
    other = worksheet(parcel_ref="Z999_0050A0.5", do_not_contact="yes")
    ws = pd.concat([worksheet(), other, worksheet(roof_id="2", parcel_ref="Z999_0050A0.5")], ignore_index=True)
    assert build_details(roofs(), ws)[2]["do_not_contact"] is True
    # per field, the last row that has a value
    second = worksheet(owner_from_visura="", contact_method="visit", contact_date="2026-11-12", outcome="", notes="")
    d = build_details(roofs([1]), pd.concat([worksheet(), second], ignore_index=True))[1]
    assert d["owner"] == {"owner": "Rossi Immobiliare srl", "type": "company", "method": "visit", "date": "2026-11-12",
                          "outcome": "no answer yet", "notes": "call back in spring"} and "do_not_contact" not in d


def test_orphaned_do_not_contact_marks_its_parcel_and_its_roof(tmp_path):
    # S1: roof 1's old row (parcel P, marked) was orphaned when roof 1 moved to parcel Q; roof 7 dropped out of the candidates
    ws = pd.concat([worksheet(parcel_ref="Z999_0099A0.9"), worksheet(roof_id="2")], ignore_index=True)  # roof 2 still on P
    orphans = csv([worksheet().iloc[0].to_dict() | {"do_not_contact": "yes", "orphan_reason": "parcel changed"},
                   worksheet().iloc[0].to_dict() | {"roof_id": "7", "parcel_ref": "Z999_0077A0.7", "do_not_contact": "si, 2026-10-01",
                                                    "orphan_reason": "roof gone"}])
    r = pd.concat([roofs([1, 2]), pd.DataFrame({"roof_id": [7], "group": ["mapped_not_seen"], "area_m2": [900]})], ignore_index=True)
    ws.to_csv(tmp_path / "contact_worksheet.csv", index=False)
    orphans.to_csv(tmp_path / "contact_worksheet_orphans.csv", index=False)
    d = build_details(r, **load_inputs(tmp_path))
    assert d[2]["do_not_contact"] is True and d[2]["dnc_parcel"] is True  # same property as the orphaned mark
    assert d[1]["do_not_contact"] is True and d[1]["dnc_orphan"] is True  # its own old row
    assert d[7]["do_not_contact"] is True and d[7]["dnc_orphan"] is True and "candidate" not in d[7]  # no longer a candidate
    assert "Rossi" not in json.dumps(d)


def test_hand_edited_files_are_read_exactly_as_the_worksheet_script_reads_them(tmp_path):
    # S3 and review3 minor 3: one reader (roofwatch.occupants) for the worksheet script and the map
    ws = worksheet(do_not_contact="sì", notes="Perché no")
    (tmp_path / "contact_worksheet.csv").write_bytes(b"\xef\xbb\xbf" + ws.to_csv(index=False, sep=";").encode("utf-8"))
    assert build_details(roofs([1]), **load_inputs(tmp_path))[1]["do_not_contact"] is True
    (tmp_path / "contact_worksheet.csv").write_bytes(ws.to_csv(index=False, sep="\t").encode("cp1252"))
    assert build_details(roofs([1]), **load_inputs(tmp_path))[1]["do_not_contact"] is True
    # whatever the script makes of an odd header, the map makes the same: it reads the objection or it stops
    (tmp_path / "contact_worksheet.csv").write_text('roof_id;do_not_contact;"called, again, later, twice"\n1;yes;x\n')
    try:
        read_worksheet(tmp_path / "contact_worksheet.csv")
    except ValueError:
        with pytest.raises(ValueError):
            load_inputs(tmp_path)
    else:
        assert build_details(roofs([1]), **load_inputs(tmp_path))[1]["do_not_contact"] is True
    (tmp_path / "contact_worksheet.csv").write_text("id|notes\n1|typed\n")
    with pytest.raises(ValueError, match="roof_id"):
        load_inputs(tmp_path)
    # the orphans file: an empty one is accepted as the script accepts it; one without its required columns stops the map
    paths = write_pilot(tmp_path / "pilot")
    (paths["out"] / "contact_worksheet_orphans.csv").write_text("")
    assert load_map_script().main(**paths)["roofs_in_panel"] == 5
    (paths["out"] / "aerial_map.html").unlink()
    orphans = worksheet(do_not_contact="yes").rename(columns={"do_not_contact": "obiezione"})
    orphans.to_csv(paths["out"] / "contact_worksheet_orphans.csv", index=False)
    with pytest.raises(ValueError, match="do_not_contact"):
        load_map_script().main(**paths)
    assert not (paths["out"] / "aerial_map.html").exists()


def test_do_not_contact_hides_sole_traders_outcome_and_search_also_next_door():
    # S4: Mario Bianchi (a sole trader, so his company name is his own) works at roof 1, whose owner objected;
    # roof 2 lists the same business next door
    occ = pd.concat([occupants_v2(), csv([{"roof_id": "2", "rank": "1", "relation": "next_door", "name": "Mario Bianchi",
                                           "website": "http://bianchi.example", "adjoining": "False", "dist_m": "20"}])], ignore_index=True)
    ws = worksheet(do_not_contact="yes")
    d = build_details(roofs(), ws, occ, None, companies())
    assert [c["legal_name"] for c in d[1]["companies"]] == ["Acme Coperture S.r.l.", "Vicino S.r.l."]  # companies stay
    assert "outcome" not in d[1]["owner"] and "Bianchi Mario" not in json.dumps(d)  # nowhere, not even next door
    allowed = build_details(roofs(), worksheet(), occ, None, companies())
    assert any(c["legal_name"] == "Bianchi Mario" for c in allowed[2]["companies"])  # without the objection it is shown
    assert "if (d.do_not_contact) return;" in panel_html(d, "map_test")  # objecting roofs are left out of the search index


@pytest.mark.parametrize("text, want", [
    ("(030) 1234567", "[removed]"), ("030 / 1234567", "[removed]"), ("+39 (0)30 1234567", "[removed]"),
    ("+39 333 123 4567", "[removed]"), ("0039 333 1234567", "[removed]"), ("3331234567", "[removed]"),
    ("tel. 030-1234567 int. 2", "tel. [removed] int. 2"), ("Via Roma 12 030 1234567", "Via Roma 12 [removed]"),
    ("info [at] rossi.it", "[removed]"), ("info(at)rossi(dot)it", "[removed]"), ("scrivi a a.rossi@rossi-srl.it oggi", "scrivi a [removed] oggi"),
    ("Via Roma 1234 25010 Brescia", "Via Roma 1234 25010 Brescia"), ("Via Industria 10/12/14/16/18/20", "Via Industria 10/12/14/16/18/20"),
    ("Via Sede 5, 00100 Roma", "Via Sede 5, 00100 Roma"), ("Via Roma 3000 25010 Brescia", "Via Roma 3000 25010 Brescia"),
    ("12.03.2026 10:30", "12.03.2026 10:30"), ("2020-2024", "2020-2024"), ("REA BS-123456", "REA BS-123456"),
    ("Capannone 1.234 m2", "Capannone 1.234 m2"),
])
def test_scrub_catches_phone_and_email_formats_and_keeps_addresses(text, want):
    assert _s(text, address=True) == want  # addresses and registered offices: road numbers and postcodes stay


@pytest.mark.parametrize("text, want", [
    ("Rossi Tel3331234567", "Rossi"), ("\uff10\uff13\uff10 \uff11\uff12\uff13\uff14\uff15\uff16\uff17", None),
    ("Ditta Bianchi (e-mail: info@bianchi.example)", "Ditta Bianchi"), ("ring +39 333 123 4567 or a@b.example", "ring or"),
    ("Acme Coperture S.r.l.", "Acme Coperture S.r.l."), ("2026-11-01", "2026-11-01"),
])
def test_names_and_free_text_use_the_worksheet_scripts_scrub(text, want):
    # review3 minor 5: names, owner fields and legal names go through roofwatch.occupants.scrub_contact
    assert _s(text) == want


def test_roof_id_must_be_a_whole_number():
    # '2.6' is not roof 3: its owner and objection must not land there; '3.0' (a spreadsheet's float) is roof 3
    stray = worksheet(roof_id="2.6", owner_from_visura="Stray Owner", do_not_contact="yes", parcel_ref="Z999_0026A0.6")
    d = build_details(roofs(), stray)
    assert "candidate" not in d[3] and "do_not_contact" not in d[3] and "Stray" not in json.dumps(d)
    assert build_details(roofs(), worksheet(roof_id="3.0"))[3]["candidate"] is True
    assert build_details(roofs(), worksheet(roof_id=" 3 "))[3]["candidate"] is True
    # review3 minor 3: the worksheet script's parser decides: a non-ASCII digit is no roof, whatever int() would make of it
    assert "do_not_contact" not in build_details(roofs(), worksheet(roof_id="\u0661", do_not_contact="yes"))[1]
    for v in ("1", "01", "1.0", " 3 ", "2.6", "\u0661", "", "abc", 7, 7.0, True):
        assert _rid(v) == (int(sheet_rid(v)) if sheet_rid(v).isascii() and sheet_rid(v).isdigit() and v is not True else None), v


def test_company_office_only_for_companies_and_notes_on_doubtful_records():
    co = pd.concat([companies(), csv([{"domain": "chain.example", "status": "ok", "legal_name": "Franchisor Holding S.p.A.",
                                       "entity_type": "company", "registered_office": "Via Sede 1, 20100 Milano",
                                       "tls_unverified": "true"}])], ignore_index=True)
    co.loc[co["domain"] == "acme-coperture.example", "legal_name"] = "Zeta Immobiliare S.r.l."
    chain = [{"roof_id": str(i), "rank": "1", "name": n, "website": "https://www.chain.example/negozi"}
             for i, n in ((1, "Market Testville"), (2, "Market Altrove"), (5, "Market Terzo"))]
    d = build_details(roofs(), worksheet(), pd.concat([occupants(), csv(chain)], ignore_index=True), None, co)
    by_name = {c["legal_name"]: c for c in d[1]["companies"]}
    assert "office" not in by_name["Bianchi Mario"]  # Minor 3: a sole trader's address is a person's home address
    assert by_name["Franchisor Holding S.p.A."]["office"] == "Via Sede 1, 20100 Milano"
    assert by_name["Zeta Immobiliare S.r.l."]["name_differs"] is True  # shares no word with "Acme Coperture" or the domain
    assert "name_differs" not in by_name["Bianchi Mario"]  # 'bianchi' is in the listing name and the domain
    assert by_name["Franchisor Holding S.p.A."]["chain"] is True and by_name["Franchisor Holding S.p.A."]["tls_unverified"] is True
    assert "chain" not in by_name["Zeta Immobiliare S.r.l."] and "tls_unverified" not in by_name["Zeta Immobiliare S.r.l."]
    page = panel_html(d, "map_test")
    for note in ("Legal name differs from the listing: check it is the same business.",
                 "Chain or shared website: the record may be the franchisor's, not this business's.",
                 "Read over a connection with an invalid certificate."):
        assert note in page


def test_tooltip_properties_are_html_escaped():
    # Minor 4: folium's GeoJsonTooltip inserts properties with innerHTML
    g = gpd.GeoDataFrame({"roof_id": [1], "comune": ["<img src=x onerror=alert(1)>"], "label_txt": ["mapped AC (>=50% of roof)"],
                          "area_m2": [1200]}, geometry=[box(10, 45, 10.001, 45.001)], crs=4326)
    props = load_map_script().geojson(g, ["roof_id", "comune", "label_txt", "area_m2"])["features"][0]["properties"]
    assert props == {"roof_id": 1, "comune": "&lt;img src=x onerror=alert(1)&gt;", "label_txt": "mapped AC (&gt;=50% of roof)",
                     "area_m2": 1200}


def test_an_objecting_roof_marks_its_current_parcel_too():
    # review2 S2c. (a) roof 1 objected on parcel P, the row was orphaned when roof 1 moved to Q: roof 3 on Q is the same property
    ws = pd.concat([worksheet(parcel_ref="Z999_0099A0.9"), worksheet(roof_id="2"), worksheet(roof_id="3", parcel_ref="Z999_0099A0.9"),
                    worksheet(roof_id="4", parcel_ref="Z999_0044A0.4")], ignore_index=True)
    orphans = csv([worksheet().iloc[0].to_dict() | {"do_not_contact": "yes", "orphan_reason": "parcel_ref changed"}])
    r = pd.DataFrame({"roof_id": [1, 2, 3, 4], "group": ["agree"] * 4, "area_m2": [900] * 4})
    d = build_details(r, ws, orphans=orphans)
    assert {k for k, v in d.items() if v.get("do_not_contact")} == {1, 2, 3}  # P (old row) and Q (current), not roof 4 on R
    assert d[3]["dnc_parcel"] is True and d[1]["dnc_orphan"] is True
    # (b) roof 1 has a row on P (generated, first) and a copied row on Q carrying the objection: both parcels are marked
    ws = pd.concat([worksheet(), worksheet(parcel_ref="Z999_0099A0.9", do_not_contact="yes"), worksheet(roof_id="2"),
                    worksheet(roof_id="3", parcel_ref="Z999_0099A0.9"), worksheet(roof_id="4", parcel_ref="Z999_0044A0.4")], ignore_index=True)
    d = build_details(r, ws)
    assert {k for k, v in d.items() if v.get("do_not_contact")} == {1, 2, 3}


FARM = [  # roof 1 (a barn) objects; the owner's sole-trader farm business is listed at the farmhouse (roof 2), 8 m away
    {"roof_id": "1", "rank": "1", "relation": "next_door", "name": "Azienda Agricola Rossi Mario", "website": "http://rossi-farm.example",
     "adjoining": "False", "dist_m": "8"},
    {"roof_id": "2", "rank": "1", "relation": "at_building", "name": "Azienda Agricola Rossi Mario", "website": "http://rossi-farm.example",
     "inside": "True", "dist_m": "0"},
    {"roof_id": "3", "rank": "1", "relation": "next_door", "name": "Azienda Agricola Rossi Mario", "website": "http://rossi-farm.example",
     "adjoining": "False", "dist_m": "25"},  # an unrelated neighbour
]
FARM_CO = {"domain": "rossi-farm.example", "status": "ok", "legal_name": "ROSSI MARIO", "entity_type": "sole_trader_or_unknown",
           "vat": "IT00000000009"}


def test_sole_trader_next_door_to_an_objection_is_hidden_on_every_roof():
    # review2 S3: next door beyond 1 m (not adjoining) is enough
    d = build_details(roofs(), worksheet(do_not_contact="yes"), csv(FARM), None, pd.concat([companies(), csv([FARM_CO])]))
    assert "ROSSI MARIO" not in json.dumps(d) and all(not v.get("companies") for v in d.values())
    shown = build_details(roofs(), worksheet(), csv(FARM), None, pd.concat([companies(), csv([FARM_CO])]))
    assert [c["legal_name"] for c in shown[2]["companies"]] == ["ROSSI MARIO"]  # without the objection it is shown


def test_listings_carrying_the_objecting_persons_name_are_hidden_where_they_appear():
    # review2 residual risk: on an objecting roof, a listing linked to a sole-trader record, or named like one, shows no name,
    # website or address there and wherever it appears next door; a company's listing and an unrelated name stay
    occ = csv(FARM + [
        {"roof_id": "1", "rank": "1", "relation": "at_building", "name": "Mario Rossi Termoidraulica", "inside": "True", "dist_m": "0",
         "address": "Via Campi 3", "category": "plumber"},  # no website: matched by two name words
        {"roof_id": "1", "rank": "2", "relation": "at_building", "name": "Acme Coperture", "website": "http://acme-coperture.example",
         "inside": "True", "dist_m": "0"},
        {"roof_id": "1", "rank": "3", "relation": "at_building", "name": "Bar Sport Rossi", "inside": "True", "dist_m": "0"},  # one word
        {"roof_id": "3", "rank": "2", "relation": "next_door", "name": "Mario Rossi Termoidraulica", "adjoining": "True", "dist_m": "1"}])
    d = build_details(roofs(), worksheet(do_not_contact="yes"), occ, None, pd.concat([companies(), csv([FARM_CO])]))
    assert [o.get("name") for o in d[1]["occupants"]] == [None, "Acme Coperture", "Bar Sport Rossi"]
    assert d[1]["occupants"][0] == {"hidden": True, "category": "plumber", "inside": True, "dist_m": 0}
    assert d[1]["next_door"] == [{"hidden": True, "dist_m": 8}]
    assert d[2]["occupants"] == [{"hidden": True, "inside": True, "dist_m": 0}]  # the farmhouse itself
    assert [o.get("hidden", False) for o in d[3]["next_door"]] == [True, True]
    page = json.dumps(d)
    for gone in ("Mario Rossi", "Rossi Mario", "ROSSI MARIO", "rossi-farm", "Via Campi"):
        assert gone not in page
    assert "Business listing hidden: do not contact" in panel_html(d, "map_test")
    shown = build_details(roofs(), worksheet(), occ, None, pd.concat([companies(), csv([FARM_CO])]))
    assert "hidden" not in json.dumps(shown) and shown[1]["occupants"][0]["name"] == "Mario Rossi Termoidraulica"


def test_empty_names_near_an_objection_do_not_crash(tmp_path):
    # a register row without legal_name and a listing without a name are read as NaN: found on the real inputs
    (tmp_path / "company_register.csv").write_text("domain,status,legal_name,entity_type\nrossi-farm.example,ok,,sole_trader_or_unknown\n")
    occ = csv(FARM + [{"roof_id": "3", "rank": "2", "relation": "next_door", "name": "", "dist_m": "12"}])
    occ.to_csv(tmp_path / "roof_occupants.csv", index=False)
    worksheet(do_not_contact="yes").to_csv(tmp_path / "contact_worksheet.csv", index=False)
    d = build_details(roofs(), **load_inputs(tmp_path))
    assert d[1]["do_not_contact"] is True and not any(v.get("companies") for v in d.values())


def test_objection_uses_the_shared_definition(monkeypatch):
    # review2 minor 1: one definition of an objection, roofwatch.occupants.is_objection, for the worksheet and the map
    import roofwatch.occupants as occupants_mod
    for value, objecting in (("FALSE", False), (" No ", False), ("0.0", False), ("", False), ("sì", True), ("x", True)):
        assert ("do_not_contact" in build_details(roofs([1]), worksheet(do_not_contact=value))[1]) is objecting, value
    monkeypatch.setattr(occupants_mod, "NOT_AN_OBJECTION", occupants_mod.NOT_AN_OBJECTION | {"nope"})
    assert "do_not_contact" not in build_details(roofs([1]), worksheet(do_not_contact="nope"))[1]


def test_hand_edited_reader_survives_bytes_that_are_not_cp1252(tmp_path):
    # review2 minor 4: a Mac Roman 'è' (0x8F) is undefined in cp1252; latin-1 decodes any byte, so roof_id is still checked
    body = worksheet(do_not_contact="yes", notes="caff\u00e8").to_csv(index=False).replace("caff\u00e8", "caffX").encode("ascii")
    (tmp_path / "contact_worksheet.csv").write_bytes(body.replace(b"caffX", b"caff\x8f"))
    assert build_details(roofs([1]), **load_inputs(tmp_path))[1]["do_not_contact"] is True
    (tmp_path / "contact_worksheet.csv").write_bytes(b"id;note\n1;caff\x8f\n")
    with pytest.raises(ValueError, match="roof_id"):
        load_inputs(tmp_path)


ROSSI = [  # the review's e1 case: the person's business at roof 1, the same listing next door to roof 2 and, under its
    # Facebook page, at roof 3, where an unrelated pizzeria also has a Facebook page; plus a shop on a subdomain
    {"roof_id": "1", "rank": "1", "relation": "at_building", "name": "Rossi Mario Idraulica", "website": "http://rossimario.example",
     "address": "Via Campi 3", "inside": "True", "dist_m": "0"},
    {"roof_id": "2", "rank": "1", "relation": "next_door", "name": "Rossi Mario Idraulica", "website": "http://rossimario.example",
     "adjoining": "False", "dist_m": "9"},
    {"roof_id": "3", "rank": "1", "relation": "at_building", "name": "Rossi Mario Idraulica", "website": "https://www.facebook.com/rossimario",
     "inside": "True", "dist_m": "0"},
    {"roof_id": "3", "rank": "2", "relation": "at_building", "name": "Pizzeria Da Gino", "website": "https://www.facebook.com/dagino",
     "inside": "True", "dist_m": "0"},
    {"roof_id": "2", "rank": "1", "relation": "at_building", "name": "Shop Uno", "website": "https://shop.uno.example", "inside": "True",
     "dist_m": "0"},
]
ROSSI_OK = {"domain": "rossimario.example", "status": "ok", "legal_name": "ROSSI MARIO", "entity_type": "sole_trader_or_unknown",
            "vat": "IT00000000009"}
ROSSI_SUPPRESSED = {"domain": "rossimario.example", "status": "suppressed"}  # what enrich_companies.py leaves after suppression


def _visible(d: dict) -> list[str]:
    return sorted(o["name"] for v in d.values() for o in v.get("occupants", []) + v.get("next_door", []) if "name" in o)


def test_suppressed_business_stays_hidden_before_and_after_the_register_is_rewritten(tmp_path):
    # review3 should fix 1: suppressing after an objection must not bring the listing back
    ws = worksheet(do_not_contact="yes")
    before = build_details(roofs(), ws, csv(ROSSI), None, csv([ROSSI_OK]))
    after = build_details(roofs(), ws, csv(ROSSI), None, csv([ROSSI_SUPPRESSED]))
    for d in (before, after):
        assert _visible(d) == ["Pizzeria Da Gino", "Shop Uno"]  # hidden on every roof; the pizzeria's shared host is no identity
        assert d[2]["next_door"] == [{"hidden": True, "dist_m": 9}] and not any(v.get("companies") for v in d.values())
        assert "Rossi Mario" not in json.dumps(d) and "rossimario" not in json.dumps(d)
    # the hand-written list alone, with no objection and the register still 'ok': gone everywhere, subdomains too
    (tmp_path / "company_suppress.csv").write_text("domain;vat\nrossimario.example;\nuno.example;\n")
    d = build_details(roofs(), None, csv(ROSSI), None, csv([ROSSI_OK]), suppress=load_inputs(tmp_path)["suppress"])
    assert _visible(d) == ["Pizzeria Da Gino"] and not any(v.get("companies") for v in d.values())
    assert "ROSSI MARIO" not in json.dumps(d)


def test_suppression_list_is_read_as_the_scripts_read_it(tmp_path):
    # review4 minor 2: occupants.load_suppressed_domains decides: any header case; a file with neither column stops the map
    (tmp_path / "company_suppress.csv").write_text("Domain;VAT\nrossimario.example;\n")
    d = build_details(roofs(), None, csv(ROSSI), None, csv([ROSSI_OK]), suppress=load_inputs(tmp_path)["suppress"])
    assert "Rossi Mario" not in json.dumps(d) and not any(v.get("companies") for v in d.values())
    paths = write_pilot(tmp_path / "pilot")
    (paths["out"] / "company_suppress.csv").write_text("dominio\nrossimario.example\n")
    with pytest.raises(ValueError, match="domain"):
        load_map_script().main(**paths)
    assert not (paths["out"] / "aerial_map.html").exists()


def test_subdomain_of_a_hidden_sole_traders_site_is_hidden_near_an_objection():
    # review4 minor 3: the shop at a subdomain of the objecting person's site (no record of its own) goes too
    shop = {"name": "Negozio Online", "website": "https://shop.rossimario.example", "dist_m": "0"}
    occ = csv([ROSSI[0], shop | {"roof_id": "1", "rank": "2", "relation": "at_building", "inside": "True"},
               shop | {"roof_id": "2", "rank": "2", "relation": "next_door", "adjoining": "False", "dist_m": "9"}])
    d = build_details(roofs(), worksheet(do_not_contact="yes"), occ, None, csv([ROSSI_OK]))
    assert _visible(d) == [] and "rossimario" not in json.dumps(d)
    assert _visible(build_details(roofs(), worksheet(), occ, None, csv([ROSSI_OK]))) == ["Negozio Online", "Negozio Online",
                                                                                         "Rossi Mario Idraulica"]


def test_shared_host_is_not_a_listing_identity():
    # review3 minor 4: a hidden listing with a Facebook page must not hide every listing with a Facebook page
    occ = csv([{**ROSSI[0], "website": "https://www.facebook.com/rossimario"}, ROSSI[3]])
    co = csv([{**ROSSI_OK, "domain": "x.example"}])  # the sole trader's record, found through another listing
    occ = pd.concat([occ, csv([{"roof_id": "1", "rank": "2", "relation": "at_building", "name": "Idraulica R", "website": "http://x.example",
                                "inside": "True", "dist_m": "0"}])], ignore_index=True)
    d = build_details(roofs(), worksheet(do_not_contact="yes"), occ, None, co)
    assert [o.get("name") for o in d[1]["occupants"]] == [None, None]  # both carry the person: hidden
    assert [o.get("name") for o in d[3]["occupants"]] == ["Pizzeria Da Gino"]  # not tied to them by facebook.com


def test_next_door_businesses_are_listed_apart_and_ranked_within_their_relation():
    d = build_details(roofs(), worksheet(), occupants_v2(), None, companies())
    r1, r3 = d[1], d[3]
    assert [o["name"] for o in r1["occupants"]] == ["Acme Coperture", "Second Biz", "Mario Bianchi"]
    assert r1["next_door"] == [
        {"name": "Vicino", "website": "http://vicino.example", "domain": "vicino.example", "adjoining": True, "dist_m": 2,
         "source": "Overture places (meta)"},
        {"name": "Far Neighbour", "category": "bakery", "dist_m": 18, "source": "Overture places (meta)"},
        {"name": "Acme Coperture Magazzino", "website": "http://acme-coperture.example", "domain": "acme-coperture.example",
         "adjoining": True, "dist_m": 9, "source": "Overture places (meta)"}]
    # company records: at the building first; a domain also listed next door keeps its at-building record
    assert [(c["legal_name"], c.get("next_door", False)) for c in r1["companies"]] == [
        ("Acme Coperture S.r.l.", False), ("Bianchi Mario", False), ("Vicino S.r.l.", True)]
    assert "occupants" not in r3 and r3["next_door"] == [{"name": "Lonely Neighbour", "dist_m": 25, "source": "Overture places (meta)"}]
    assert "Unknown Relation" not in json.dumps(d)
    # no relation column, or an empty relation, means at the building
    for occ in (occupants(), occupants().assign(relation="")):
        r = build_details(roofs([1]), occupants=occ)[1]
        assert "next_door" not in r and len(r["occupants"]) == 3


def test_same_parcel_lists_the_other_candidates_largest_first_capped():
    ws = pd.concat([worksheet(), worksheet(roof_id="2", area_m2="800", model_score="0.81"),
                    worksheet(roof_id="9", area_m2="2000.0", model_score="0.95"), worksheet(roof_id="3", parcel_ref="Z999_0099A0.1")])
    d = build_details(roofs(), ws)
    assert d[1]["same_parcel"] == [{"id": 9, "area_m2": 2000, "p": 0.95}, {"id": 2, "area_m2": 800, "p": 0.81}]
    assert [x["id"] for x in d[2]["same_parcel"]] == [9, 1] and "same_parcel_more" not in d[1]
    assert "same_parcel" not in d[3]  # alone on its parcel
    many = pd.concat([worksheet()] + [worksheet(roof_id=str(100 + i), area_m2=str(600 + i)) for i in range(13)])
    r = build_details(roofs([1]), many)[1]
    assert len(r["same_parcel"]) == 10 and r["same_parcel_more"] == 3 and r["same_parcel"][0] == {"id": 112, "area_m2": 612, "p": 0.91}
    assert '"and " + d.same_parcel_more + " more"' in panel_html(d, "map_test")  # the panel caps the list the same way


def test_small_roof_flag_only_for_model_groups_under_500_m2():
    r = pd.DataFrame({"roof_id": [1, 2, 3, 4, 5], "group": ["agree", "mapped_not_seen", None, "suspected_new", "suspected_new"],
                      "area_m2": [300, 499, 300, 500, 2000]})
    d = build_details(r)
    assert [d[i].get("small_roof", False) for i in range(1, 6)] == [True, True, False, False, False]
    assert "Under 500 m2: below the size where the model passed its accuracy test." in panel_html(d, "map_test")


def write_pilot(tmp: Path) -> dict:
    """Tiny synthetic pilot: 5 roofs (agree, suspected new, mapped not seen, solar only, small agree), labels, parcels, inputs."""
    raw, proc, out = tmp / "data/raw", tmp / "data/processed", tmp / "outputs"
    for p in (raw / "cadastre", proc / "chips", out):
        p.mkdir(parents=True)
    x0, y0 = 590000, 5030000
    geoms = [box(x0 + i * 100, y0, x0 + i * 100 + 40, y0 + 30) for i in range(4)] + [box(x0 + 400, y0, x0 + 420, y0 + 15)]
    gpd.GeoDataFrame({"roof_id": [1, 2, 3, 4, 5], "area_m2": [1200.0, 900.0, 700.0, 650.0, 300.0], "comune": ["Testville"] * 5,
                      "label": ["ac", "none", "ac", "none", "ac"]}, geometry=geoms, crs=32632).to_file(proc / "lombardy_roofs.gpkg", layer="roofs")
    pd.DataFrame({"roof_id": [1, 2, 3, 4, 5], "p": [0.9, 0.8, 0.2, 0.1, 0.9]}).to_parquet(out / "aerial_oof.parquet")
    pd.DataFrame({"roof_id": [1, 2, 3, 4, 5], "p_pv": [0.01, 0.5, 0.05, 0.02, 0.5]}).to_parquet(out / "pilot_pv_scores.parquet")
    labels = gpd.GeoDataFrame(geometry=[geoms[0], geoms[2]], crs=32632)
    labels.to_file(raw / "lombardy_pilot.gpkg", layer="ac_2020")
    gpd.GeoDataFrame(geometry=[box(x0 - 50, y0 - 50, x0 + 500, y0 + 100)], crs=32632).to_file(raw / "lombardy_pilot.gpkg", layer="surveyed_2020")
    gpd.GeoDataFrame({"ref": ["Z999_0012A0.345"], "comune": ["Z999"], "foglio": ["0012"], "particella": ["345"]},
                     geometry=[box(x0 - 5, y0 - 5, x0 + 45, y0 + 35)], crs=32632).to_crs(6706).to_file(raw / "cadastre/1_2.gpkg", layer="parcels")
    worksheet().to_csv(out / "contact_worksheet.csv", index=False)
    occupants_v2().to_csv(out / "roof_occupants.csv", index=False)
    companies().to_csv(out / "company_register.csv", index=False)
    return {"raw": raw, "proc": proc, "out": out}


def load_map_script():
    spec = importlib.util.spec_from_file_location("aerial_map", ROOT / "scripts/aerial_map.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_aerial_map_main_embeds_the_panel_once(tmp_path):
    paths = write_pilot(tmp_path)
    summary = load_map_script().main(**paths)
    html = (paths["out"] / "aerial_map.html").read_text()
    assert summary["roofs_in_panel"] == 5 and summary["with_businesses"] == 2 and summary["with_company_record"] == 1
    assert summary["with_next_door"] == 2 and summary["small_model_roofs"] == 1 and summary["sharing_parcel"] == 0
    assert 'id="rw-panel"' in html and html.count('id="rw-data"') == 1 and html.count("window.roofPanel.register") == 4  # 4 roof layers
    assert "bindPopup" not in html and "<img" not in html  # no per-layer popups, no chip markup repeated per feature
    assert html.count("Acme Coperture S.r.l.") == 1 and html.count("Z999_0012A0.345") == 2  # panel data once, parcel layer once
    assert '"chips":"../data/processed/chips/"' in html
    feats = [json.loads(m) for m in re.findall(r"_add\((\{.*?\})\);", html)]
    roof_layers = [f for f in feats if f["features"] and "roof_id" in f["features"][0]["properties"]]
    assert len(roof_layers) == 4 and all(set(x["properties"]) <= {"roof_id", "comune", "area_m2", "p", "p_pv"}
                                         for f in roof_layers for x in f["features"])
    assert "Input: cadastral parcels of candidate roofs (1)" in html and "Company records" in html
    assert "<div id='rw-search' class='rw-search'></div>" in html  # the legend hosts the roof search box
