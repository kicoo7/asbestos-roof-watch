from roofwatch.labels import fetch_arcgis_layer, label_from_esito


def test_fetch_arcgis_layer_follows_pages_until_limit_not_exceeded():
    pages = {
        0: {"features": [{"id": 1}, {"id": 2}], "exceededTransferLimit": True},
        2: {"features": [{"id": 3}], "exceededTransferLimit": False},
    }
    calls = []

    def get_json(url, params):
        calls.append(params["resultOffset"])
        return pages[params["resultOffset"]]

    feats = fetch_arcgis_layer("http://x/5", get_json=get_json, page_size=2)
    assert [f["id"] for f in feats] == [1, 2, 3]
    assert calls == [0, 2]


def test_fetch_arcgis_layer_raises_on_service_error():
    import pytest

    with pytest.raises(RuntimeError, match="boom"):
        fetch_arcgis_layer("http://x/5", get_json=lambda u, p: {"error": {"message": "boom"}})


def test_label_from_esito_maps_arpa_piemonte_outcomes():
    assert label_from_esito("1", "Presenza MCA - non bonificato") == "ac"
    assert label_from_esito("3", "Completamente bonificato") == "removed"
    assert label_from_esito("3", "Parzialmente bonificato") == "partial"
    assert label_from_esito("3", "Bonifica in corso") == "partial"
    assert label_from_esito("6", "Da verificare") == "candidate"
