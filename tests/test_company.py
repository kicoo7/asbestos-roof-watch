"""Company public record: VAT extraction, legal name and form, REA, VIES parsing, page selection. Offline, synthetic data only."""
import json
import re
from pathlib import Path

import pandas as pd
import pytest

from roofwatch.company import (entity_type, extract_legal_name, extract_vat_candidates, legal_form, page_text, parse_vies, rea_number,
                               select_pages, valid_partita_iva)

DATA = Path(__file__).parent / "data/company"
# synthetic numbers with a valid check digit (office code 017 = Brescia, 098 = Lodi); not looked up anywhere
VAT_A, VAT_B, VAT_C = "01234560173", "09876540171", "05554440981"
PHONE_11 = "03024560173"  # an 11-digit phone number that also passes the checksum
CF_ONLY = "91234560174"   # passes the checksum, but a 9xxxxxxx serial is an association's fiscal code, never a VAT number


def vats(text):
    return [c.vat for c in extract_vat_candidates(text)]


def test_valid_partita_iva():
    assert valid_partita_iva(VAT_A) and valid_partita_iva(VAT_B) and valid_partita_iva(VAT_C)
    assert not valid_partita_iva("01234560174")  # wrong check digit
    for bad in ("00000000000", "11111111111", "12345678903", "0123456017", "012345601733", "IT01234560173", "0123456017a",
                None, 1234560173, ""):
        assert not valid_partita_iva(bad), bad
    assert not valid_partita_iva("00000000017")  # zero serial
    assert not valid_partita_iva(CF_ONLY) and not valid_partita_iva("80001230178")  # fiscal codes of bodies without VAT


def test_page_text_strips_scripts_styles_and_decodes_entities():
    t = page_text((DATA / "home_footer_credit.html").read_text())
    assert "tracking" not in t and "content:" not in t and VAT_C not in t
    assert "Rossi Impianti S.r.l. – Via dell'Industria 12" in t
    assert f"C.F. e P. IVA {VAT_A}" in t
    assert page_text(t) == t  # idempotent: offsets from extract_vat_candidates(text) also fit page_text(text)


def test_page_text_copes_with_sloppy_markup():
    # </head> is optional in HTML; unclosed tags and stray brackets must not hide the footer
    assert vats(f"<html><head><title>Rossi</title><body><p>Rossi Srl<p>P.IVA {VAT_A}<div>a < b") == [VAT_A]
    assert page_text("<p>Caff&egrave; &amp; Bar</p><!-- P.IVA 0 --><p>x</p>") == "Caffè & Bar\nx"


def test_page_text_reads_vat_from_json_ld():
    html = ('<html><head><script type="application/ld+json">{"@type": "Organization", "legalName": "Bianchi Srl", '
            f'"vatID": "IT{VAT_A}"}}</script></head><body>Benvenuti</body></html>')
    assert vats(html) == [VAT_A]
    c = extract_vat_candidates(html)[0]
    assert extract_legal_name(page_text(html), c.pos) == "Bianchi Srl"


@pytest.mark.parametrize("snippet", [
    f"P.IVA {VAT_A}", f"P. IVA: {VAT_A}", f"P.Iva {VAT_A}", f"Partita IVA n. {VAT_A}", f"PARTITA IVA {VAT_A}",
    f"C.F. e P.IVA {VAT_A}", f"C.F./P.IVA {VAT_A}", f"VAT {VAT_A}", f"VAT No. IT{VAT_A}", f"VAT number: IT {VAT_A}",
    f"P.IVA IT{VAT_A}", "P.IVA 012 345 601 73", "P.IVA 01.234.560.173", f"P.I. {VAT_A}", f"Partita&nbsp;IVA&nbsp;{VAT_A}",
    f"<b>P.IVA</b><span>{VAT_A}</span>", f"<dt>Partita IVA</dt>\n<dd>{VAT_A}</dd>", f"{VAT_A} (P.IVA)",
    f"Iscritta al Registro delle Imprese di Brescia al n. {VAT_A}",
])
def test_extract_vat_spellings(snippet):
    assert vats(f"<footer>Rossi Srl - {snippet} - Brescia</footer>") == [VAT_A]


def test_phone_number_with_valid_checksum_is_not_a_vat():
    assert valid_partita_iva(PHONE_11)
    assert vats(f"Tel. {PHONE_11}") == []
    assert vats(f"Chiamaci al {PHONE_11} per un preventivo") == []
    assert vats(f"P.IVA {VAT_A} - Tel. {PHONE_11}") == [VAT_A]
    assert vats(f"P.IVA {VAT_A} {PHONE_11}") == [VAT_A]   # another number sits between the label and the phone
    assert vats(f"Partita IVA: tel. {PHONE_11}") == []    # a phone label is nearer than the VAT label
    assert vats(f"Ordine n. {VAT_A}") == []               # checksum alone is not enough


def test_numbers_inside_longer_digit_runs_are_ignored():
    assert vats(f"P.IVA 9{VAT_A}") == [] and vats(f"P.IVA {VAT_A}9") == []
    assert vats("IBAN IT60 X054 2811 1010 0000 0123 456 P.IVA") == []
    assert vats(f"P.IVA {VAT_A} 2024") == [VAT_A]


def test_codice_fiscale_equal_to_vat_is_one_candidate():
    c = extract_vat_candidates(f"C.F. {VAT_A} - P.IVA {VAT_A}")
    assert [x.vat for x in c] == [VAT_A] and c[0].count == 2 and c[0].strength == 3


def test_fiscal_code_of_a_body_without_vat_is_not_a_candidate():
    assert vats(f"Associazione Rossi - C.F. {CF_ONLY}") == []
    assert vats(f"C.F. {CF_ONLY} - P.IVA {VAT_A}") == [VAT_A]


def test_vat_label_beats_fiscal_code_label():
    c = extract_vat_candidates(f"Codice fiscale {VAT_B} - Partita IVA {VAT_A}")
    assert [x.vat for x in c] == [VAT_A, VAT_B]
    assert c[0].strength == 3 and c[1].strength == 1


def test_agency_credit_in_footer_ranks_last():
    c = extract_vat_candidates((DATA / "home_footer_credit.html").read_text())
    assert [x.vat for x in c] == [VAT_A, VAT_B]
    assert not c[0].credit and c[1].credit
    # agency first, business second: still the business first
    c = extract_vat_candidates(f"Sito realizzato da Pixel Web Agency Srl P.IVA {VAT_B}<br>© 2024 Rossi Srl - P.IVA {VAT_A}")
    assert [(x.vat, x.credit) for x in c] == [(VAT_A, False), (VAT_B, True)]
    # an agency named without a VAT before the business's own VAT does not taint it
    c = extract_vat_candidates(f"Powered by Pixel | © 2024 Rossi Srl P.IVA {VAT_A}")
    assert [(x.vat, x.credit) for x in c] == [(VAT_A, False)]
    # only the agency's VAT on the page: kept, but flagged
    assert [(x.vat, x.credit) for x in extract_vat_candidates(f"Web design by Pixel Srl - P.IVA {VAT_B}")] == [(VAT_B, True)]


def test_more_occurrences_rank_higher():
    c = extract_vat_candidates(f"P.IVA {VAT_B}<br>P.IVA {VAT_A}<br>Partita IVA {VAT_A}")
    assert [x.vat for x in c] == [VAT_A, VAT_B]


@pytest.mark.parametrize("text, expected", [
    (f"Rossi Impianti S.r.l. - Via Roma 1, 25100 Brescia - P.IVA {VAT_A}", "Rossi Impianti S.r.l."),
    (f"© 2024 ROSSI IMPIANTI SRL. Tutti i diritti riservati. P.IVA {VAT_A}", "ROSSI IMPIANTI SRL"),
    (f"Copyright 2023 Officina Meccanica di Rossi Mario & C. S.n.c. | P.IVA {VAT_A}", "Officina Meccanica di Rossi Mario & C. S.n.c."),
    (f"P.IVA {VAT_A} - Bianchi Costruzioni S.p.A.", "Bianchi Costruzioni S.p.A."),
    (f"Benvenuti nel sito di Verdi Legnami s.a.s. P.IVA {VAT_A}", "Verdi Legnami s.a.s."),
    (f"Cooperativa Sociale La Rondine - P.IVA {VAT_A}", "Cooperativa Sociale La Rondine"),
    (f"Mario Rossi - Via Roma 1 - P.IVA {VAT_A}", None),
])
def test_extract_legal_name(text, expected):
    t = page_text(text)
    assert extract_legal_name(t, extract_vat_candidates(t)[0].pos) == expected
    assert extract_legal_name(t, VAT_A) == expected


def test_extract_legal_name_ignores_agency_after_the_vat():
    t = page_text((DATA / "home_footer_credit.html").read_text())
    c = extract_vat_candidates(t)[0]
    assert extract_legal_name(t, c.pos) == "Rossi Impianti S.r.l."
    assert extract_legal_name(f"© Pixel S.r.l. P.IVA {VAT_A} realizzato da Agency S.p.A.", VAT_A) == "Pixel S.r.l."


@pytest.mark.parametrize("name, form", [
    ("Rossi Impianti S.R.L.", "srl"), ("Rossi Impianti srl", "srl"), ("Rossi Impianti S.r.l.", "srl"), ("ROSSI SRL", "srl"),
    ("Rossi S. r. l.", "srl"), ("Rossi Società a responsabilità limitata", "srl"), ("Rossi G. S.r.l.", "srl"),
    ("Rossi S.r.l.s.", "srls"), ("ROSSI SRLS", "srls"), ("Rossi S.r.l. semplificata", "srls"),
    ("ROSSI SOCIETA' RESPONSABILITA' LIMITATA SEMPLIFICATA", "srls"), ("ROSSI SOCIETA' A RESPONSABILITA' LIMITATA", "srl"),
    ("BANCA DI CREDITO COOPERATIVO DI ROSSI", "coop"), ("GRUPPO IVA ROSSI", "sole_trader_or_unknown"),
    ("Bianchi S.p.A.", "spa"), ("BIANCHI SPA", "spa"), ("Bianchi SpA", "spa"), ("Bianchi Società per Azioni", "spa"),
    ("Rossi & C. S.a.p.a.", "sapa"), ("Rossi Mario & C. s.n.c.", "snc"), ("ROSSI E C. SNC DI ROSSI MARIO", "snc"),
    ("Verdi s.a.s. di Verdi Luca & C.", "sas"), ("SOCIETA' AGRICOLA VERDI S.S.", "ss"), ("Società semplice agricola Verdi", "ss"),
    ("La Rondine Soc. Coop. a r.l.", "coop"), ("Cooperativa Sociale La Rondine Onlus", "coop"), ("LA RONDINE S.C.A R.L.", "coop"),
    ("Consorzio Agrario di Brescia", "consortium"), ("Servizi Società Consortile a r.l.", "consortium"),
    ("Associazione Sportiva Dilettantistica Rossi", "association_or_foundation"), ("Fondazione Bianchi Onlus", "association_or_foundation"),
    ("ASD Calcio Rossi", "association_or_foundation"), ("COMUNE DI BRESCIA", "public_body"),
    ("ROSSI MARIO", "sole_trader_or_unknown"), ("Autofficina Rossi di Rossi Mario", "sole_trader_or_unknown"),
    ("Business Express Glass", "sole_trader_or_unknown"), ("", "sole_trader_or_unknown"), (None, "sole_trader_or_unknown"),
])
def test_legal_form(name, form):
    assert legal_form(name) == form


def test_entity_type():
    for f in ("srl", "srls", "spa", "sapa", "snc", "sas", "ss", "coop", "consortium", "association_or_foundation", "public_body"):
        assert entity_type(f) == "company"
    assert entity_type("sole_trader_or_unknown") == "sole_trader_or_unknown"
    assert entity_type(None) == "sole_trader_or_unknown" and entity_type("") == "sole_trader_or_unknown"


@pytest.mark.parametrize("text, rea", [
    ("REA BS-412345", "BS-412345"), ("R.E.A. BS 412345", "BS-412345"), ("REA: BS412345", "BS-412345"), ("N. REA BS - 412345", "BS-412345"),
    ("Numero REA BS-412345", "BS-412345"), ("R.E.A. n. 412345", "412345"), ("REA di Brescia n. 412345", "412345"),
    (f"P.IVA {VAT_A} - Rea Bs 412345", "BS-412345"), ("Area produttiva 12000 m2", None), ("nessun numero", None), (None, None),
])
def test_rea_number(text, rea):
    assert rea_number(text) == rea


def test_parse_vies_valid():
    p = {"isValid": True, "requestDate": "2026-11-14T00:13:16.037Z", "userError": "VALID", "name": "ROSSI IMPIANTI S.R.L.",
         "address": "VIA DELL'INDUSTRIA 12 \n25100 BRESCIA BS\n", "vatNumber": VAT_A, "viesApproximate": {"name": "---"}}
    assert parse_vies(p) == {"valid": True, "name": "ROSSI IMPIANTI S.R.L.", "address": "VIA DELL'INDUSTRIA 12, 25100 BRESCIA BS", "error": None}
    # POST shape, name and address withheld by the member state
    assert parse_vies({"valid": True, "name": "---", "address": "---"}) == {"valid": True, "name": None, "address": None, "error": None}


def test_parse_vies_invalid_number():
    p = {"isValid": False, "userError": "INVALID", "name": "---", "address": "---", "vatNumber": VAT_B}
    assert parse_vies(p) == {"valid": False, "name": None, "address": None, "error": None}
    assert parse_vies({"valid": False})["valid"] is False


@pytest.mark.parametrize("payload, err", [
    ({"isValid": False, "userError": "MS_UNAVAILABLE", "name": "---", "address": "---"}, "MS_UNAVAILABLE"),
    ({"isValid": False, "userError": "MS_MAX_CONCURRENT_REQ"}, "MS_MAX_CONCURRENT_REQ"),
    ({"actionSucceed": False, "errorWrappers": [{"error": "SERVICE_UNAVAILABLE", "message": "try later"}]}, "SERVICE_UNAVAILABLE"),
    ({"actionSucceed": False}, "error"), ({}, "no answer"), (None, "no answer"), ({"name": "X"}, "no answer"),
])
def test_parse_vies_service_error_is_not_false(payload, err):
    out = parse_vies(payload)
    assert out["valid"] is None and out["error"] == err and out["name"] is None


def test_select_pages():
    html = (DATA / "home_no_vat.html").read_text()
    base = "https://www.verdi-legnami.example/it/"
    assert select_pages(base, html) == ["https://verdi-legnami.example/it/note-legali/", "https://www.verdi-legnami.example/it/contattaci"]
    assert select_pages(base, html, max_extra=5) == [
        "https://verdi-legnami.example/it/note-legali/", "https://www.verdi-legnami.example/it/contattaci",
        "https://www.verdi-legnami.example/it/privacy", "https://www.verdi-legnami.example/it/azienda.html",
        "https://www.verdi-legnami.example/it/credits"]


def test_select_pages_relative_links_meta_refresh_and_junk():
    base = "http://rossi.example/"
    html = ('<meta http-equiv="refresh" content="0; url=/it/index.html">'
            '<a href="chi-siamo">Chi siamo</a><a href="/">Home</a><a href="http://rossi.example/#contatti">Contatti</a>'
            '<a href="HTTP://ROSSI.EXAMPLE/Contatti/">Contatti</a><a href="ftp://rossi.example/legal">x</a>')
    assert select_pages(base, html, 3) == ["http://rossi.example/it/index.html", "http://rossi.example/Contatti/", "http://rossi.example/chi-siamo"]
    assert select_pages(base, "") == [] and select_pages(base, "<a href='/contatti'") == []
    frames = '<frameset><frame src="menu.htm"><frame src="main.htm"></frameset><a href="/contatti.htm">Contatti</a>'
    assert select_pages(base, frames) == ["http://rossi.example/menu.htm", "http://rossi.example/main.htm"]
    assert select_pages(base, frames, 3)[2] == "http://rossi.example/contatti.htm"


# ---------------------------------------------------------------- scripts/enrich_companies.py, offline with fake sites

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/enrich_companies.py"


def _load_ec():
    import importlib.util
    spec = importlib.util.spec_from_file_location("enrich_companies", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ec(tmp_path, monkeypatch):
    mod = _load_ec()
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "PAGES", tmp_path / "pages")
    monkeypatch.setattr(mod, "VIES_DIR", tmp_path / "vies")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return mod


def _vat_page(vat, name="Rossi Impianti S.r.l."):
    return f"<footer>{name} - REA BS-412345 - P.IVA {vat}</footer>"


_NO_VAT_HOME = '<a href="/contatti">Contatti</a><p>Benvenuti</p>'
SITES = {  # url -> (status, content type, body) or ("redirect", target url)
    "http://rossi.example/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /private\n"),
    "http://rossi.example/": (200, "text/html", '<a href="/private/note-legali">Note legali</a><a href="/contatti">Contatti</a><p>Benvenuti</p>'),
    "http://rossi.example/contatti": (200, "text/html; charset=iso-8859-1",
                                      f"<footer>Rossi Impianti S.r.l. - Societ\xe0 - REA BS-412345 - P.IVA {VAT_A}</footer>".encode("latin-1")),
    "http://blocked.example/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /\n"),
    "http://pixel.example/robots.txt": (404, "", ""),
    "http://pixel.example/": (200, "text/html", f"<p>Benvenuti</p><footer>Sito realizzato da Pixel Web Srl P.IVA {VAT_B}</footer>"),
    "http://pdf.example/robots.txt": (404, "", ""),
    "http://pdf.example/": (200, "application/pdf", "%PDF-1.4"),
    "http://captcha.example/robots.txt": (404, "", ""),
    "http://captcha.example/": (202, "text/html", '<html><head><meta http-equiv="refresh" content="0;/.well-known/sgcaptcha/?r=%2F"></head></html>'),
    # a home page that redirects to another host whose robots.txt forbids everything
    "http://moved.example/robots.txt": (404, "", ""),
    "http://moved.example/": ("redirect", "https://www.elsewhere.example/"),
    "https://www.elsewhere.example/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /\n"),
    "https://www.elsewhere.example/": (200, "text/html", _vat_page(VAT_A)),
    # ... and one whose new host allows it
    "http://hop.example/robots.txt": (404, "", ""),
    "http://hop.example/": ("redirect", "https://www.hop.example/"),
    "https://www.hop.example/robots.txt": (200, "text/plain", "User-agent: *\nAllow: /\n"),
    "https://www.hop.example/": (200, "text/html", _vat_page(VAT_B, "Hop Srl")),
    # an extra page that redirects to a partner's host that forbids crawling
    "http://side.example/robots.txt": (404, "", ""),
    "http://side.example/": (200, "text/html", _NO_VAT_HOME),
    "http://side.example/contatti": ("redirect", "https://partner.example/contatti"),
    "https://partner.example/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /\n"),
    "https://partner.example/contatti": (200, "text/html", _vat_page(VAT_A)),
    # robots.txt answering 429 (rate limited) even after backing off
    "http://busy.example/robots.txt": (429, "text/plain", ""),
    "http://busy.example/": (200, "text/html", _vat_page(VAT_A)),
    # Crawl-delay above 30 s: home page only; 20 s: honoured
    "http://lazy.example/robots.txt": (200, "text/plain", "User-agent: *\nCrawl-delay: 60\n"),
    "http://lazy.example/": (200, "text/html", _NO_VAT_HOME),
    "http://lazy.example/contatti": (200, "text/html", _vat_page(VAT_A)),
    "http://calm.example/robots.txt": (200, "text/plain", "User-agent: *\nCrawl-delay: 20\n"),
    "http://calm.example/": (200, "text/html", _NO_VAT_HOME),
    "http://calm.example/contatti": (200, "text/html", _vat_page(VAT_A)),
    # contact details on the page and a phone number in the link that leads to it: none of it may reach the cache
    "http://contact.example/robots.txt": (404, "", ""),
    "http://contact.example/": (200, "text/html", '<a href="/contatti?tel=0300000000">Contatti</a><a href="tel:+390300000000">Chiama</a>'),
    "http://contact.example/contatti?tel=0300000000": (200, "text/html", f'<p>info@contact.example - PEC rossi@pec.example</p>{_vat_page(VAT_A)}'),
}


def fake_fetch(calls):
    def fetch(url, pace):
        calls.append(url)
        final = url
        while url in SITES and SITES[url][0] == "redirect":
            url = SITES[url][1]
        if url not in SITES:
            return {"url": final, "final_url": url, "status": None, "content_type": "", "error": "dns", "truncated": False}, b""
        st, ct, body = SITES[url]
        body = body if isinstance(body, bytes) else body.encode()
        err = None if st < 400 else f"http {st}"
        return {"url": final, "final_url": url, "status": st, "content_type": ct, "error": err, "truncated": False}, body
    return fetch


def test_crawl_statuses_and_cache(ec, monkeypatch):
    calls = []
    monkeypatch.setattr(ec, "fetch", fake_fetch(calls))
    r = ec.crawl("rossi.example", "http://rossi.example/chi-siamo", False)
    assert (r["status"], r["vat"], r["page_name"], r["rea"]) == ("ok", VAT_A, "Rossi Impianti S.r.l.", "BS-412345")
    assert r["source_url"] == "http://rossi.example/contatti"
    assert "http://rossi.example/private/note-legali" not in calls  # robots.txt says no
    assert ec.crawl("blocked.example", "http://blocked.example/", False)["status"] == "robots_disallowed"
    assert calls[-1] == "http://blocked.example/robots.txt"
    r = ec.crawl("down.example", "http://down.example/", False)
    assert (r["status"], r["reason"]) == ("fetch_failed", "dns")
    assert "http://www.down.example/robots.txt" in calls  # tried with www. too, and never fetched a page without robots.txt
    assert not any(u.endswith("down.example/") for u in calls)
    r = ec.crawl("pixel.example", "http://pixel.example/", False)
    assert (r["status"], r["vat"]) == ("no_vat_found", "") and r["reason"].startswith("only a web-agency VAT")
    assert ec.crawl("pdf.example", "http://pdf.example/", False)["status"] == "fetch_failed"
    assert ec.crawl("captcha.example", "http://captcha.example/", False)["reason"] == "bot challenge"
    assert ec.crawl("facebook.com", "https://www.facebook.com/rossi", False)["status"] == "not_own_site"
    n = len(calls)  # while a domain is crawled, its pages are cached: no second request
    ec.crawl("rossi.example", "http://rossi.example/", False)
    assert len(calls) == n


# ---- Minor 5a: a redirect to another host is used only if that host's robots.txt allows it

def test_home_redirect_to_a_host_that_disallows_is_not_used(ec, monkeypatch):
    calls = []
    monkeypatch.setattr(ec, "fetch", fake_fetch(calls))
    r = ec.crawl("moved.example", "http://moved.example/", False)
    assert r["status"] == "robots_disallowed" and r["vat"] == ""
    assert "https://www.elsewhere.example/robots.txt" in calls
    r = ec.crawl("hop.example", "http://hop.example/", False)
    assert (r["status"], r["vat"], r["source_url"]) == ("ok", VAT_B, "https://www.hop.example/")
    assert "https://www.hop.example/robots.txt" in calls


def test_extra_page_redirected_to_a_disallowing_host_is_skipped(ec, monkeypatch):
    calls = []
    monkeypatch.setattr(ec, "fetch", fake_fetch(calls))
    r = ec.crawl("side.example", "http://side.example/", False)
    assert (r["status"], r["vat"]) == ("no_vat_found", "")
    assert "https://partner.example/robots.txt" in calls


# ---- Minor 5b, 5c, 5d: Crawl-delay up to 30 s, robots.txt 429, one pace per registrable domain

def test_crawl_delay_over_30_s_means_home_page_only(ec, monkeypatch):
    calls = []
    monkeypatch.setattr(ec, "fetch", fake_fetch(calls))
    r = ec.crawl("lazy.example", "http://lazy.example/", False)
    assert r["status"] == "no_vat_found" and "http://lazy.example/contatti" not in calls
    r = ec.crawl("calm.example", "http://calm.example/", False)
    assert (r["status"], r["vat"]) == ("ok", VAT_A) and "http://calm.example/contatti" in calls
    assert ec.pace_for("calm.example").gap == 20 and ec.pace_for("lazy.example").gap == 30


def test_robots_txt_answering_429_is_a_failure_not_a_licence(ec, monkeypatch):
    calls = []
    monkeypatch.setattr(ec, "fetch", fake_fetch(calls))
    r = ec.crawl("busy.example", "http://busy.example/", False)
    assert (r["status"], r["reason"]) == ("fetch_failed", "robots.txt http 429")
    assert "http://busy.example/" not in calls


def test_one_pace_per_registrable_domain():
    ec = _load_ec()
    assert ec.site_key("shop.rossi.it") == ec.site_key("www.rossi.it") == ec.site_key("rossi.it") == "rossi.it"
    assert ec.site_key("manerbio1.agency.example") == "agency.example"
    assert ec.site_key("www.rossi.co.uk") == "rossi.co.uk" and ec.site_key("comune.brescia.bs.it") == "brescia.bs.it"
    assert ec.pace_for("shop.rossi.it") is ec.pace_for("http://www.rossi.it/contatti") is not ec.pace_for("bianchi.it")


def test_pace_serialises_requests_across_threads():
    import threading
    import time
    ec = _load_ec()
    pace, spans = ec.Pace(gap=0.05), []

    def worker():
        for _ in range(3):
            with pace:
                t0 = time.monotonic()
                time.sleep(0.01)
                spans.append((t0, time.monotonic()))
    ts = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    spans.sort()
    assert len(spans) == 6 and all(b[0] - a[1] >= 0.045 for a, b in zip(spans, spans[1:]))  # one at a time, 50 ms apart


# ---- S5: no page bodies kept; a small record per domain makes reruns offline

def test_page_bodies_are_not_kept_after_a_crawl(ec, monkeypatch):
    calls = []
    monkeypatch.setattr(ec, "fetch", fake_fetch(calls))
    r = ec.crawl_safe(("contact.example", "http://contact.example/"), False)
    assert (r["status"], r["vat"]) == ("ok", VAT_A)
    files = [f for f in ec.PAGES.rglob("*") if f.is_file()]
    assert not [f for f in files if f.suffix == ".body"]
    for f in files:  # what is left holds no contact details, not even in a URL
        t = f.read_text()
        assert "@" not in t and "tel" not in t and "0300000000" not in t, f.name


def test_record_cache_reruns_offline_and_keep_pages(ec, monkeypatch):
    calls = []
    monkeypatch.setattr(ec, "fetch", fake_fetch(calls))
    r1 = ec.domain_record("contact.example", "http://contact.example/")
    assert r1["source_url"] == "http://contact.example/contatti"  # the ?tel=... query is dropped
    assert [f.name for f in (ec.PAGES / "contact.example").iterdir()] == ["record.json"]
    n = len(calls)
    assert ec.domain_record("contact.example", "http://contact.example/") == r1 and len(calls) == n  # offline
    r2 = ec.domain_record("rossi.example", "http://rossi.example/", keep_pages=True)  # debugging: bodies kept
    assert r2["vat"] == VAT_A and list((ec.PAGES / "rossi.example").glob("*.body"))
    assert ec.domain_record("rossi.example", "http://rossi.example/") == r2  # a default run removes the leftovers
    assert [f.name for f in (ec.PAGES / "rossi.example").iterdir()] == ["record.json"]


def test_retry_failed_crawls_a_failed_record_again(ec, monkeypatch):
    calls = []
    monkeypatch.setattr(ec, "fetch", fake_fetch(calls))
    assert ec.domain_record("down.example", "http://down.example/")["status"] == "fetch_failed"
    n = len(calls)
    ec.domain_record("down.example", "http://down.example/")
    assert len(calls) == n
    ec.domain_record("down.example", "http://down.example/", retry_failed=True)
    assert len(calls) > n


def test_tidy_cache_drops_page_files_in_every_folder(ec):
    for d in ("gone.example", "123456789.example", "listed.example"):
        (ec.PAGES / d).mkdir(parents=True)
        (ec.PAGES / d / "abc.body").write_bytes(b"<p>info@x.example</p>")
        (ec.PAGES / d / "record.json").write_text("{}")
    (ec.PAGES / "unrecorded.example").mkdir()
    (ec.PAGES / "unrecorded.example" / "abc.body").write_bytes(b"<p>x</p>")
    removed, _ = ec.tidy_cache(keep_pages=True)  # debugging: page files stay, a phone-like folder never does
    assert removed == 1 and not (ec.PAGES / "123456789.example").exists() and (ec.PAGES / "listed.example" / "abc.body").exists()
    removed, freed = ec.tidy_cache()
    assert removed == 1 and not (ec.PAGES / "unrecorded.example").exists() and freed > 0
    for d in ("gone.example", "listed.example"):  # listed or not: only the record is left
        assert [f.name for f in (ec.PAGES / d).iterdir()] == ["record.json"]


def test_a_crawl_that_raises_leaves_no_page_bodies(ec, monkeypatch):
    calls = []
    monkeypatch.setattr(ec, "fetch", fake_fetch(calls))
    monkeypatch.setattr(ec, "extract_vat_candidates", lambda html: (_ for _ in ()).throw(RuntimeError("parser bug")))
    r = ec.crawl_safe(("rossi.example", "http://rossi.example/"), False)
    assert (r["status"], r["reason"]) == ("fetch_failed", "error RuntimeError") and "http://rossi.example/" in calls
    assert not list(ec.PAGES.rglob("*.body"))
    ec.crawl_safe(("rossi.example", "http://rossi.example/"), False, True)  # --keep-pages: kept, for debugging
    assert list(ec.PAGES.rglob("*.body"))


# ---- B1: a messaging link or a URL carrying a phone number or e-mail address is not a website

MESSY_URLS = ["https://wa.me/390000000000", "https://api.whatsapp.com/send?phone=390000000000", "http://info@acme.example/",
              "https://t.me/rossi", "http://www.rossi.example/chi-siamo?utm_source=x&tel=0300000000", "rossi.example",
              "http://bianchi.example/0300000000/contatti", "https://www.verdi.example/#contatti"]


def test_load_websites_uses_public_website(ec):
    (ec.ROOT / "outputs").mkdir()
    pd.DataFrame({"website": MESSY_URLS}).to_csv(ec.ROOT / "outputs/roof_occupants.csv", index=False)
    _, items = ec.load_websites()
    # a phone number in the path only: public_website keeps the site's root, the rest is dropped or cleaned
    assert items == [("bianchi.example", "http://bianchi.example/"), ("rossi.example", "http://rossi.example"),
                     ("verdi.example", "https://www.verdi.example/")]


def test_run_writes_no_phone_bearing_url_anywhere(ec, monkeypatch):
    calls = []
    monkeypatch.setattr(ec, "fetch", fake_fetch(calls))
    monkeypatch.setattr(ec.urllib.request, "urlopen", lambda req, timeout: _Resp({"isValid": True, "userError": "VALID", "name": "ROSSI SRL"}))
    items = ec.group_websites(MESSY_URLS + ["http://contact.example/"])
    rows, _ = ec.run(items)
    ec.write_register(rows, ec.ROOT / "register.csv")
    everything = [ec.ROOT / "register.csv"] + [f for f in ec.ROOT.rglob("*") if f.is_file()]
    for f in everything:
        for t in (str(f.relative_to(ec.ROOT)), f.read_text()):
            assert "@" not in t and "wa.me" not in t and "whatsapp" not in t and "0000000" not in t, f
    assert not any("tel" in u for u in calls if "contact.example" not in u)


# ---- suppression after an objection: outputs/company_suppress.csv

def _write_suppress(ec, text, enc="utf-8-sig"):
    (ec.ROOT / "outputs").mkdir(exist_ok=True)
    (ec.ROOT / "outputs/company_suppress.csv").write_bytes(text.encode(enc))


def test_load_suppression_reads_like_the_worksheet(ec):
    assert ec.load_suppression() == (frozenset(), frozenset())  # no file: nothing suppressed
    _write_suppress(ec, f"Domain;VAT\r\nhttps://www.Rossi.example/chi-siamo;\r\n;IT 012 345 60173\r\nverdi.example;{VAT_B[1:]}\r\n", "cp1252")
    assert ec.load_suppression() == ({"rossi.example", "verdi.example"}, {VAT_A, VAT_B})  # ';', CRLF, IT prefix, lost zero
    _write_suppress(ec, f"vat\n{VAT_A}\n")  # UTF-8 with BOM, one column
    assert ec.load_suppression() == (set(), {VAT_A})
    _write_suppress(ec, "domain\n")
    assert ec.load_suppression() == (set(), set())
    _write_suppress(ec, "sito;partita iva\nrossi.example;x\n")
    with pytest.raises(ValueError, match="'domain' and/or 'vat'"):
        ec.load_suppression()


def test_suppression_vat_with_leading_zeros_lost_by_a_spreadsheet(ec, monkeypatch):
    monkeypatch.setattr(ec, "fetch", lambda url, pace: pytest.fail("no fetch: the record is cached"))
    (ec.PAGES / "zero.example").mkdir(parents=True)
    (ec.PAGES / "zero.example" / "record.json").write_text(json.dumps({"status": "ok", "vat": "00012345678", "page_name": "Zero Srl"}))
    for cell in ("12345678", "IT 0001 2345 678", "0001.2345.678"):
        _write_suppress(ec, f"vat\n{cell}\n")
        assert ec.load_suppression() == (set(), {"00012345678"})
        rows, _ = ec.run(ec.group_websites(["http://zero.example/"]), suppress=ec.load_suppression())
        assert [(r["domain"], r["status"], r["vat"]) for r in rows] == [("zero.example", "suppressed", "")]
        assert "00012345678" not in (ec.PAGES / "zero.example" / "record.json").read_text()


@pytest.mark.parametrize("text, where", [("vat\nabc\n", "line 2, vat 'abc'"), ("vat\n01234560173\n123456789012\n", "line 3, vat"),
                                         ("domain;vat\nrossi.example;\n???;\n", "line 3, domain '???'")])
def test_unreadable_suppression_cell_stops_the_run_before_anything(ec, monkeypatch, text, where):
    (ec.ROOT / "outputs").mkdir()
    pd.DataFrame({"website": ["http://rossi.example/"]}).to_csv(ec.ROOT / "outputs/roof_occupants.csv", index=False)
    _write_suppress(ec, text)
    with pytest.raises(ValueError, match="Nothing was written") as e:
        ec.load_suppression()
    assert where in str(e.value)
    monkeypatch.setattr(ec, "run", lambda *a, **k: pytest.fail("no crawl"))
    monkeypatch.setattr(ec, "write_register", lambda *a, **k: pytest.fail("nothing written"))
    monkeypatch.setattr(ec.sys, "argv", ["enrich_companies.py"])
    with pytest.raises(ValueError, match=re.escape(where)):
        ec.main()
    assert not ec.PAGES.exists()


def _vies_answers(ec, monkeypatch, asked):
    monkeypatch.setattr(ec.urllib.request, "urlopen", lambda req, timeout: asked.append(req.full_url) or _Resp(
        {"isValid": True, "userError": "VALID", "name": "ROSSI MARIO", "address": "VIA CASA 1"}))


def test_suppressed_domain_is_forgotten_and_never_fetched_again(ec, monkeypatch):
    calls, asked = [], []
    monkeypatch.setattr(ec, "fetch", fake_fetch(calls))
    _vies_answers(ec, monkeypatch, asked)
    items = ec.group_websites(["http://rossi.example/", "http://hop.example/"])
    ec.run(items)
    assert (ec.PAGES / "rossi.example" / "record.json").exists() and (ec.VIES_DIR / f"{VAT_A}.json").exists()
    _write_suppress(ec, "domain\nrossi.example\n")
    n, m = len(calls), len(asked)
    for _ in range(2):  # the run that suppresses, then a rerun while it is listed
        rows, _ = ec.run(items, suppress=ec.load_suppression())
        assert next(r for r in rows if r["domain"] == "rossi.example") == {c: "" for c in ec.COLS} | {"domain": "rossi.example",
                                                                                                    "status": "suppressed"}
        assert _tag_only(ec, "rossi.example", VAT_A, "Rossi") and not (ec.VIES_DIR / f"{VAT_A}.json").exists()
        assert len(calls) == n and len(asked) == m
    assert next(r for r in rows if r["domain"] == "hop.example")["status"] == "ok"


def _tag_only(ec, domain, vat, name):
    """The domain's folder holds only a tag-only record: no VAT number, name or URL."""
    files = list((ec.PAGES / domain).iterdir())
    text = files[0].read_text()
    return ([f.name for f in files] == ["record.json"] and set(json.loads(text)) == {"status", "vat_tag"} and vat not in text
            and name.lower() not in text.lower() and "http" not in text)


def test_a_listed_domains_vat_suppresses_its_other_websites(ec, monkeypatch):
    calls, asked = [], []
    monkeypatch.setattr(ec, "fetch", fake_fetch(calls))
    _vies_answers(ec, monkeypatch, asked)
    items = ec.group_websites(["http://rossi.example/", "http://calm.example/", "http://hop.example/"])  # rossi, calm: VAT_A
    ec.run(items)
    _write_suppress(ec, "domain\nrossi.example\n")
    for _ in range(2):
        rows, _ = ec.run(items, suppress=ec.load_suppression())
        assert {r["domain"]: r["status"] for r in rows} == {"rossi.example": "suppressed", "calm.example": "suppressed",
                                                            "hop.example": "ok"}
        assert _tag_only(ec, "calm.example", VAT_A, "Rossi") and not (ec.VIES_DIR / f"{VAT_A}.json").exists() and len(asked) == 2


def test_suppressed_vat_keeps_no_identity_and_lifts_when_unlisted(ec, monkeypatch):
    calls, asked = [], []
    monkeypatch.setattr(ec, "fetch", fake_fetch(calls))
    _vies_answers(ec, monkeypatch, asked)
    items = ec.group_websites(["http://hop.example/", "http://rossi.example/"])  # hop.example shows VAT_B
    ec.run(items, keep_pages=True)
    _write_suppress(ec, f"vat\nIT{VAT_B}\n")
    n, m = len(calls), len(asked)
    for _ in range(2):
        rows, _ = ec.run(items, suppress=ec.load_suppression())
        assert next(r for r in rows if r["domain"] == "hop.example") == {c: "" for c in ec.COLS} | {"domain": "hop.example",
                                                                                                  "status": "suppressed"}
        rec = (ec.PAGES / "hop.example" / "record.json").read_text()
        assert json.loads(rec)["status"] == "suppressed" and VAT_B not in rec and "Hop Srl" not in rec
        assert [f.name for f in (ec.PAGES / "hop.example").iterdir()] == ["record.json"]
        assert not (ec.VIES_DIR / f"{VAT_B}.json").exists() and len(calls) == n and len(asked) == m
    assert next(r for r in rows if r["domain"] == "rossi.example")["status"] == "ok"
    (ec.ROOT / "outputs/company_suppress.csv").unlink()  # taken off the list: the site is read again
    rows, _ = ec.run(items, suppress=ec.load_suppression())
    assert next(r for r in rows if r["domain"] == "hop.example")["vat"] == VAT_B and len(calls) > n


def test_suppressed_domains_stay_suppressed_when_no_longer_listed(ec, monkeypatch):
    calls, asked = [], []
    monkeypatch.setattr(ec, "fetch", fake_fetch(calls))
    _vies_answers(ec, monkeypatch, asked)
    ec.run(ec.group_websites(["http://rossi.example/", "http://hop.example/", "http://side.example/"]))
    (ec.PAGES / "old.side.example").mkdir()  # a subdomain cached by an earlier run
    (ec.PAGES / "old.side.example" / "record.json").write_text('{"status": "no_vat_found", "vat": ""}')
    _write_suppress(ec, f"domain;vat\nrossi.example;\nside.example;\n;{VAT_B}\n")
    for _ in range(2):  # none of them is in the input any more
        rows, _ = ec.run(ec.group_websites(["http://pixel.example/"]), suppress=ec.load_suppression())
        assert {r["domain"]: r["status"] for r in rows} == {"rossi.example": "suppressed", "side.example": "suppressed",
                                                            "old.side.example": "suppressed", "hop.example": "suppressed",
                                                            "pixel.example": "no_vat_found"}
        assert _tag_only(ec, "rossi.example", VAT_A, "Rossi") and _tag_only(ec, "hop.example", VAT_B, "Hop")
        assert not (ec.VIES_DIR / f"{VAT_A}.json").exists() and not (ec.VIES_DIR / f"{VAT_B}.json").exists()
    (ec.ROOT / "outputs/company_suppress.csv").unlink()  # off the list: the tag-only records go
    rows, _ = ec.run(ec.group_websites(["http://pixel.example/"]), suppress=ec.load_suppression())
    assert [r["domain"] for r in rows] == ["pixel.example"] and sorted(d.name for d in ec.PAGES.iterdir()) == ["pixel.example"]


def _vies_by_vat(ec, monkeypatch, asked):
    names = {VAT_A: "ROSSI IMPIANTI S.R.L.", VAT_B: "HOP S.R.L."}
    monkeypatch.setattr(ec.urllib.request, "urlopen", lambda req, timeout: asked.append(req.full_url) or _Resp(
        {"isValid": True, "userError": "VALID", "name": names[req.full_url.rsplit("/", 1)[1]], "address": "VIA X 1 \n25100 BRESCIA BS\n"}))


_CYCLES = {  # places, suppression list, domains that must stay suppressed, what no cache file may hold
    "vat only": (["http://rossi.example/", "http://hop.example/"], f"vat\n{VAT_B}\n", {"hop.example"},
                 [VAT_B, "Hop Srl", "HOP S.R.L.", "://hop.example", "://www.hop.example"]),
    "domain, VAT shared": (["http://rossi.example/", "http://calm.example/", "http://hop.example/"], "domain\nrossi.example\n",
                           {"rossi.example", "calm.example"},
                           [VAT_A, "Rossi Impianti", "ROSSI IMPIANTI", "://rossi.example", "://calm.example"]),
}


@pytest.mark.parametrize("contacting_order", [True, False], ids=["enrich-then-worksheet", "worksheet-then-enrich"])
@pytest.mark.parametrize("case", list(_CYCLES))
def test_suppression_survives_the_listing_leaving_the_input(ec, monkeypatch, case, contacting_order):
    """Review 4, section 1: the worksheet drops a suppressed listing, so the next input lacks it; it must stay suppressed."""
    from roofwatch.urls import normalize_domain
    places, listed, gone, secrets = _CYCLES[case]
    calls, asked = [], []
    monkeypatch.setattr(ec, "fetch", fake_fetch(calls))
    _vies_by_vat(ec, monkeypatch, asked)
    out = ec.ROOT / "register.csv"
    ec.write_register(ec.run(ec.group_websites(places))[0], out)  # before the objection
    _write_suppress(ec, listed)
    n, m = len(calls), len(asked)

    def worksheet():  # what contact_worksheet.py keeps: listings neither on the list nor suppressed in the register
        reg = pd.read_csv(out, dtype=str, keep_default_na=False)
        hidden = set(reg.domain[reg.status == "suppressed"]) | set(ec.load_suppression()[0])
        return [u for u in places if not ec.is_suppressed(normalize_domain(u), hidden)]
    occupants = list(places)
    for cycle in range(4):
        if not contacting_order:
            occupants = worksheet()
        ec.write_register(ec.run(ec.group_websites(occupants), suppress=ec.load_suppression())[0], out)
        if contacting_order:
            occupants = worksheet()
        reg = pd.read_csv(out, dtype=str, keep_default_na=False).set_index("domain")
        assert all(reg.status.get(d) == "suppressed" for d in gone), (cycle, reg.status.to_dict())
        assert not {normalize_domain(u) for u in worksheet()} & gone and (reg.status != "suppressed").sum() == len(places) - len(gone)
        for f in [p for d in (ec.PAGES, ec.VIES_DIR) for p in d.rglob("*") if p.is_file()]:
            text = f.name + f.read_text()
            assert not [x for x in secrets if x in text], (cycle, f.name)
        assert len(calls) == n and len(asked) == m  # nothing fetched or looked up again


def test_suppression_list_covers_subdomains(ec, monkeypatch):
    calls = []
    monkeypatch.setattr(ec, "fetch", fake_fetch(calls))
    _write_suppress(ec, "domain\nhop.example\n")
    items = ec.group_websites(["http://shop.hop.example/", "http://www.hop.example/", "http://nothop.example/"])
    rows, _ = ec.run(items, suppress=ec.load_suppression())
    assert {r["domain"]: r["status"] for r in rows} == {"shop.hop.example": "suppressed", "hop.example": "suppressed",
                                                        "nothop.example": "fetch_failed"}  # a look-alike is not covered
    assert not any("hop.example" in u and "nothop" not in u for u in calls) and not (ec.PAGES / "shop.hop.example").exists()
    assert ec.is_suppressed("a.b.hop.example", {"hop.example"}) and not ec.is_suppressed("hop.example.it", {"hop.example"})


# ---- Minor 7, and the row assembly the review asked to test

def test_register_has_a_tls_unverified_column(ec):
    assert ec.COLS[-1] == "tls_unverified"


_OK = {"domain": "a.example", "website": "http://a.example/", "status": "ok", "vat": VAT_A, "page_name": "Pagina Srl", "rea": "BS-1",
       "source_url": "http://a.example/", "fetched_on": "2026-11-14", "tls_unverified": True}


def _vies(**payload):
    return {"checked_on": "2026-11-13", "http_status": 200, "payload": payload}


def test_register_row_vies_confirmed_company(ec):
    row = ec.register_row(_OK, _vies(isValid=True, userError="VALID", name="VIES NOME S.R.L.", address="VIA X 1 \n25100 BRESCIA BS\n"))
    assert (row["legal_name"], row["legal_form"], row["entity_type"]) == ("VIES NOME S.R.L.", "srl", "company")
    assert (row["registered_office"], row["vies_valid"], row["vies_checked_on"]) == ("VIA X 1, 25100 BRESCIA BS", "true", "2026-11-13")
    assert row["tls_unverified"] == "true" and list(row) == ec.COLS


def test_register_row_sole_trader_never_gets_an_address(ec):
    row = ec.register_row(_OK, _vies(isValid=True, userError="VALID", name="ROSSI MARIO", address="VIA CASA 1 \n25100 BRESCIA BS\n"))
    assert (row["legal_name"], row["entity_type"], row["registered_office"]) == ("ROSSI MARIO", "sole_trader_or_unknown", "")


def test_register_row_without_vies_confirmation_uses_the_page(ec):
    for vr, valid, on in ((_vies(isValid=False, userError="INVALID", name="---", address="---"), "false", "2026-11-13"),
                          (_vies(isValid=False, userError="MS_UNAVAILABLE"), "", ""), (None, "", "")):
        row = ec.register_row(_OK, vr)
        assert (row["legal_name"], row["legal_form"], row["registered_office"]) == ("Pagina Srl", "srl", "")
        assert (row["vies_valid"], row["vies_checked_on"]) == (valid, on)
    row = ec.register_row(_OK, _vies(isValid=True, userError="VALID", name="---", address="VIA X 1"))  # name withheld
    assert (row["legal_name"], row["registered_office"]) == ("Pagina Srl", "VIA X 1")


def test_register_row_without_vat(ec):
    row = ec.register_row({**_OK, "status": "no_vat_found", "vat": "", "source_url": "", "rea": ""}, None)
    assert {k for k, v in row.items() if v} == {"domain", "website", "status", "fetched_on"}


def test_run_twice_is_offline_and_byte_identical(ec, monkeypatch):
    calls, asked = [], []
    monkeypatch.setattr(ec, "fetch", fake_fetch(calls))
    monkeypatch.setattr(ec.urllib.request, "urlopen", lambda req, timeout: asked.append(1) or _Resp(
        {"isValid": True, "userError": "VALID", "name": "ROSSI IMPIANTI S.R.L.", "address": "VIA X 1 \n25100 BRESCIA BS\n"}))
    items = ec.group_websites([f"http://{d}.example/" for d in ("rossi", "blocked", "down", "pixel", "pdf", "captcha", "hop", "side", "calm")])
    out = ec.ROOT / "register.csv"
    ec.write_register(ec.run(items)[0], out)
    first, n, m = out.read_bytes(), len(calls), len(asked)
    ec.write_register(ec.run(items)[0], out)
    assert out.read_bytes() == first and len(calls) == n and len(asked) == m
    assert not list(ec.PAGES.rglob("*.body"))


# ---- fetch(): retries and the TLS fallback

def _scripted_http_get(answers, seen):
    def http_get(url, insecure=False):
        seen.append(insecure)
        a = answers.pop(0)
        return {"url": url, "final_url": url, "status": a.get("status", 200), "content_type": "text/html", "error": a.get("error"),
                "truncated": False, "retry_after": a.get("retry_after")}, b"<p>ok</p>"
    return http_get


def test_fetch_retries_transient_errors(ec, monkeypatch):
    seen, sleeps = [], []
    monkeypatch.setattr(ec.time, "sleep", sleeps.append)
    monkeypatch.setattr(ec, "http_get", _scripted_http_get([{"status": 503, "error": "http 503"}, {"status": None, "error": "timeout"}, {}], seen))
    meta, body = ec.fetch("http://a.example/", ec.Pace(0))
    assert meta["error"] is None and body == b"<p>ok</p>" and len(seen) == 3 and sleeps == [2, 4]
    seen.clear(), sleeps.clear()
    monkeypatch.setattr(ec, "http_get", _scripted_http_get([{"status": 429, "error": "http 429", "retry_after": "7"}, {}], seen))
    assert ec.fetch("http://a.example/", ec.Pace(0))[0]["error"] is None and sleeps == [7]  # Retry-After honoured
    seen.clear()
    monkeypatch.setattr(ec, "http_get", _scripted_http_get([{"status": 503, "error": "http 503"}] * 5, seen))
    assert ec.fetch("http://a.example/", ec.Pace(0))[0]["error"] == "http 503" and len(seen) == 3  # 2 retries, then give up
    seen.clear()
    monkeypatch.setattr(ec, "http_get", _scripted_http_get([{"status": 404, "error": "http 404"}, {}], seen))
    assert ec.fetch("http://a.example/", ec.Pace(0))[0]["error"] == "http 404" and len(seen) == 1  # not transient


def test_fetch_tls_fallback(ec, monkeypatch):
    seen = []
    monkeypatch.setattr(ec, "http_get", _scripted_http_get([{"status": None, "error": "tls certificate: expired"}, {}], seen))
    meta, _ = ec.fetch("https://a.example/", ec.Pace(0))
    assert seen == [False, True] and meta["error"] is None and meta["tls_unverified"] is True
    seen.clear()
    monkeypatch.setattr(ec, "http_get", _scripted_http_get([{"status": None, "error": "tls: handshake failure"}] * 2, seen))
    meta, _ = ec.fetch("https://a.example/", ec.Pace(0))
    assert seen == [False, True] and meta["error"].startswith("tls") and meta["tls_unverified"] is False


# ---- Minor 6: bounded decompression

def test_decompression_is_bounded(ec, monkeypatch):
    import zlib
    real, outs = zlib.decompressobj, []

    class Spy:
        def __init__(self, *a):
            self.o = real(*a)

        def decompress(self, data, max_length=0):
            out = self.o.decompress(data, max_length)
            outs.append(len(out))
            return out
    co = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    bomb = b"".join(co.compress(b"\0" * 1_000_000) for _ in range(20)) + co.flush()  # 20 MB of zeros in about 20 kB
    assert len(bomb) < 100_000
    monkeypatch.setattr(ec.zlib, "decompressobj", Spy)
    out = ec._decompress(bomb, "gzip")
    assert len(out) == ec.MAX_BYTES and max(outs) <= ec.MAX_BYTES  # never more than MAX_BYTES produced, not cut afterwards
    assert ec._decompress(b"not gzip at all", "gzip") == b""
    assert ec._decompress(zlib.compress(b"<p>ciao</p>"), "deflate") == b"<p>ciao</p>"


# ---- VIES

class _Resp:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def read(self, n=-1):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_vies_lookup_backs_off_caches_and_drops_sole_trader_address(ec, monkeypatch):
    answers = [{"isValid": False, "userError": "MS_UNAVAILABLE"},
               {"isValid": True, "userError": "VALID", "name": "ROSSI MARIO", "address": "VIA ROMA 1 \n25100 BRESCIA BS\n",
                "viesApproximate": {"name": "---"}}]
    monkeypatch.setattr(ec.urllib.request, "urlopen", lambda req, timeout: _Resp(answers.pop(0)))
    rec, asked = ec.vies_lookup(VAT_A, ec.Pace(), False)
    assert asked and parse_vies(rec["payload"])["valid"] is True
    assert "address" not in rec["payload"] and "viesApproximate" not in rec["payload"]
    assert json.loads((ec.VIES_DIR / f"{VAT_A}.json").read_text()) == rec
    assert ec.vies_lookup(VAT_A, ec.Pace(), False) == (rec, False) and answers == []  # cached: no second request
    assert ec.vies_lookup(VAT_B, ec.Pace(), False, live=False) == (None, False)
    # the name now reads as a company (e.g. after a classifier fix) but the cached answer has no address: ask again
    stale = {"checked_on": "2026-01-01", "http_status": 200, "payload": {"isValid": True, "userError": "VALID", "name": "ROSSI SRL"}}
    (ec.VIES_DIR / f"{VAT_B}.json").write_text(json.dumps(stale))
    answers.append({"isValid": True, "userError": "VALID", "name": "ROSSI SRL", "address": "VIA ROMA 1 \n25100 BRESCIA BS\n"})
    rec, asked = ec.vies_lookup(VAT_B, ec.Pace(), False)
    assert asked and parse_vies(rec["payload"])["address"] == "VIA ROMA 1, 25100 BRESCIA BS" and answers == []


def test_vies_lookup_offline_still_returns_a_cached_answer(ec, monkeypatch):
    # Minor 10: with the circuit breaker tripped (live=False), an entry that would be refreshed is still used
    monkeypatch.setattr(ec.urllib.request, "urlopen", lambda req, timeout: pytest.fail("no request when live=False"))
    stale = {"checked_on": "2026-01-01", "http_status": 200, "payload": {"isValid": True, "userError": "VALID", "name": "ROSSI SRL"}}
    failed = {"checked_on": "2026-01-01", "http_status": 200, "payload": {"isValid": False, "userError": "MS_UNAVAILABLE"}}
    ec.VIES_DIR.mkdir(parents=True)
    (ec.VIES_DIR / f"{VAT_A}.json").write_text(json.dumps(stale))
    (ec.VIES_DIR / f"{VAT_B}.json").write_text(json.dumps(failed))
    assert ec.vies_lookup(VAT_A, ec.Pace(), False, live=False) == (stale, False)
    assert ec.vies_lookup(VAT_B, ec.Pace(), True, live=False) == (failed, False)


def _unavailable(calls):
    return lambda req, timeout: calls.append(1) or _Resp({"isValid": False, "userError": "MS_UNAVAILABLE"})


def test_vies_lookup_no_answer_stays_empty(ec, monkeypatch):
    calls = []
    monkeypatch.setattr(ec.urllib.request, "urlopen", _unavailable(calls))
    rec, asked = ec.vies_lookup(VAT_B, ec.Pace(), False)
    assert parse_vies(rec["payload"])["valid"] is None and len(calls) == 4  # backed off 3 times, then left it empty
    assert ec.vies_lookup(VAT_B, ec.Pace(), False)[1] is False                 # cached
    assert ec.vies_lookup(VAT_B, ec.Pace(), True)[1] is True and len(calls) == 8  # --retry-failed asks again


def test_vies_worker_circuit_breaker(ec, monkeypatch, capsys):
    import queue
    calls = []
    monkeypatch.setattr(ec.urllib.request, "urlopen", _unavailable(calls))
    q, out = queue.Queue(), {}
    for v in ["0000001017" + str(i) for i in range(7)] + [None]:
        q.put(v)
    ec.vies_worker(q, out, False)
    assert len(calls) == 5 * 4 and len(out) == 7 and list(out.values())[5:] == [None, None]  # 2 left unchecked, uncached
    assert "no more live VIES checks" in capsys.readouterr().out


def test_script_helpers(ec):
    assert ec.decode("Società".encode("cp1252"), "text/html; charset=utf-8") == "Società"  # mislabelled latin-1
    assert ec.decode(b'<meta charset="iso-8859-1">Societ\xe0', "text/html").endswith("Società")
    assert ec.decode(b"\xef\xbb\xbfciao", "") == "ciao"
    assert ec.is_html({"content_type": "application/xhtml+xml"}, b"") and not ec.is_html({"content_type": "application/pdf"}, b"<html>")
    assert ec.is_html({"content_type": ""}, b"  <!DOCTYPE html><html>")
    for host in ("www.facebook.com", "m.facebook.com", "maps.google.it", "rossi.business.site", "www.tripadvisor.co.uk", "linktr.ee", "x.com"):
        assert ec.not_own(host), host
    for host in ("rossi.it", "fox.com", "booking-rossi.it", "rossi.altervista.org", "rossi.wixsite.com", "sites.google.com"):
        assert not ec.not_own(host), host
    assert ec._ascii_url("https://caffè.example/chi siamo/") == "https://xn--caff-8oa.example/chi%20siamo/"
