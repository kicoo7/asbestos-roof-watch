"""T1.6: building-age (GHSL first built epoch) and context per roof -> data/processed/context.parquet."""
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import rasterio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from roofwatch.features import first_built_year

inv = gpd.read_file(ROOT / "data/processed/roofs_inventory.gpkg", layer="roofs")
pts = inv.representative_point().to_crs("ESRI:54009")
xy = list(zip(pts.x, pts.y))
built = {}
for y in range(1975, 2021, 5):
    with rasterio.open(next((ROOT / "data/raw/ghsl").glob(f"GHS_BUILT_S_E{y}_*.tif"))) as src:
        built[y] = [v[0] for v in src.sample(xy)]
built = pd.DataFrame(built, index=inv.index)
ctx = inv[["roof_id", "area_m2", "tier", "comune", "class", "subtype"]].copy()
ctx["built_year"] = first_built_year(built).values
ctx["built_2020_m2"] = built[2020].values
ctx["compactness"] = (4 * 3.14159 * inv.area / inv.length ** 2).values
ctx.to_parquet(ROOT / "data/processed/context.parquet", index=False)
print(ctx["built_year"].value_counts(dropna=False).sort_index().to_dict())
lab = inv.set_index("roof_id")["label"]
print(pd.crosstab(lab.reindex(ctx.roof_id).values, ctx["built_year"].fillna(0).values >= 1995, rownames=["label"], colnames=["built>=1995"]))
