import geopandas as gpd
import numpy as np
from affine import Affine
from shapely.geometry import box

from roofwatch.inventory import assign_tier, build_inventory, pixel_lookup

UTM = "EPSG:32632"


def test_assign_tier_thresholds():
    assert assign_tier(2000) == "A"
    assert assign_tier(1999.9) == "B"
    assert assign_tier(500) == "B"
    assert assign_tier(499) is None


def test_build_inventory_drops_small_roofs_and_buffers_inward():
    fp = gpd.GeoDataFrame(
        {"source_id": ["big", "mid", "small"]},
        geometry=[box(0, 0, 60, 60), box(100, 0, 130, 30), box(200, 0, 210, 10)],
        crs=UTM,
    )
    inv = build_inventory(fp, inner_buffer_m=10)
    assert list(inv["source_id"]) == ["big", "mid"]
    assert list(inv["tier"]) == ["A", "B"]
    assert inv.loc[0, "area_m2"] == 3600
    # 60x60 shrunk by 10 m each side -> 40x40
    assert abs(inv.loc[0, "sample_geom"].area - 1600) < 1e-6
    assert inv["roof_id"].is_unique


def test_build_inventory_drops_roofs_that_vanish_after_buffer():
    # 500 m2 but only 10 m wide: nothing left after a 10 m inward buffer
    fp = gpd.GeoDataFrame({"source_id": ["thin"]}, geometry=[box(0, 0, 50, 10)], crs=UTM)
    assert len(build_inventory(fp, inner_buffer_m=10)) == 0


def test_pixel_lookup_returns_pixels_whose_centres_fall_inside():
    # 10 m grid starting at (0, 100), 10x10 pixels
    transform = Affine(10, 0, 0, 0, -10, 100)
    geoms = [box(0, 70, 20, 100), box(50, 0, 60, 10)]
    lut = pixel_lookup(geoms, transform, (10, 10))
    rows, cols = lut[0]
    assert sorted(zip(rows.tolist(), cols.tolist())) == [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)]
    assert list(zip(*lut[1])) == [(9, 5)]


def test_pixel_lookup_empty_for_roof_outside_grid():
    transform = Affine(10, 0, 0, 0, -10, 100)
    lut = pixel_lookup([box(500, 500, 520, 520)], transform, (10, 10))
    assert lut[0][0].size == 0
