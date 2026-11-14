from datetime import date
from pathlib import Path

from roofwatch.grants import load_grants
from roofwatch.tabs import grants_tab, tabs_html

DATA = Path(__file__).resolve().parents[1] / "data/grants_lombardia.json"
TODAY = date(2026, 11, 14)


def _roofs():
    return [
        {"roof_id": 1, "comune": "Verolanuova", "area_m2": 9000, "group": "agree", "owner_type": "farm", "why": "livestock permit", "dnc": False},
        {"roof_id": 2, "comune": "Manerbio", "area_m2": 4000, "group": "suspected_new", "owner_type": "company", "why": "business", "dnc": False},
        {"roof_id": 3, "comune": "Leno <b>x</b>", "area_m2": 250, "group": "agree", "owner_type": "home", "why": "small roof", "dnc": False},
        {"roof_id": 4, "comune": "Offlaga", "area_m2": 20000, "group": "agree", "owner_type": "farm", "why": "farmyard", "dnc": True},
        {"roof_id": 5, "comune": "Pompiano", "area_m2": 3000, "group": "mapped_not_seen", "owner_type": "farm", "why": "farmyard", "dnc": False},
    ]


def test_grants_tab_cards_counts_and_links():
    html = grants_tab(load_grants(DATA), TODAY, _roofs())
    assert "Parco Agrisolare 2026" in html and "OPEN" in html
    # farm schemes count farm candidate roofs: roof 1 only (4 is do-not-contact, 5 is not a candidate)
    assert 'data-grant="parco_agrisolare_2026" data-roofs="1"' in html
    assert 'href="#roof=1"' in html and 'href="#roof=4"' not in html and 'href="#roof=5"' not in html
    assert 'data-grant="bonus_ristrutturazioni_2026" data-roofs="1"' in html   # the home roof
    assert "Leno &lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html
    assert "NA/1" in html                                                       # owner obligations shown


def test_tabs_html_has_map_and_grants_tabs():
    html = tabs_html({"grants": ("Grants", "<p>content</p>")})
    assert 'data-tab="map"' in html and 'data-tab="grants"' in html
    assert "<p>content</p>" in html and "hashchange" in html
