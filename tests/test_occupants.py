import io
from datetime import datetime

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, box

from roofwatch import occupants as occ
from roofwatch.occupants import OCCUPANT_COLS, assign_places, business_domain, merge_manual, name_key, nearest_address, website

CRS = 32632
MANUAL = ["owner_from_visura", "owner_type", "contact_method", "contact_date", "outcome", "do_not_contact", "notes"]


def roofs(*boxes, ids=None):
    return gpd.GeoDataFrame({"roof_id": ids or list(range(1, len(boxes) + 1))}, geometry=[box(*b) for b in boxes], crs=CRS)


def places(rows):
    """rows: (id, x, y, **fields) -> Overture-like places GeoDataFrame (synthetic)."""
    base = {"name": None, "category": "shop", "website": None, "address": None, "locality": None, "postcode": None,
            "confidence": 0.5, "operating_status": "open", "src": "meta"}
    recs = [{**base, "id": pid, **kw} for pid, x, y, kw in rows]
    return gpd.GeoDataFrame(recs, geometry=[Point(x, y) for _, x, y, _ in rows], crs=CRS)


def p(pid, x, y, **kw):
    kw.setdefault("name", pid.upper())
    return (pid, x, y, kw)


def test_inside_and_nearest_roof_competition():
    r = roofs((0, 0, 20, 20), (30, 0, 50, 20))
    pl = places([p("in_a", 10, 10), p("near_a", 24, 10), p("near_b", 27, 10), p("far", 200, 200), p("edge_b", 30, 5)])
    out = assign_places(r, pl, next_door=False)
    assert list(out.columns) == ["roof_id", "relation", "rank", "place_id", "name", "category", "website", "address", "dist_m", "inside",
                                 "confidence", "source", "adjoining"] == OCCUPANT_COLS
    got = {(row.roof_id, row.place_id): (row.dist_m, row.inside) for row in out.itertuples()}
    assert got == {(1, "in_a"): (0, True), (1, "near_a"): (4, False), (2, "near_b"): (3, False), (2, "edge_b"): (0, True)}
    # the shop 3 m from roof 2 is NOT attributed to roof 1, although it is only 7 m from it


def test_max_distance_and_closed_places():
    r = roofs((0, 0, 20, 20))
    pl = places([p("ok", 45, 10), p("too_far", 55, 10), p("closed", 10, 10, operating_status="permanently_closed"),
                 p("unknown_status", 12, 12, operating_status=None)])
    assert set(assign_places(r, pl, max_dist_m=30)["place_id"]) == {"ok", "unknown_status"}
    assert set(assign_places(r, pl, max_dist_m=10)["place_id"]) == {"unknown_status"}


def test_dedupe_same_name_keeps_highest_confidence():
    r = roofs((0, 0, 20, 20))
    pl = places([p("a1", 5, 5, name="Zincatura Rossi S.r.l.", confidence=0.6, src="meta"),
                 p("a2", 6, 6, name="ZINCATURA ROSSI SRL", confidence=0.93, src="BrightQuery"),
                 p("a3", 7, 7, name="Zincatùra  Rossi", confidence=0.4, src="Microsoft"),
                 p("b", 8, 8, name="Officina Bianchi", confidence=0.5)])
    out = assign_places(r, pl)
    assert sorted(out["place_id"]) == ["a2", "b"]
    row = out.set_index("place_id").loc["a2"]
    assert row["source"] == "BrightQuery" and row["confidence"] == 0.93


def test_dedupe_takes_placement_of_closest_listing():
    r = roofs((0, 0, 20, 20))
    pl = places([p("in_low", 10, 10, name="F M F di Rossi", website="http://www.fmf.it", confidence=0.6, src="meta"),
                 p("out_high", 36, 10, name="Officina FMF", website="https://fmf.it/officina", confidence=0.99, src="BrightQuery"),
                 p("other", 22, 10, name="Bar Centrale", confidence=0.9)])
    out = assign_places(r, pl)
    assert out["place_id"].tolist() == ["out_high", "other"]     # one business, ranked by its listing inside the building
    row = out.iloc[0]
    assert row["name"] == "Officina FMF" and row["source"] == "BrightQuery" and row["inside"] and row["dist_m"] == 0


def test_dedupe_same_domain_but_not_shared_hosts():
    r = roofs((0, 0, 20, 20))
    pl = places([p("a1", 5, 5, name="Acme", website="http://www.acme.it/contatti", confidence=0.5),
                 p("a2", 6, 6, name="Acme Industrie Meccaniche", website="https://acme.it", confidence=0.8),
                 p("a3", 7, 7, name="Gruppo A", website="acme.it", confidence=0.3),
                 p("f1", 8, 8, name="Bar Sport", website="https://www.facebook.com/barsport", confidence=0.6),
                 p("f2", 9, 9, name="Pizzeria Luna", website="https://m.facebook.com/luna", confidence=0.6)])
    assert sorted(assign_places(r, pl)["place_id"]) == ["a2", "f1", "f2"]


def test_dedupe_is_transitive_and_per_roof():
    r = roofs((0, 0, 20, 20), (100, 0, 120, 20))
    # x~z by name, y~z by domain -> one business; the same name on another roof is a different listing
    pl = places([p("x", 5, 5, name="Alfa", confidence=0.9), p("y", 6, 6, name="Beta", website="beta.it", confidence=0.7),
                 p("z", 7, 7, name="Alfa", website="beta.it", confidence=0.2), p("x2", 105, 5, name="Alfa", confidence=0.9)])
    out = assign_places(r, pl)
    assert out[["roof_id", "place_id"]].values.tolist() == [[1, "x"], [2, "x2"]]


def test_ranking_and_cap():
    r = roofs((0, 0, 20, 20))
    pl = places([p("in_low", 5, 5, confidence=0.3), p("in_high", 6, 6, confidence=0.9), p("out_12", 32, 10, confidence=0.99),
                 p("out_4", 24, 10, confidence=0.2), p("out_4b", 10, 24, confidence=0.6), p("out_20", 40, 10), p("out_25", 10, 45)])
    out = assign_places(r, pl)
    assert out["place_id"].tolist() == ["in_high", "in_low", "out_4b", "out_4", "out_12"]
    assert out["rank"].tolist() == [1, 2, 3, 4, 5]
    assert assign_places(r, pl, max_per_roof=2)["place_id"].tolist() == ["in_high", "in_low"]


def test_output_fields_address_website_crs():
    r = roofs((0, 0, 20, 20))
    pl = places([p("a", 5, 5, address="Via Roma 1", postcode="25025", locality="Manerbio", website="www.alfa.it", confidence=0.876, src="Microsoft"),
                 p("b", 6, 6, address="Via Po 2", locality="Leno", website="mailto:info@beta.it"),
                 p("c", 7, 7, website="javascript:alert(1)"), p("d", 8, 8, website="http://info@delta.it")])
    pl = pl.to_crs(4326)  # places in lon/lat are projected onto the roofs' CRS
    out = assign_places(r, pl).set_index("place_id")
    assert out.loc["a", "address"] == "Via Roma 1, 25025 Manerbio" and out.loc["b", "address"] == "Via Po 2, Leno"
    assert pd.isna(out.loc["c", "address"])
    assert out.loc["a", "website"] == "http://www.alfa.it" and out[["website"]].loc[["b", "c", "d"]].isna().all().all()
    assert out.loc["a", "confidence"] == 0.88 and out.loc["a", "source"] == "Microsoft" and out.loc["a", "dist_m"] == 0
    assert out["inside"].dtype == bool


def test_empty_inputs():
    r = roofs((0, 0, 20, 20))
    assert assign_places(r, places([p("far", 500, 500)])).empty
    assert list(assign_places(r, places([])).columns) == OCCUPANT_COLS


def rows(out, relation):
    s = out[out["relation"] == relation]
    return [(r.roof_id, r.rank, r.place_id, r.dist_m, r.adjoining) for r in s.itertuples()]


def test_next_door_vs_at_building_and_adjoining():
    # A and B share a wall (0.6 m gap); C stands 20 m from B
    r = roofs((0, 0, 20, 20), (20.6, 0, 40, 20), (60, 0, 80, 20))
    pl = places([p("a_in", 12, 10), p("b_in", 28, 10), p("yard_b", 48, 10), p("c_in", 72, 10)])
    out = assign_places(r, pl)
    assert rows(out, "at_building") == [(1, 1, "a_in", 0, False), (2, 1, "b_in", 0, False), (2, 2, "yard_b", 8, False), (3, 1, "c_in", 0, False)]
    # next door: distance to THIS roof; adjoining when the place's own building (B) is within 1 m of this roof
    assert rows(out, "next_door") == [(1, 1, "b_in", 8, True), (1, 2, "yard_b", 28, True), (2, 1, "a_in", 9, True), (3, 1, "yard_b", 12, False)]
    assert not out.loc[out.relation == "next_door", "inside"].any()
    assert out[["roof_id", "relation", "rank"]].values.tolist() == sorted(out[["roof_id", "relation", "rank"]].values.tolist())


def test_next_door_adjoining_first_rank_restart_and_cap():
    # R has one business of its own; N adjoins R (0.5 m) with five businesses 5-25 m away; M is 3 m off with one 4 m away
    r = roofs((0, 0, 20, 20), (20.5, 0, 60, 20), (0, 23, 20, 40))
    pl = places([p("own", 10, 10)] + [p(f"n{x}", x, 10) for x in (45, 25, 35, 30, 40)] + [p("m", 10, 24)])
    out = assign_places(r, pl)
    nd = out[(out.roof_id == 1) & (out.relation == "next_door")]
    assert nd["place_id"].tolist() == ["n25", "n30", "n35", "n40", "n45"] and nd["rank"].tolist() == [1, 2, 3, 4, 5]
    assert out[(out.roof_id == 1) & (out.relation == "at_building")]["rank"].tolist() == [1]
    six = assign_places(r, pl, max_per_roof=6)
    assert six[(six.roof_id == 1) & (six.relation == "next_door")]["place_id"].tolist()[-1] == "m"   # nearer, but not adjoining


def test_next_door_never_repeats_a_business_listed_at_the_building():
    r = roofs((0, 0, 20, 20), (20.5, 0, 40, 20))
    pl = places([p("big", 5, 5, name="Grande Magazzino", confidence=0.9), p("acme_r", 10, 10, name="Acme", confidence=0.5),
                 p("acme_n", 30, 10, name="ACME S.r.l.", confidence=0.8, src="BrightQuery"),
                 p("dom_r", 12, 12, name="Officina Rossi", website="https://rossi.it"),
                 p("dom_n", 32, 12, name="Rossi Group", website="www.rossi.it"),
                 p("bar_n1", 35, 15, name="Bar Luna", confidence=0.4, src="meta"),
                 p("bar_n2", 36, 15, name="BAR LUNA", confidence=0.7, src="Microsoft")])
    out = assign_places(r, pl, max_per_roof=1)   # Acme's own row on roof 1 is cut by the cap, it still blocks the repeat
    assert out[out.relation == "at_building"][["roof_id", "place_id"]].values.tolist() == [[1, "big"], [2, "acme_n"]]
    nd = out[out.relation == "next_door"]
    assert nd[["roof_id", "place_id"]].values.tolist() == [[1, "bar_n2"], [2, "big"]]   # Bar Luna once, at its highest confidence
    full = assign_places(r, pl)
    assert set(full[(full.roof_id == 1) & (full.relation == "next_door")]["place_id"]) == {"bar_n2"}
    assert set(full[(full.roof_id == 2) & (full.relation == "next_door")]["place_id"]) == {"big"}


def test_next_door_false_is_the_at_building_rows():
    r = roofs((0, 0, 20, 20), (20.5, 0, 40, 20), (60, 0, 80, 20))
    pl = places([p("a", 5, 5), p("b", 30, 10), p("y", 48, 10), p("c", 70, 10, name="Gamma", confidence=0.9)])
    full, only = assign_places(r, pl), assign_places(r, pl, next_door=False)
    assert (only["relation"] == "at_building").all() and not only["adjoining"].any()
    assert only.equals(full[full.relation == "at_building"].reset_index(drop=True))
    assert len(full) > len(only)


def test_name_key_and_domain():
    assert name_key("Rossi Mario & C. S.n.c.") == name_key("ROSSI MARIO") == "rossi mario"
    assert name_key("S.p.A.") == "spa" and name_key(None) == ""
    assert business_domain("https://www.Acme.it/x") == "acme.it" and business_domain("https://it-it.facebook.com/a") is None
    assert website("ftp://x.it") is None and website("acme.it") == "http://acme.it"


def addresses(rows):
    recs = [{"id": i, "street": s, "number": n, "postcode": pc, "postal_city": city, "src": "test"} for i, (s, n, pc, city, _, _) in enumerate(rows)]
    return gpd.GeoDataFrame(recs, geometry=[Point(x, y) for *_, x, y in rows], crs=CRS)


def test_nearest_address_completion_and_limits():
    r = roofs((0, 0, 20, 20), (100, 0, 120, 20), (300, 0, 320, 20), (500, 0, 520, 20))
    r["comune"] = ["Pavone del Mella", "MANERBIO", "LENO", "BAGNOLO MELLA"]
    ad = addresses([("VIA ROMA", "5", None, None, 30, 10),             # 10 m from roof 1, no postcode or city
                    ("VIA PO", "2", "25025", "Manerbio", 125, 10),     # complete
                    ("VIA LONTANA", "1", None, None, 400, 10),        # 80 m: too far
                    ("", None, None, None, 510, 10),                   # no street: not an address
                    ("VIA UNO", "1", None, None, 505, 5), ("VIA DUE", "2", None, None, 506, 6)])  # tie inside roof 4
    out = nearest_address(r, ad).set_index("roof_id")
    assert list(out.reset_index().columns) == ["roof_id", "address", "address_dist_m"]
    assert out.loc[1, "address"] == "VIA ROMA 5, Pavone del Mella" and out.loc[1, "address_dist_m"] == 10  # name kept as written
    assert out.loc[2, "address"] == "VIA PO 2, 25025 Manerbio" and out.loc[2, "address_dist_m"] == 5
    assert 3 not in out.index
    assert out.loc[4, "address"] == "VIA UNO 1, Bagnolo Mella" and out.loc[4, "address_dist_m"] == 0
    assert nearest_address(r[["roof_id", "geometry"]], ad).set_index("roof_id").loc[1, "address"] == "VIA ROMA 5"  # no comune given


def worksheet(rows):
    """rows: (roof_id, parcel_ref, {manual col: value}) -> worksheet-like frame (synthetic)."""
    recs = [{"roof_id": rid, "parcel_ref": ref, "area_m2": 1000.0, **{c: "" for c in MANUAL}, **m} for rid, ref, m in rows]
    return pd.DataFrame(recs)


def csv_text(df):
    """Write and read back the way the script does: everything as text."""
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return pd.read_csv(io.StringIO(buf.getvalue()), dtype=str, keep_default_na=False)


def test_merge_manual_carries_over_and_round_trips_text():
    old = csv_text(worksheet([(1, "A_000100.1", {"notes": "rang, call back", "do_not_contact": "1", "contact_date": "2026-11-14"}),
                              (2, None, {"owner_type": "company", "outcome": "NA"}),
                              (3, "A_000100.3", {})]))
    new = worksheet([(1, "A_000100.1", {}), (2, None, {}), (3, "A_000100.3", {}), (4, "A_000100.4", {})]).astype({"roof_id": int})
    merged, orphans = merge_manual(new, old, MANUAL)
    assert orphans.empty
    m = merged.set_index("roof_id")
    assert m.loc[1, "notes"] == "rang, call back" and m.loc[1, "do_not_contact"] == "1" and m.loc[1, "contact_date"] == "2026-11-14"
    assert m.loc[2, "owner_type"] == "company" and m.loc[2, "outcome"] == "NA"   # guard missing on both sides; 'NA' stays text
    assert (m.loc[[3, 4], MANUAL] == "").all().all()
    assert merged["area_m2"].tolist() == new["area_m2"].tolist()                  # generated columns untouched
    back = csv_text(merged)
    assert back.loc[back.roof_id == "1", "do_not_contact"].item() == "1" and "nan" not in back[MANUAL].values
    again, orphans2 = merge_manual(new, back, MANUAL)                              # a second run changes nothing
    assert again[MANUAL].equals(merged[MANUAL]) and orphans2.empty


def test_merge_manual_orphans_never_lost():
    old = csv_text(worksheet([(1, "A_000100.1", {"notes": "parcel moved"}), (2, "A_000100.2", {"do_not_contact": "yes"}),
                              (3, "A_000100.3", {}), (5, None, {"notes": "first"}), (5, None, {"notes": "second"})]))
    new = worksheet([(1, "B_000200.9", {}), (3, "A_000100.3", {}), (5, None, {})])
    merged, orphans = merge_manual(new, old, MANUAL)
    assert (merged.set_index("roof_id").loc[[1, 3], MANUAL] == "").all().all()   # mismatched parcel: nothing carried
    assert merged.set_index("roof_id").loc[5, "notes"] == "first"
    reasons = dict(zip(orphans["notes"].where(orphans["notes"] != "", orphans["do_not_contact"]), orphans["orphan_reason"]))
    assert reasons == {"parcel moved": "parcel_ref changed", "yes": "roof no longer a candidate", "second": "roof_id repeated in old worksheet"}
    assert set(orphans.columns) == set(old.columns) | {"orphan_reason"} and orphans["parcel_ref"].tolist()[0] == "A_000100.1"


def test_merge_manual_without_old_and_numeric_old():
    new = worksheet([(1, "A_000100.1", {})])
    merged, orphans = merge_manual(new, None, MANUAL)
    assert (merged[MANUAL] == "").all().all() and orphans.empty
    old = pd.DataFrame({"roof_id": [1.0], "parcel_ref": ["A_000100.1"], "do_not_contact": [1.0], "notes": [float("nan")]})  # read without dtype=str
    merged, _ = merge_manual(new, old, MANUAL)
    assert merged.loc[0, "do_not_contact"] == "1" and merged.loc[0, "notes"] == ""


def test_merge_manual_rejects_duplicate_new_keys():
    with pytest.raises(ValueError):
        merge_manual(worksheet([(1, None, {}), (1, None, {})]), worksheet([(1, None, {"notes": "x"})]), MANUAL)


# --- round 3: review fixes -------------------------------------------------------------------------------------------

def test_website_is_the_public_filter():  # B1: a WhatsApp link is a phone number, user info is an e-mail address
    assert website("https://wa.me/393331234567") is None and website("https://api.whatsapp.com/send?phone=39333") is None
    assert website("http://info@acme.example/") is None
    assert website("https://shop.example/?mail=info@shop.example&tel=0301234567") == "https://shop.example/"
    assert website("https://shop.example/catalogo?utm_source=x#top") == "https://shop.example/catalogo"


def test_assign_places_never_outputs_contact_urls():  # B1, end to end through the occupants table
    r = roofs((0, 0, 20, 20), (20.5, 0, 40, 20))
    pl = places([p("wa", 5, 5, website="https://wa.me/393331234567"), p("mail", 6, 6, website="http://info@acme.example/"),
                 p("q", 7, 7, website="https://shop.example/?mail=info@shop.example&tel=0301234567"),
                 p("tg", 30, 10, website="https://t.me/officina_rossi")])
    out = assign_places(r, pl).set_index(["roof_id", "relation", "place_id"])["website"]
    q = [(1, "at_building", "q"), (2, "next_door", "q")]
    assert out.loc[q].tolist() == ["https://shop.example/"] * 2
    assert out.drop(q).isna().all() and len(out) == 8   # at the building and next door


def test_merge_manual_old_without_key_column():  # S3: was KeyError: 'roof_id'
    merged, orphans = merge_manual(worksheet([(1, "A_000100.1", {})]), pd.DataFrame({"notes": ["typed"]}), MANUAL)
    assert (merged[MANUAL] == "").all().all()
    assert orphans[["notes", "orphan_reason"]].values.tolist() == [["typed", "no roof_id"]]


def write(path, df, sep=",", encoding="utf-8"):
    path.write_bytes(df.to_csv(index=False, sep=sep).encode(encoding))
    return path


def test_read_table_any_separator_and_encoding(tmp_path):  # S3: Excel in an Italian locale saves ';' in Windows-1252
    df = pd.DataFrame({"roof_id": ["7"], "notes": ["Società; chiamato, richiamare"], "do_not_contact": ["1"], "outcome": ["NA"]})
    for sep, enc in ((",", "utf-8"), (";", "cp1252"), ("\t", "utf-8-sig"), (";", "utf-8-sig")):
        got = occ.read_table(write(tmp_path / "w.csv", df, sep, enc))
        assert got.equals(df), (sep, enc)
    assert occ.read_table(tmp_path / "missing.csv") is None
    assert occ.read_table(write(tmp_path / "empty.csv", pd.DataFrame())) is None


def test_read_worksheet_without_roof_id_stops_with_a_message(tmp_path):  # S3
    bad = tmp_path / "contact_worksheet.csv"
    bad.write_text("priority|roof_id|notes\n1|7|x\n")  # a separator we do not read: one column, no roof_id
    with pytest.raises(ValueError, match="no 'roof_id' column"):
        occ.read_worksheet(bad)
    assert occ.read_worksheet(write(tmp_path / "ok.csv", worksheet([(7, "A_000100.7", {"notes": "x"})]), ";"))["notes"].tolist() == ["x"]


def save(tmp_path, new, **kw):
    kw.setdefault("now", datetime(2026, 11, 14, 9, 30, 0))
    return occ.save_worksheet(new, tmp_path / "contact_worksheet.csv", tmp_path / "contact_worksheet_orphans.csv", **kw)


def test_save_worksheet_carries_backs_up_and_appends_orphans(tmp_path):  # item 5: the script's carry-over, unit tested
    ws, orph = tmp_path / "contact_worksheet.csv", tmp_path / "contact_worksheet_orphans.csv"
    info = save(tmp_path, worksheet([(1, "A_000100.1", {}), (2, "A_000100.2", {})]))  # first run: nothing to keep
    assert info == {"backup": None, "carried": 0, "orphans": {}, "objection_rows": 0, "objections_unreached": []}
    assert not orph.exists() and len(list(tmp_path.glob("*.backup-*"))) == 0
    old = worksheet([(1, "A_000100.1", {"notes": "rang, call back", "outcome": "NA", "contact_date": "007"}),
                     (2, "A_000100.2", {"owner_type": "company"}), (3, "A_000100.3", {"notes": "gone"})])
    write(ws, old, ";", "cp1252")  # re-saved by a spreadsheet
    raw = ws.read_bytes()
    new = worksheet([(1, "A_000100.1", {}), (2, "A_000100.2", {})])
    info = save(tmp_path, new)
    assert info["backup"].name == "contact_worksheet.backup-20261114-093000.csv" and info["backup"].read_bytes() == raw
    assert info["carried"] == 2 and info["orphans"] == {"roof no longer a candidate": 1}
    got = occ.read_table(ws).set_index("roof_id")
    assert got.loc["1", ["notes", "outcome", "contact_date"]].tolist() == ["rang, call back", "NA", "007"]
    assert got.loc["2", "owner_type"] == "company" and "3" not in got.index
    o = occ.read_table(orph)
    assert o[["orphaned_at", "roof_id", "notes", "orphan_reason"]].values.tolist() == [
        ["2026-11-14 09:30:00", "3", "gone", "roof no longer a candidate"]]
    ws.write_bytes(raw)  # a re-run after a crash before the worksheet was written: same orphan, not appended twice
    info = save(tmp_path, new)
    assert info["backup"].name == "contact_worksheet.backup-20261114-093000-2.csv"   # same second: no backup overwritten
    assert len(occ.read_table(orph)) == 1


def test_save_worksheet_stops_before_writing_when_roof_id_is_missing(tmp_path):  # S3
    ws = tmp_path / "contact_worksheet.csv"
    ws.write_text("notes\ntyped by hand\n")
    with pytest.raises(ValueError, match="no 'roof_id' column"):
        save(tmp_path, worksheet([(1, "A_000100.1", {})]))
    assert ws.read_text() == "notes\ntyped by hand\n" and sorted(f.name for f in tmp_path.iterdir()) == ["contact_worksheet.csv"]


NOTE = "do_not_contact carried from roof {} on the same parcel (see contact_worksheet_orphans.csv)"
ROOF_NOTE = "do_not_contact carried from an orphaned row of roof {} (see contact_worksheet_orphans.csv)"
SPREAD_NOTE = "do_not_contact carried from roof {} on the same parcel"


def test_objection_follows_the_parcel_when_its_row_is_orphaned(tmp_path):  # S1, review E3a
    ws = tmp_path / "contact_worksheet.csv"
    write(ws, worksheet([(1, "P_000100.1", {"do_not_contact": "yes"}), (2, "P_000100.1", {"notes": "letter sent"}),
                         (3, "R_000100.3", {})]))
    new = worksheet([(1, "Q_000100.9", {}), (2, "P_000100.1", {}), (3, "R_000100.3", {})])  # roof 1 now on another parcel
    info = save(tmp_path, new)
    got = occ.read_table(ws).set_index("roof_id")
    assert got.loc["2", "do_not_contact"] == "yes" and got.loc["2", "notes"] == "letter sent; " + NOTE.format(1)
    assert got.loc["3", "do_not_contact"] == ""                                       # another parcel, no objection there
    assert got.loc["1", "do_not_contact"] == "yes"   # round 4: the objecting roof keeps its own mark on its new parcel
    assert info["objection_rows"] == 2 and info["objections_unreached"] == []
    assert occ.read_table(tmp_path / "contact_worksheet_orphans.csv")["orphan_reason"].tolist() == ["parcel_ref changed"]
    first = ws.read_bytes()
    info = save(tmp_path, new)                                                         # rerun: nothing added twice
    assert ws.read_bytes() == first and info["objection_rows"] == 0
    assert len(occ.read_table(tmp_path / "contact_worksheet_orphans.csv")) == 1


def test_objection_survives_its_roof_dropping_out_and_comes_back_later(tmp_path):  # S1, review E3b
    ws = tmp_path / "contact_worksheet.csv"
    write(ws, worksheet([(1, "P_000100.1", {"do_not_contact": "1", "notes": "said no"}), (2, "P_000100.1", {})]))
    save(tmp_path, worksheet([(2, "P_000100.1", {})]))                                 # roof 1 is no longer a candidate
    assert occ.read_table(ws).set_index("roof_id").loc["2", ["do_not_contact", "notes"]].tolist() == ["1", NOTE.format(1)]
    # a later run where another roof of the same property becomes a candidate: the objection reaches it from the orphans file
    info = save(tmp_path, worksheet([(2, "P_000100.1", {}), (5, "P_000100.1", {}), (6, "S_000100.6", {})]))
    got = occ.read_table(ws).set_index("roof_id")
    assert got.loc["5", ["do_not_contact", "notes"]].tolist() == ["1", NOTE.format(1)] and got.loc["6", "do_not_contact"] == ""
    assert info["objections_unreached"] == []


def test_objection_with_no_row_on_its_parcel_is_reported(tmp_path):  # S1: today's warning when nothing shares the parcel
    write(tmp_path / "contact_worksheet.csv", worksheet([(1, "P_000100.1", {"do_not_contact": "yes"})]))
    info = save(tmp_path, worksheet([(2, "S_000100.2", {})]))
    assert info["objections_unreached"] == ["1"] and info["objection_rows"] == 0
    assert occ.read_table(tmp_path / "contact_worksheet_orphans.csv")["do_not_contact"].tolist() == ["yes"]


def test_objection_on_a_repeated_row_is_not_lost(tmp_path):  # S1 + review S2: a roof typed in twice, no parcel on either side
    write(tmp_path / "contact_worksheet.csv", worksheet([(1, None, {"notes": "first contact"}), (1, None, {"do_not_contact": "yes"})]))
    info = save(tmp_path, worksheet([(1, None, {})]))
    got = occ.read_table(tmp_path / "contact_worksheet.csv").iloc[0]
    assert got["do_not_contact"] == "yes" and got["notes"] == "first contact; " + ROOF_NOTE.format(1)
    assert info["orphans"] == {"roof_id repeated in old worksheet": 1}


def test_generated_columns_are_not_user_columns(tmp_path):  # minor 9
    ws, gen = tmp_path / "contact_worksheet.csv", (*occ.WORKSHEET_GENERATED, "retired_col")
    old = worksheet([(1, "A_000100.1", {}), (2, "A_000100.2", {})]).assign(retired_col=["stale", "stale"])
    write(ws, old.assign(**{"Unnamed: 12": ""}))
    info = save(tmp_path, worksheet([(1, "A_000100.1", {})]), generated_cols=gen)  # roof 2 drops out
    assert info["backup"] is None and info["orphans"] == {}                          # nothing typed: no backup, no orphan
    assert "retired_col" not in occ.read_table(ws).columns and "Unnamed: 12" not in occ.read_table(ws).columns
    write(ws, old.assign(my_col=["", "mine"]))
    info = save(tmp_path, worksheet([(1, "A_000100.1", {}), (2, "A_000100.2", {})]), generated_cols=gen)
    got = occ.read_table(ws)
    assert got.columns[-1] == "my_col" and got["my_col"].tolist() == ["", "mine"] and "retired_col" not in got.columns
    with pytest.raises(ValueError, match="WORKSHEET_GENERATED"):                    # a new generated column must be listed
        save(tmp_path, worksheet([(1, "A_000100.1", {})]).assign(brand_new=1))


def test_contact_details_in_listing_text_are_not_stored():  # privacy rule: a phone number typed into a business name
    r = roofs((0, 0, 20, 20))
    pl = places([p("a", 5, 5, name="Autofficina Rossi Tel 333 1234567"), p("b", 6, 6, name="Bar Sport - cell. +39 333 123 4567"),
                 p("c", 7, 7, name="Gelateria Luna info@luna.example", address="Via Po 3 info@luna.example"),
                 p("d", 8, 8, name="Pane@Vino 2000", address="SP11 11-25022", postcode="25022", locality="Borgo")])
    out = assign_places(r, pl).set_index("place_id")
    assert out["name"].to_dict() == {"a": "Autofficina Rossi", "b": "Bar Sport", "c": "Gelateria Luna", "d": "Pane@Vino 2000"}
    assert out.loc["c", "address"] == "Via Po 3" and out.loc["d", "address"] == "SP11 11-25022, 25022 Borgo"  # road number kept


def test_earlier_orphans_reach_their_roof_and_parcel_like_the_map(tmp_path):  # same rule as roofwatch.panel: roof id and parcel
    occ.write_csv(pd.DataFrame({"orphaned_at": ["2026-01-01 10:00:00"], "roof_id": ["7.0"], "parcel_ref": ["P_000100.1"],
                                "do_not_contact": ["yes"], "notes": [""], "orphan_reason": ["roof no longer a candidate"]}),
                  tmp_path / "contact_worksheet_orphans.csv")
    save(tmp_path, worksheet([(7, "Q_000100.9", {}), (8, "Q_000100.9", {}), (9, "P_000100.1", {}), (10, "S_000100.5", {})]))
    got = occ.read_table(tmp_path / "contact_worksheet.csv").set_index("roof_id")
    assert got["do_not_contact"].to_dict() == {"7": "yes", "8": "yes", "9": "yes", "10": ""}
    assert got.loc["7", "notes"] == ROOF_NOTE.format(7) and got.loc["9", "notes"] == NOTE.format(7)
    assert got.loc["8", "notes"] == SPREAD_NOTE.format(7)                              # roof 7's new parcel


# --- round 4: second review ------------------------------------------------------------------------------------------

def test_orphans_file_without_its_header_stops_the_run(tmp_path):  # should-fix 1 (review2 e11)
    ws, orph = tmp_path / "contact_worksheet.csv", tmp_path / "contact_worksheet_orphans.csv"
    write(ws, worksheet([(5, "S_000100.5", {"do_not_contact": "yes"})]))
    orph.write_text("2026-01-01 10:00:00,7,P_000100.1,yes,,roof no longer a candidate\n")    # header row deleted by hand
    files = {f.name: f.read_bytes() for f in tmp_path.iterdir()}
    with pytest.raises(ValueError, match="has no 'roof_id', 'parcel_ref', 'do_not_contact' columns"):
        save(tmp_path, worksheet([(1, "P_000100.1", {})]))
    assert {f.name: f.read_bytes() for f in tmp_path.iterdir()} == files                  # nothing written
    orph.write_text("roof_id,notes\n7,x\n")
    with pytest.raises(ValueError, match="has no 'parcel_ref', 'do_not_contact' columns"):
        occ.read_orphans(orph)
    orph.write_text("")
    assert occ.read_orphans(orph) is None and occ.read_orphans(tmp_path / "missing.csv") is None


def test_every_objection_spreads_over_its_parcel(tmp_path):  # should-fix 2a: not only orphaned ones
    ws = tmp_path / "contact_worksheet.csv"
    write(ws, worksheet([(1, "P_000100.1", {"notes": "rang"}), (2, "P_000100.1", {"do_not_contact": "yes"}),
                         (3, "P_000100.1", {"do_not_contact": "1"}), (4, "R_000100.4", {})]))
    new = worksheet([(1, "P_000100.1", {}), (2, "P_000100.1", {}), (3, "P_000100.1", {}), (4, "R_000100.4", {})])
    info = save(tmp_path, new)
    got = occ.read_table(ws).set_index("roof_id")
    assert got["do_not_contact"].to_dict() == {"1": "yes", "2": "yes", "3": "1", "4": ""}
    assert got["notes"].to_dict() == {"1": "rang; " + SPREAD_NOTE.format(2), "2": "", "3": "", "4": ""}  # own objection: no note
    assert info["objection_rows"] == 1
    first = ws.read_bytes()
    assert save(tmp_path, new)["objection_rows"] == 0 and ws.read_bytes() == first      # idempotent


def test_parcel_change_keeps_the_roofs_own_objection(tmp_path):  # should-fix 2b, then 2a over the roof's new parcel
    ws = tmp_path / "contact_worksheet.csv"
    write(ws, worksheet([(1, "P_000100.1", {"do_not_contact": "yes"}), (2, "P_000100.1", {})]))
    info = save(tmp_path, worksheet([(1, "Q_000100.9", {}), (2, "P_000100.1", {}), (3, "Q_000100.9", {})]))
    got = occ.read_table(ws).set_index("roof_id")
    assert got["do_not_contact"].to_dict() == {"1": "yes", "2": "yes", "3": "yes"}
    assert got["notes"].to_dict() == {"1": ROOF_NOTE.format(1), "2": NOTE.format(1), "3": SPREAD_NOTE.format(1)}
    assert info["objections_unreached"] == [] and info["orphans"] == {"parcel_ref changed": 1}


def test_no_zero_false_are_not_objections(tmp_path):  # minor 1: one definition, occupants.is_objection
    ws = tmp_path / "contact_worksheet.csv"
    write(ws, worksheet([(1, "P_000100.1", {"do_not_contact": "no"}), (2, "P_000100.1", {"do_not_contact": "FALSE"}),
                         (3, "P_000100.1", {"do_not_contact": "0"}), (4, "P_000100.1", {"notes": "rang"})]))
    info = save(tmp_path, worksheet([(1, "Q_000100.9", {}), (2, "P_000100.1", {}), (3, "P_000100.1", {}), (4, "P_000100.1", {}),
                                     (5, "Q_000100.9", {})]))
    got = occ.read_table(ws).set_index("roof_id")
    assert got["do_not_contact"].to_dict() == {"1": "", "2": "FALSE", "3": "0", "4": "", "5": ""}  # typed values kept as typed
    assert not got["notes"].str.contains("carried").any()
    assert info["objection_rows"] == 0 and info["objections_unreached"] == []           # the orphaned 'no' is no objection
    assert occ.read_table(tmp_path / "contact_worksheet_orphans.csv")["do_not_contact"].tolist() == ["no"]


@pytest.mark.parametrize("text, want", [
    ("Officina Rossi (030) 1234567", "Officina Rossi"), ("Bar Luna 030 / 1234567", "Bar Luna"),
    ("Autofficina +39 (0)30 1234567", "Autofficina"), ("Rossi Impianti info [at] rossi.it", "Rossi Impianti"),
    ("Pizzeria Tel3331234567", "Pizzeria"), ("Bar 2000 030 1234567", "Bar 2000"), ("Ditta (e-mail: a.b@c.example)", "Ditta"),
    ("Rossi Srl P.IVA 01234567890", "Rossi Srl"), ("Tel/Fax 030.1234567 Ditta X", "Ditta X"),
    ("Pane@Vino 2000", "Pane@Vino 2000"), ("WA2000", "WA2000"), ("Officina 3 Erre", "Officina 3 Erre")])
def test_scrub_contact_is_as_strong_as_the_panel(text, want):  # minor 3: the panel's cases, safe for names
    assert occ.scrub_contact(text) == want


def test_scrub_contact_keeps_addresses_figures():  # the phone part is never applied to addresses
    for addr in ("SP11 11-25022", "Via Roma 1234 25010", "Via Industria 10/12/14/16/18/20"):
        assert occ.scrub_contact(addr, phones=False) == addr
    assert occ.scrub_contact("Via Po 3 info [at] po.example", phones=False) == "Via Po 3"


# --- round 5: third review ---------------------------------------------------------------------------------------------

def test_drop_suppressed_domain_subdomains_and_same_business():  # should-fix 1, source side
    r = roofs((0, 0, 20, 20), (20.5, 0, 40, 20))
    pl = places([p("own", 5, 5, name="Rossi Mario Idraulica", website="https://www.rossimario.example/contatti"),
                 p("sub", 6, 6, name="Rossi Mario Shop", website="http://shop.rossimario.example"),
                 p("same", 30, 10, name="ROSSI MARIO IDRAULICA", src="BrightQuery"),           # same business, no website
                 p("other", 7, 7, name="Officina Bianchi", website="https://notrossimario.example"),  # not a subdomain
                 p("bar", 32, 12, name="Bar Luna")])
    kept = occ.drop_suppressed(pl, frozenset({"rossimario.example"}))
    assert sorted(kept["id"]) == ["bar", "other"]
    out = assign_places(r, kept)
    assert set(out["place_id"]) == {"bar", "other"} and not out["name"].str.contains("Rossi Mario", case=False).any()
    assert occ.drop_suppressed(pl, frozenset()).equals(pl)


def test_load_suppressed_domains(tmp_path):  # both files optional; the register covers suppression by VAT number
    sup, reg = tmp_path / "company_suppress.csv", tmp_path / "company_register.csv"
    assert occ.load_suppressed_domains(sup, reg) == frozenset()
    sup.write_text("Domain ;vat\nwww.RossiMario.example;\n;IT01234567890\n")
    reg.write_text("domain,status,legal_name\nhop.example,suppressed,\nfine.example,ok,FINE SRL\n")
    assert occ.load_suppressed_domains(sup, reg) == {"rossimario.example", "hop.example"}
    sup.write_text("")
    assert occ.load_suppressed_domains(sup, tmp_path / "missing.csv") == frozenset()
    sup.write_text("name\nRossi\n")
    with pytest.raises(ValueError, match="needs a 'domain' and/or 'vat' column"):
        occ.load_suppressed_domains(sup, reg)


@pytest.mark.parametrize("value, want", [("12", 12), (" 012 ", 12), ("01", 1), ("12.0", 12), (12, 12), (12.0, 12), ("2.6", None),
                                         ("١", None), ("１２", None), ("12a", None), ("1e3", None), ("-1", None), ("", None), (None, None)])
def test_parse_roof_id(value, want):  # minor 3: one parser, ASCII digits only
    assert occ.parse_roof_id(value) == want


def test_orphan_roof_id_forms_match_like_the_map(tmp_path):  # minor 3 (a): '01' is roof 1; a non-ASCII digit is no roof id
    occ.write_csv(pd.DataFrame({"roof_id": ["01", "١"], "parcel_ref": ["", ""], "do_not_contact": ["yes", "yes"], "notes": ["", ""],
                                "orphan_reason": ["x", "x"]}), tmp_path / "contact_worksheet_orphans.csv")
    info = save(tmp_path, worksheet([(1, "Q_000100.9", {}), (2, "R_000100.2", {})]))
    assert occ.read_table(tmp_path / "contact_worksheet.csv")["do_not_contact"].tolist() == ["yes", ""]
    assert info["objections_unreached"] == ["١"]


def test_header_names_are_trimmed(tmp_path):  # minor 3 (b): 'do_not_contact ' typed with a trailing blank
    ws = tmp_path / "contact_worksheet.csv"
    write(ws, worksheet([(1, "P_000100.1", {"do_not_contact": "yes"}), (2, "P_000100.1", {})]).rename(columns={"do_not_contact": "do_not_contact "}))
    save(tmp_path, worksheet([(1, "P_000100.1", {}), (2, "P_000100.1", {})]))
    got = occ.read_table(ws)
    assert got["do_not_contact"].tolist() == ["yes", "yes"] and "do_not_contact " not in got.columns


def test_blank_orphans_file_means_no_orphans(tmp_path):  # minor 3 (c): decided: empty is "no orphans", not an error
    orph = tmp_path / "contact_worksheet_orphans.csv"
    for content in (b"", b"\n  \n", b"\xef\xbb\xbf"):
        orph.write_bytes(content)
        assert occ.read_orphans(orph) is None
        assert save(tmp_path, worksheet([(1, "P_000100.1", {})]))["objections_unreached"] == []


def test_unreached_objections_are_reported_on_every_run(tmp_path):  # minor 7: earlier orphans too
    occ.write_csv(pd.DataFrame({"orphaned_at": ["2026-01-01 10:00:00"], "roof_id": ["9"], "parcel_ref": ["S_000100.9"],
                                "do_not_contact": ["yes"], "notes": [""], "orphan_reason": ["roof no longer a candidate"]}),
                  tmp_path / "contact_worksheet_orphans.csv")
    for _ in range(2):  # no orphan is created by these runs
        info = save(tmp_path, worksheet([(1, "P_000100.1", {})]))
        assert info["objections_unreached"] == ["9"] and info["orphans"] == {}


def test_drop_suppressed_compares_names_after_the_contact_scrub():  # review4 minor 4
    pl = places([p("site", 5, 5, name="Rossi Mario Idraulica", website="https://rossimario.example"),
                 p("tel", 6, 6, name="Rossi Mario Idraulica Tel. 333 1234567"), p("bar", 7, 7, name="Bar Luna")])
    assert occ.drop_suppressed(pl, frozenset({"rossimario.example"}))["id"].tolist() == ["bar"]


# --- merge with f7ead55: company on site (AIA permit holders, OSM named features, same parcel), visura queue ----------

def test_known_company_precedence_and_source():
    df = pd.DataFrame({"business_name": ["Acme Srl", None, "", None, None, None],
                       "aia_company": ["Permit Spa", "Permit Spa", None, None, None, None],
                       "osm_operator": ["Op", "Op", "Op Coop", "", None, None],
                       "osm_name": ["Cascina", "Cascina", "Cascina", "Cascina Bassa", None, None],
                       "parcel_company": ["P", None, None, None, "Bar Uno", None],
                       "parcel_company_source": ["x", None, None, None, "Overture places (same parcel)", None]})
    company, source = occ.known_company(df)
    assert [c if isinstance(c, str) else None for c in company] == ["Acme Srl", "Permit Spa", "Op Coop", "Cascina Bassa", "Bar Uno", None]
    assert source.tolist() == ["Overture places", "AIA permit", "OSM operator", "OSM name", "Overture places (same parcel)", ""]


def test_parcel_companies_same_property_first_source_wins():
    parcels = gpd.GeoDataFrame({"ref": ["P1", "P2", "P3"]}, geometry=[box(0, 0, 100, 100), box(200, 0, 300, 100), box(400, 0, 500, 100)],
                               crs=CRS)
    over = gpd.GeoDataFrame({"name": ["Bar Uno Tel 333 1234567"]}, geometry=[Point(50, 50)], crs=CRS).to_crs(4326)  # other CRS
    aia = gpd.GeoDataFrame({"name": ["Allevamento Bianchi", "Ditta Due", "Fuori"]}, geometry=[Point(60, 60), Point(250, 50), Point(450, 50)],
                           crs=CRS)
    out = occ.parcel_companies([("Overture places (same parcel)", over), ("AIA permit (same parcel)", aia)], parcels, ["P1", "P2"])
    assert out.values.tolist() == [["P1", "Bar Uno", "Overture places (same parcel)"], ["P2", "Ditta Due", "AIA permit (same parcel)"]]


def test_prepare_aia_and_osm_scrub_names_and_filter_websites():
    aia = occ.prepare_aia(gpd.GeoDataFrame({"RAGIONE_SO": ["Azienda Agricola Rossi Mario tel. 030 1234567", "  "], "TIPOLOGIA_": ["ALL", "IND"],
                                            "INDIRIZZO": ["VIA ROMA", "VIA PO"], "NUM_CIVICO": ["5", None], "CAP": ["25025", None],
                                            "COMUNE": ["MANERBIO", "LENO"]}, geometry=[Point(0, 0), Point(1, 1)], crs=CRS))
    assert aia.drop(columns="geometry").values.tolist() == [["Azienda Agricola Rossi Mario", "livestock farm", "Via Roma 5, 25025 Manerbio"]]
    osm = occ.prepare_osm(gpd.GeoDataFrame({"osm_name": ["Cascina Alta", "Officina", None], "osm_operator": [None, "Rossi info@rossi.example", None],
                                            "osm_kind": ["farmyard", "industrial", "farmyard"],
                                            "osm_website": ["https://wa.me/393331234567", "http://rossi.example/?tel=0301234567", "x.example"]},
                                           geometry=[Point(0, 0), Point(1, 1), Point(2, 2)], crs=CRS))
    assert [[v if isinstance(v, str) else None for v in row] for row in osm.drop(columns="geometry").values.tolist()] == [
        ["Cascina Alta", None, "farmyard", None],
        ["Officina", "Rossi", "industrial", "http://rossi.example/"]]


def test_suppression_reaches_permit_holders_and_osm_sites():  # a farm run by one person: same rule as a listing
    dom = frozenset({"rossimario.example"})
    pl = places([p("site", 5, 5, name="Rossi Mario Idraulica", website="https://rossimario.example"), p("bar", 6, 6, name="Bar Luna"),
                 p("cascina", 7, 7, name="Cascina Verdi")])
    osm = occ.prepare_osm(gpd.GeoDataFrame({"osm_name": ["Cascina Verdi", "Cascina Gialla"], "osm_operator": [None, None],
                                            "osm_kind": ["farmyard", "farmyard"],
                                            "osm_website": ["https://shop.rossimario.example", "https://gialla.example"]},
                                           geometry=[Point(0, 0), Point(1, 1)], crs=CRS))
    aia = occ.prepare_aia(gpd.GeoDataFrame({"RAGIONE_SO": ["ROSSI MARIO IDRAULICA", "Allevamento Neri"], "TIPOLOGIA_": ["IND", "ALL"]},
                                           geometry=[Point(0, 0), Point(1, 1)], crs=CRS))
    names = occ.suppressed_names(pl, dom) | occ.suppressed_names(osm, dom, "osm_website", ("osm_name", "osm_operator"))
    assert sorted(occ.drop_suppressed(pl, dom, names=names)["id"]) == ["bar"]                      # 'Cascina Verdi' too: its OSM site is suppressed
    assert occ.drop_suppressed(osm, dom, "osm_website", ("osm_name", "osm_operator"), names=names)["osm_name"].tolist() == ["Cascina Gialla"]
    assert occ.drop_suppressed(aia, dom, name_col="aia_company", names=names)["aia_company"].tolist() == ["Allevamento Neri"]
    assert occ.drop_suppressed(aia, frozenset(), name_col="aia_company") is aia                    # nothing suppressed: unchanged


def test_nearest_point_within_distance_ties_to_first():
    r = roofs((0, 0, 20, 20), (500, 0, 520, 20))
    pts = gpd.GeoDataFrame({"aia_company": ["Prima", "Seconda", "Lontana"]}, geometry=[Point(30, 10), Point(10, 30), Point(900, 0)], crs=CRS)
    assert occ.nearest_point(r, pts, 200, "aia_dist_m").values.tolist() == [[1, "Prima", 10.0]]


def queue_sheet(rows):
    """(priority, roof_id, parcel_ref, known_company, owner_from_visura, do_not_contact) -> worksheet as read back (text)."""
    return pd.DataFrame([{"priority": str(pr), "roof_id": str(rid), "comune": "Leno", "area_m2": str(1000 - pr), "model_score": "0.8",
                          "comune_code": "E526", "foglio": "1", "particella": "1", "parcel_ref": ref, "address": "", "street_view": "sv",
                          "known_company": kc, "owner_from_visura": ow, "do_not_contact": dnc} for pr, rid, ref, kc, ow, dnc in rows])


def test_visura_queue_one_row_per_open_parcel():
    ws = queue_sheet([(1, 11, "A", "", "", ""), (2, 12, "B", "Acme", "", ""), (3, 13, "A", "", "", ""), (4, 14, "B", "", "", ""),
                      (5, 15, "C", "", "Rossi Mario", ""), (6, 16, "D", "", "", ""), (7, 17, "D", "", "", "yes"),
                      (8, 18, "E", "", "", "no"), (9, 19, "", "", "", ""), (10, 20, "F", "", "", "")])
    q = occ.visura_queue(ws)
    assert q["roof_id"].tolist() == ["11", "18", "20"]               # B: company found; C: owner typed; D: objection; '' : no parcel
    assert list(q.columns) == occ.QUEUE_COLS and "owner_from_visura" not in q
    assert occ.visura_queue(ws, n=2)["parcel_ref"].tolist() == ["A", "E"]


def test_save_visura_queue_never_overwrites_typed_values_silently(tmp_path):
    path = tmp_path / "visura_queue.csv"
    path.write_text("priority;parcel_ref;owner_from_visura;registered_office\n1;A;Rossi Mario;\n")   # an older queue, typed into
    raw = path.read_bytes()
    q = occ.visura_queue(queue_sheet([(1, 11, "A", "", "", "")]))
    backup = occ.save_visura_queue(q, path, now=datetime(2026, 11, 14, 9, 30, 0))
    assert backup.name == "visura_queue.backup-20261114-093000.csv" and backup.read_bytes() == raw
    assert occ.read_table(path).columns.tolist() == occ.QUEUE_COLS
    assert occ.save_visura_queue(q, path) is None and len(list(tmp_path.glob("*.backup-*"))) == 1  # nothing typed: no backup
