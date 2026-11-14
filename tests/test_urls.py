from roofwatch.urls import normalize_domain, public_website, safe_http_url


def test_normalize_domain():
    assert normalize_domain("http://www.Example.it/contatti?x=1") == "example.it"
    assert normalize_domain("https://shop.example.it:8443/") == "shop.example.it"
    assert normalize_domain("www.example.it") == "example.it"
    assert normalize_domain("example.it.") == "example.it"
    for bad in (None, "", "   ", float("nan"), "localhost", "http://", "http://[bad"):
        assert normalize_domain(bad) is None


def test_safe_http_url():
    assert safe_http_url("https://example.it/a") == "https://example.it/a"
    assert safe_http_url("example.it") == "http://example.it"
    for bad in (None, "", "javascript:alert(1)", "javascript://example.it/%0aalert(1)", "data:text/html,x", "ftp://example.it", "http://nohost"):
        assert safe_http_url(bad) is None


def test_public_website_keeps_sites_and_drops_contact_details():
    assert public_website("https://www.rossi-impianti.example/chi-siamo") == "https://www.rossi-impianti.example/chi-siamo"
    assert public_website("rossi.example") == "http://rossi.example"
    assert public_website("https://shop.example/it/?utm_source=x&tel=0301234567#top") == "https://shop.example/it/"
    assert public_website("https://www.facebook.com/RossiImpianti") == "https://www.facebook.com/RossiImpianti"  # a page, not a phone
    assert public_website("http://example.it/2024/10/notizie") == "http://example.it/2024/10/notizie"          # dates are not phones
    for bad in ("https://wa.me/393331234567", "https://api.whatsapp.com/send?phone=393331234567", "http://t.me/rossi", "https://m.me/rossi",
                "http://info@rossi.example/", "https://0301234567.example/", "javascript:alert(1)", "", None):
        assert public_website(bad) is None, bad
    # a contact detail in the path only: the site root is kept, the detail is not
    for path in ("contatti/info@rossi.example", "tel/030-1234567", "info%40rossi.example", "info%2540rossi.example", "tel/(030)1234567",
                 "333%20123%204567", "info(at)rossi.example", "info\uff20rossi.example", "p/100064832719455",
                 "333%C2%A0123%C2%A04567", "info%5Bat%5Drossi.example", "info%EF%BC%A0rossi.example"):
        assert public_website("https://rossi.example/" + path) == "https://rossi.example/", path
