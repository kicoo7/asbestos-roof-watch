from pathlib import Path

import pandas as pd
import pytest

from roofwatch.cadastre import parse_parcels_gml, parse_reference

SAMPLE = Path(__file__).parent / "data/parcels_sample.xml"


def test_parse_reference():
    assert parse_reference("L777_001600.103") == {"comune": "L777", "foglio": 16, "allegato": "0", "sviluppo": "0", "particella": "103"}
    assert parse_reference("B157_0012A0.45")["allegato"] == "A"


def test_parse_parcels_gml_real_sample():
    g = parse_parcels_gml(SAMPLE.read_bytes())
    assert len(g) == 4
    assert set(g["ref"]).issuperset({"L777_001600.103", "L777_001600.105"})
    row = g.set_index("ref").loc["L777_001600.103"]
    assert row["comune"] == "L777" and row["foglio"] == 16 and row["particella"] == "103"
    # coordinates are lat/lon in the GML; geometry must be lon/lat (x = lon ~ 10.08, y = lat ~ 45.33)
    minx, miny, maxx, maxy = g.total_bounds
    assert 10.0 < minx < 10.2 and 45.2 < miny < 45.4
    assert g.crs.to_epsg() == 6706
    assert g.geometry.is_valid.all()


def test_assign_parcels_main_and_secondary():
    import geopandas as gpd
    from shapely.geometry import box

    from roofwatch.cadastre import assign_parcels

    roofs = gpd.GeoDataFrame({"roof_id": [1, 2]}, geometry=[box(0, 0, 10, 10), box(100, 100, 110, 110)], crs=32632)
    parcels = gpd.GeoDataFrame({"ref": ["A_000100.1", "A_000100.2", "A_000100.3"]},
                               geometry=[box(-5, -5, 7, 15), box(7, -5, 20, 15), box(9.5, -5, 10, 15)], crs=32632)
    out = assign_parcels(roofs, parcels, min_share=0.2).set_index("roof_id")
    assert out.loc[1, "parcel_ref"] == "A_000100.1"
    assert abs(out.loc[1, "parcel_share"] - 0.7) < 1e-9
    assert out.loc[1, "other_parcels"] == "A_000100.2"      # 30% share; parcel .3 (5%) ignored
    assert pd.isna(out.loc[2, "parcel_ref"])
