"""Building footprints and the join of label points to roofs (PLAN T1.1, T1.3)."""
from __future__ import annotations

import geopandas as gpd
import pandas as pd
import shapely

UTM = "EPSG:32632"


def load_overture(path, crs: str = UTM, sources: list[str] | None = None) -> gpd.GeoDataFrame:
    df = pd.read_parquet(path)
    if sources is not None:
        df = df[df["src"].isin(sources)]
    gdf = gpd.GeoDataFrame(
        df.drop(columns="wkb"), geometry=shapely.from_wkb(df["wkb"].values), crs="EPSG:4326"
    ).to_crs(crs)
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    return gdf.rename(columns={"id": "source_id"}).reset_index(drop=True)


def _match(roofs: gpd.GeoDataFrame, labels: gpd.GeoDataFrame, max_dist_m: float) -> pd.DataFrame:
    """Label index -> roof_id, using containment first, then nearest roof within max_dist_m."""
    if max_dist_m <= 0:
        j = gpd.sjoin(labels[["geometry"]], roofs[["roof_id", "geometry"]], predicate="within")
    else:
        j = gpd.sjoin_nearest(
            labels[["geometry"]], roofs[["roof_id", "geometry"]], max_distance=max_dist_m, how="inner"
        )
    j = j[~j.index.duplicated(keep="first")]
    return j[["roof_id"]]


def label_coverage(roofs: gpd.GeoDataFrame, labels: gpd.GeoDataFrame, max_dist_m: float = 15) -> float:
    if len(labels) == 0:
        return float("nan")
    return len(_match(roofs, labels, max_dist_m)) / len(labels)


def join_labels(
    roofs: gpd.GeoDataFrame,
    labels: gpd.GeoDataFrame,
    max_dist_m: float = 15,
    min_ac_fraction: float = 0.5,
) -> pd.DataFrame:
    """One row per roof: label, label_date, ac_m2.

    Labels: ac, removed, partial (mix of ac and removed, or ARPA 'partial'),
    ac_small_part (AC covers less than min_ac_fraction of the roof),
    candidate (unverified remote-sensing hit), none (no label point nearby).
    """
    m = _match(roofs, labels, max_dist_m).join(labels[["label", "mq_stimati", "data_sopr"]])
    rows = []
    area = roofs.set_index("roof_id")["area_m2"]
    for roof_id, g in m.groupby("roof_id"):
        kinds = set(g["label"])
        ac_m2 = g.loc[g["label"] == "ac", "mq_stimati"].fillna(0).sum()
        if "partial" in kinds or {"ac", "removed"} <= kinds:
            lab = "partial"
        elif "ac" in kinds:
            lab = "ac" if ac_m2 >= min_ac_fraction * area[roof_id] else "ac_small_part"
        elif "removed" in kinds:
            lab = "removed"
        elif "candidate" in kinds:
            lab = "candidate"
        else:
            lab = "unknown"
        rows.append({"roof_id": roof_id, "label": lab, "label_date": g["data_sopr"].max(), "ac_m2": ac_m2})
    out = roofs[["roof_id"]].merge(pd.DataFrame(rows, columns=["roof_id", "label", "label_date", "ac_m2"]), how="left")
    out["label"] = out["label"].fillna("none")
    out["ac_m2"] = out["ac_m2"].fillna(0.0)
    return out


def polygon_cover(roofs: gpd.GeoDataFrame, polys: gpd.GeoDataFrame) -> pd.Series:
    """Fraction of each roof's area covered by the union of `polys` (index = roof_id)."""
    out = pd.Series(0.0, index=roofs["roof_id"].values)
    if len(polys) == 0:
        return out
    polys = polys.set_geometry(shapely.make_valid(polys.geometry.values))
    roofs = roofs.set_geometry(shapely.make_valid(roofs.geometry.values))
    cand = gpd.sjoin(roofs[["roof_id", "geometry"]], polys[["geometry"]], predicate="intersects")
    for rid, idx in cand.groupby("roof_id")["index_right"]:
        roof = roofs.geometry[roofs["roof_id"] == rid].iloc[0]
        covered = shapely.union_all(polys.geometry.loc[idx.unique()].values).intersection(roof).area
        out.loc[rid] = covered / roof.area if roof.area > 0 else 0.0
    return out
