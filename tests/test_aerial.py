import numpy as np
import pytest
from shapely.geometry import box

from roofwatch.aerial import ORIGIN, mask_square, mosaic, tile_bounds, tile_res, tiles_for_bounds


def test_tile_res_level_18():
    assert tile_res(18) == pytest.approx(0.5971642835598172)


def test_tile_bounds_and_cover_roundtrip():
    lvl = 18
    b = tile_bounds(lvl, row=100, col=200)
    size = 256 * tile_res(lvl)
    assert b == pytest.approx((ORIGIN + 200 * size, -ORIGIN - 101 * size, ORIGIN + 201 * size, -ORIGIN - 100 * size))
    inner = (b[0] + 1, b[1] + 1, b[2] - 1, b[3] - 1)
    assert tiles_for_bounds(inner, lvl) == [(100, 200)]


def test_tiles_for_bounds_spanning_four_tiles():
    lvl = 18
    b = tile_bounds(lvl, 100, 200)
    straddle = (b[2] - 5, b[1] - 5, b[2] + 5, b[1] + 5)  # corner shared by 4 tiles
    assert sorted(tiles_for_bounds(straddle, lvl)) == [(100, 200), (100, 201), (101, 200), (101, 201)]


def test_mosaic_places_tiles_and_crops_to_bounds():
    lvl = 18
    res = tile_res(lvl)
    b00 = tile_bounds(lvl, 100, 200)

    def fetch(level, row, col):
        return np.full((256, 256, 3), (row - 100) * 10 + (col - 200), dtype=np.uint8)

    # 10 px into tile (100,201) horizontally, full height of row 100
    bounds = (b00[2] - 5 * res, b00[1], b00[2] + 10 * res, b00[3])
    img, transform = mosaic(bounds, lvl, fetch)
    assert img.shape == (256, 15, 3)
    assert (img[:, :5, 0] == 0).all() and (img[:, 5:, 0] == 1).all()
    assert transform.c == pytest.approx(bounds[0]) and transform.f == pytest.approx(bounds[3])


def test_mosaic_missing_tile_is_black():
    lvl = 18
    b = tile_bounds(lvl, 100, 200)
    img, _ = mosaic(b, lvl, lambda l, r, c: None)
    assert img.shape == (256, 256, 3) and img.max() == 0


def test_mask_square_blanks_outside_polygon_and_resizes():
    from affine import Affine
    from shapely.geometry import Polygon

    img = np.full((100, 200, 3), 200, dtype=np.uint8)
    transform = Affine(1, 0, 0, 0, -1, 100)  # row r -> y = 100 - r
    tri = Polygon([(0, 0), (100, 0), (0, 100)])  # lower-left half of the left square
    out = mask_square(img, transform, tri, size=64, margin_frac=0.0)
    assert out.shape == (64, 64, 3)
    assert out[-2, 1].tolist() == [200, 200, 200]  # bottom-left: inside
    assert out[1, -2].tolist() == [0, 0, 0]        # top-right: outside


def test_mask_square_pads_non_square_roofs():
    from affine import Affine

    img = np.full((100, 200, 3), 200, dtype=np.uint8)
    transform = Affine(1, 0, 0, 0, -1, 100)
    out = mask_square(img, transform, box(0, 40, 200, 60), size=50, margin_frac=0.0)  # 200 x 20 strip
    assert out.shape == (50, 50, 3)
    assert out[25, 25].tolist() == [200, 200, 200]
    assert out[2, 25].tolist() == [0, 0, 0]
