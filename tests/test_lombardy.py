import geopandas as gpd
import pytest
from shapely.geometry import box

from roofwatch.footprints import polygon_cover
from roofwatch.lombardy import decode_older


def test_decode_older_classes():
    assert decode_older("non variato al 2018") == "unchanged"
    assert decode_older("variato con installazione fotovoltaico fra 2007 e 2012") == "pv"
    assert decode_older("variato con demolizione edificio fra 2015 e 2018") == "demolished"
    assert decode_older("variato fra 2012 e 2015 e demolizione edificio") == "demolished"
    assert decode_older("variato fra 2015 e 2018") == "changed"
    assert decode_older(None) == "unknown"


def test_polygon_cover_fraction_of_each_roof():
    roofs = gpd.GeoDataFrame({"roof_id": [0, 1, 2]},
                             geometry=[box(0, 0, 10, 10), box(20, 0, 30, 10), box(40, 0, 50, 10)], crs=32632)
    polys = gpd.GeoDataFrame(geometry=[box(0, 0, 10, 6), box(20, 0, 22, 10), box(22, 0, 24, 10)], crs=32632)
    cover = polygon_cover(roofs, polys)
    assert cover.loc[0] == pytest.approx(0.6)
    assert cover.loc[1] == pytest.approx(0.4)   # two label polygons on one roof add up
    assert cover.loc[2] == 0.0


def test_polygon_cover_overlapping_label_polygons_not_double_counted():
    roofs = gpd.GeoDataFrame({"roof_id": [0]}, geometry=[box(0, 0, 10, 10)], crs=32632)
    polys = gpd.GeoDataFrame(geometry=[box(0, 0, 10, 6), box(0, 0, 10, 6)], crs=32632)
    assert polygon_cover(roofs, polys).loc[0] == pytest.approx(0.6)


def test_polygon_cover_tolerates_invalid_label_polygons():
    from shapely.geometry import Polygon

    roofs = gpd.GeoDataFrame({"roof_id": [0]}, geometry=[box(0, 0, 10, 10)], crs=32632)
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10), (0, 0)])  # self-intersecting
    polys = gpd.GeoDataFrame(geometry=[bowtie, box(0, 0, 10, 5)], crs=32632)
    cover = polygon_cover(roofs, polys)
    assert 0.5 <= cover.loc[0] <= 1.0
