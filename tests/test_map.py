import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_weekly import write_map  # noqa: E402


def test_map_uses_keyless_basemaps_and_offers_satellite(tmp_path):
    roofs = gpd.GeoDataFrame({
        "roof_id": [1], "comune": ["Torino"], "tier": ["A"], "area_m2": [3000.0], "status": ["changed"],
        "ac_probability": [float("nan")], "view": ["clear"], "last_clear_date": [pd.Timestamp("2026-11-01")],
        "last_change_type": ["bright_new_roof"], "last_change_date": [pd.Timestamp("2026-10-05")],
    }, geometry=[box(396000, 4990000, 396060, 4990060)], crs="EPSG:32632")
    out = tmp_path / "map.html"
    write_map(roofs, pd.Timestamp("2026-11-13"), out)
    html = out.read_text()
    assert "basemaps.cartocdn.com" not in html      # needs an API key
    assert "tile.openstreetmap.org" not in html     # blocks pages opened from a local file ("Access blocked")
    assert "World_Street_Map" in html               # keyless street map
    assert "World_Imagery" in html
