"""Download ARPA Piemonte asbestos layers to data/raw/piemonte_labels.gpkg (T0.2)."""
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from roofwatch.labels import (LAYER_CANDIDATES, LAYER_CHECKED, LAYER_MUNICIPALITIES,
                              PIEMONTE_FS, fetch_arcgis_layer, label_from_esito)

out = Path(__file__).resolve().parents[1] / "data/raw/piemonte_labels.gpkg"
for layer, name in [(LAYER_CHECKED, "checked"), (LAYER_CANDIDATES, "candidates"), (LAYER_MUNICIPALITIES, "municipalities")]:
    feats = fetch_arcgis_layer(f"{PIEMONTE_FS}/{layer}")
    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:32632")
    if "esito_tot" in gdf:
        gdf["label"] = [label_from_esito(e, d) for e, d in zip(gdf.esito_tot, gdf.desc_esito)]
    if "data_sopr" in gdf:
        gdf["data_sopr"] = pd.to_datetime(gdf["data_sopr"], unit="ms", errors="coerce")
    gdf.to_file(out, layer=name, driver="GPKG")
    print(name, len(gdf), gdf.get("label", pd.Series(dtype=str)).value_counts().to_dict())
