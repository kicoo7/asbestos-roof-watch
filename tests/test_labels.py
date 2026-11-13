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


def test_fetch_arcgis_layer_halves_page_size_when_server_fails():
    data = [{"id": i} for i in range(5)]
    sizes = []

    def get_json(url, params):
        n, off = params["resultRecordCount"], params["resultOffset"]
        sizes.append(n)
        if n > 2:
            return {"error": {"message": "Failed to execute query."}}
        batch = data[off:off + n]
        return {"features": batch, "exceededTransferLimit": off + n < len(data)}

    feats = fetch_arcgis_layer("http://x/1", get_json=get_json, page_size=8)
    assert [f["id"] for f in feats] == [0, 1, 2, 3, 4]
    assert sizes[:3] == [8, 4, 2]


def test_esri_rings_to_shapely_with_hole_and_two_parts():
    from roofwatch.labels import esri_rings_to_shapely

    outer = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]      # clockwise = exterior in Esri JSON
    hole = [[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]]           # counter-clockwise = hole
    second = [[20, 0], [20, 5], [25, 5], [25, 0], [20, 0]]    # another exterior
    g = esri_rings_to_shapely([outer, hole, second])
    assert g.geom_type == "MultiPolygon"
    assert abs(g.area - (100 - 4 + 25)) < 1e-9


def test_esri_rings_single_polygon():
    from roofwatch.labels import esri_rings_to_shapely

    g = esri_rings_to_shapely([[[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]])
    assert g.geom_type == "Polygon" and g.area == 100
