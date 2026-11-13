import geopandas as gpd
from shapely.geometry import box

from roofwatch.extract import assign_home_tiles, snap_bounds


def test_snap_bounds_expands_to_grid():
    assert snap_bounds((101, 199, 349, 401), 20) == (100, 180, 360, 420)


def test_snap_bounds_keeps_aligned_bounds():
    assert snap_bounds((100, 200, 300, 400), 20) == (100, 200, 300, 400)


def test_assign_home_tiles_uses_first_tile_that_fully_contains_roof():
    roofs = gpd.GeoDataFrame(
        {"roof_id": [0, 1, 2, 3]},
        geometry=[box(10, 10, 20, 20), box(95, 10, 105, 20), box(150, 10, 160, 20), box(500, 500, 510, 510)],
        crs="EPSG:32632",
    )
    tiles = {"T1": box(0, 0, 100, 100), "T2": box(90, 0, 200, 100)}
    home = assign_home_tiles(roofs, tiles, priority=["T1", "T2"])
    assert home.tolist() == ["T1", "T2", "T2", None]


def test_tile_extent_from_proj_transform_and_shape():
    from types import SimpleNamespace
    from roofwatch.extract import tile_extent

    asset = SimpleNamespace(extra_fields={"proj:shape": [10980, 10980], "proj:transform": [10, 0, 300000, 0, -10, 5000040]})
    item = SimpleNamespace(assets={"red": asset})
    assert tile_extent(item).bounds == (300000, 4890240, 409800, 5000040)
