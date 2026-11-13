"""Roof inventory: size tiers, inward buffer and the Sentinel-2 pixel lookup (PLAN 4.1)."""
from __future__ import annotations

import geopandas as gpd
import numpy as np
from affine import Affine
from rasterio import features

TIER_A_M2 = 2000
TIER_B_M2 = 500


def assign_tier(area_m2: float) -> str | None:
    if area_m2 >= TIER_A_M2:
        return "A"
    if area_m2 >= TIER_B_M2:
        return "B"
    return None


def build_inventory(footprints: gpd.GeoDataFrame, inner_buffer_m: float = 10) -> gpd.GeoDataFrame:
    """Keep roofs of at least 500 m2 and add the inward-buffered sampling polygon.

    `footprints` must be in a metric CRS. Roofs whose sampling polygon is empty are dropped.
    """
    inv = footprints.copy()
    inv["area_m2"] = inv.geometry.area
    inv["tier"] = inv["area_m2"].map(assign_tier)
    inv = inv[inv["tier"].notna()].copy()
    inv["sample_geom"] = inv.geometry.buffer(-inner_buffer_m)
    inv = inv[~inv["sample_geom"].is_empty & (inv["sample_geom"].area > 0)]
    inv = inv.reset_index(drop=True)
    inv["roof_id"] = np.arange(len(inv), dtype=np.int64)
    return inv


def pixel_lookup(geoms, transform: Affine, shape: tuple[int, int]) -> list[tuple[np.ndarray, np.ndarray]]:
    """For each geometry, the (rows, cols) of grid pixels whose centres fall inside it."""
    if len(geoms) == 0:
        return []
    labels = features.rasterize(
        ((g, i + 1) for i, g in enumerate(geoms)),
        out_shape=shape,
        transform=transform,
        fill=0,
        all_touched=False,
        dtype="int32",
    )
    flat = labels.ravel()
    order = np.argsort(flat, kind="stable")
    sorted_labels = flat[order]
    out = []
    for i in range(len(geoms)):
        lo, hi = np.searchsorted(sorted_labels, [i + 1, i + 2])
        idx = order[lo:hi]
        out.append(np.unravel_index(idx, shape))
    return out
