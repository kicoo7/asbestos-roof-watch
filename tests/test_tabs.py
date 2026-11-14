import json
import re
from datetime import date
from pathlib import Path

from roofwatch.grants import load_grants
from roofwatch.tabs import grants_tab, tabs_html

DATA = Path(__file__).resolve().parents[1] / "data/grants_lombardia.json"
TODAY = date(2026, 11, 14)


def _r(rid, comune, area, group, otype, stage="to identify", confirmed=None, dnc=False, p=0.9, p_pv=0.05, company=""):
    return {"roof_id": rid, "comune": comune, "area_m2": area, "group": group, "owner_type": otype, "why": "evidence",
            "confirmed_type": confirmed, "stage": "do not contact" if dnc else stage, "dnc": dnc, "p": p, "p_pv": p_pv,
            "company": company, "parcel_ref": f"L777_0015{rid:02d}.1", "address": ""}


def _roofs():
    return [
        _r(1, "Verolanuova", 9000, "agree", "farm", stage="interested"),
        _r(2, "Manerbio", 4000, "suspected_new", "company", stage="contacted"),
        _r(3, "Leno <b>x</b></script>", 250, "agree", "home"),
        _r(4, "Offlaga", 20000, "agree", "farm", dnc=True),
        _r(5, "Pompiano", 3000, "mapped_not_seen", "farm"),
        _r(6, "Leno", 1500, "agree", "unknown", confirmed="farm", stage="owner identified"),
    ]


def _data(html):
    m = re.search(r'<script type="application/json" id="rw-grants-data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def test_cards_show_funnel_counts_and_status():
    html = grants_tab(load_grants(DATA), TODAY, _roofs())
    # Agrisolare (farms): roofs 1 (guessed farm) and 6 (confirmed farm); 4 is do-not-contact, 5 is not a candidate
    card = re.search(r'<div class=\'rw-card\' data-grant="parco_agrisolare_2026"[^>]*>', html).group(0)
    assert 'data-roofs="2"' in card
    assert re.search(r'data-grant="parco_agrisolare_2026".*?interested\D+1', html, re.S)
    assert "Prepare for the next call" in html            # closed schemes
    assert re.search(r"OPEN.*?days left", html, re.S)      # open scheme countdown
    assert 'data-show="parco_agrisolare_2026"' in html     # the button opening its eligible list


def test_embedded_data_has_scores_and_no_markup_injection():
    html = grants_tab(load_grants(DATA), TODAY, _roofs())
    d = _data(html)
    ids = {r["i"] for r in d["roofs"]}
    assert ids == {1, 2, 3, 6}                              # candidates only, do-not-contact left out
    r6 = next(r for r in d["roofs"] if r["i"] == 6)
    assert r6["k"] == "confirmed" and r6["o"] == "farm" and "parco_agrisolare_2026" in r6["s"]
    assert "<b>x</b>" not in html and "</script></script>" not in html
    assert any(g["id"] == "parco_agrisolare_2026" for g in d["grants"])


def test_tabs_html_has_map_and_grants_tabs():
    html = tabs_html({"grants": ("Grants", "<p>content</p>")})
    assert 'data-tab="map"' in html and 'data-tab="grants"' in html
    assert "<p>content</p>" in html and "hashchange" in html and "window.rwTabs" in html
