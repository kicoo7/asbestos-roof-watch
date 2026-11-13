"""Weekly run (PLAN 4.5, T3.1-T3.4). Idempotent: re-running a date adds nothing and re-flags nothing.

    python run_weekly.py [--date YYYY-MM-DD] [--days 14] [--max-cloud 80] [--notify]

Writes to outputs/: observations.parquet, change_events.parquet, changes_YYYY-MM-DD.csv,
roofs.gpkg, map.html, run_log.json. Every result is "suspected": only an inspection or a
lab test confirms asbestos.
"""
from __future__ import annotations

import argparse
import json
import pickle
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from roofwatch.change import DETECT_PARAMS, detect_changes  # noqa: E402
from roofwatch.extract import COLLECTION, STAC_URL, extract_scene, tile_code  # noqa: E402
from roofwatch.pipeline import clean_labels, feature_matrix, load_all_observations, train_and_score  # noqa: E402
from roofwatch.store import append_observations, load_observations  # noqa: E402
from roofwatch.weekly import record_news, roof_status  # noqa: E402

OUT = ROOT / "outputs"
PROC = ROOT / "data/processed"
RESCORE_DAYS = 30
SUSPECT_THRESHOLD = 0.5
CHANGED_WINDOW_DAYS = 365
OFFICIAL_AC = {"ac", "ac_small_part", "partial", "ac_changed_since_label"}


def fetch_new_scenes(run_date, days, max_cloud, lookups, log):
    from pystac_client import Client

    bbox = list(gpd.read_file(ROOT / "data/pilot_area.geojson").total_bounds)
    start = (run_date - pd.Timedelta(days=days)).date()
    items = [i for i in Client.open(STAC_URL).search(
        collections=[COLLECTION], bbox=bbox, datetime=f"{start}/{run_date.date()}T23:59:59Z",
        query={"eo:cloud_cover": {"lt": max_cloud}}).items() if tile_code(i) in lookups]
    done_log = OUT / "observations.scenes_done.txt"
    done = set(done_log.read_text().split()) if done_log.exists() else set()
    todo = [i for i in items if i.id not in done]
    log["scenes_found"] = [{"id": i.id, "date": i.properties["datetime"][:10],
                            "cloud": round(i.properties["eo:cloud_cover"], 1)} for i in items]
    added, roofs_seen = 0, set()
    for it in todo:
        b, t, shape, lut = lookups[tile_code(it)]
        df = extract_scene(it, b, shape, lut)
        if not df.empty:
            added += append_observations(OUT / "observations.parquet", df)
            roofs_seen |= set(df["roof_id"])
        with open(done_log, "a") as fh:
            fh.write(it.id + "\n")
    log["scenes_processed_this_run"] = [i.id for i in todo]
    log["observation_rows_added"] = int(added)
    return roofs_seen


def scores(run_date, obs, labels, ctx, log):
    path = OUT / "ac_scores.parquet"
    if path.exists():
        old = pd.read_parquet(path)
        if (run_date - pd.Timestamp(old["scored_on"].iloc[0])).days < RESCORE_DAYS:
            log["scores"] = f"reused from {pd.Timestamp(old['scored_on'].iloc[0]).date()}"
            return old.set_index("roof_id")["ac_probability"]
    X = feature_matrix(obs, labels, ctx, run_date)
    lab = labels["clean_label"].reindex(X.index)
    train_idx = X.index[lab.isin(["ac", "none"])]
    y = (lab.reindex(train_idx) == "ac").astype(int).values
    p = train_and_score(X, y, train_idx, labels["comune"].reindex(train_idx).values)
    p.rename_axis("roof_id").reset_index().assign(scored_on=run_date).to_parquet(path, index=False)
    log["scores"] = f"re-scored {len(p)} roofs on {run_date.date()}"
    return p


def write_map(roofs: gpd.GeoDataFrame, run_date, path):
    import folium

    colors = {"changed": "#7b3294", "official_ac": "#d7191c", "suspected": "#fdae61", "not_suspected": "#969696"}
    g = roofs.to_crs(4326).copy()
    g["geometry"] = g.geometry.simplify(0.00002)
    cols = ["roof_id", "comune", "tier", "area_m2", "status", "ac_probability", "view",
            "last_clear_date", "last_change_type", "last_change_date", "geometry"]
    g = g[cols].copy()
    for c in ("last_clear_date", "last_change_date"):
        g[c] = g[c].astype(str).replace({"NaT": "", "None": ""})
    g["ac_probability"] = g["ac_probability"].round(2)
    g["area_m2"] = g["area_m2"].round(0)
    c = g.union_all().centroid
    m = folium.Map(location=[c.y, c.x], zoom_start=12, tiles="CartoDB positron")
    title = (f"<div style='position:fixed;top:10px;left:50px;z-index:9999;background:white;padding:8px;"
             f"border:1px solid #999;font:13px sans-serif'><b>Roof watch, run {run_date.date()}</b><br>"
             "All asbestos results are <b>suspected</b>; only inspection or lab test confirms.<br>"
             + "".join(f"<span style='color:{v}'>&#9632;</span> {k} ({(g.status == k).sum()})<br>" for k, v in colors.items())
             + "</div>")
    m.get_root().html.add_child(folium.Element(title))
    for status in ["not_suspected", "suspected", "official_ac", "changed"]:
        sub = g[g["status"] == status]
        if sub.empty:
            continue
        folium.GeoJson(
            sub, name=f"{status} ({len(sub)})",
            style_function=lambda f, col=colors[status]: {"color": col, "fillColor": col, "weight": 1, "fillOpacity": 0.6},
            tooltip=folium.GeoJsonTooltip(fields=[c for c in cols if c != "geometry"]),
            show=status != "not_suspected",
        ).add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(str(path))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--max-cloud", type=float, default=80)
    ap.add_argument("--notify", action="store_true", help="macOS notification on failure")
    a = ap.parse_args()
    run_date = pd.Timestamp(a.date) if a.date else pd.Timestamp.now().normalize()
    log = {"run_date": str(run_date.date()), "started": pd.Timestamp.now().isoformat(timespec="seconds"), "ok": False}
    t0 = time.time()
    try:
        inv = gpd.read_file(PROC / "roofs_inventory.gpkg", layer="roofs")
        ctx = pd.read_parquet(PROC / "context.parquet")
        lookups = pickle.load(open(PROC / "lookups.pkl", "rb"))

        roofs_seen = fetch_new_scenes(run_date, a.days, a.max_cloud, lookups, log)
        obs = load_observations(OUT / "observations.parquet")
        obs = obs[obs["date"] <= run_date]
        allobs = load_all_observations(OUT)
        allobs = allobs[allobs["date"] <= run_date]

        events = detect_changes(allobs, **DETECT_PARAMS)
        if not events.empty:
            events = events[events["confirmed_on"] <= run_date]
        reg_path = OUT / "change_events.parquet"
        known = pd.read_parquet(reg_path) if reg_path.exists() else None
        register, news = record_news(events, known, run_date)
        register.to_parquet(reg_path, index=False)

        labels = inv.set_index("roof_id")[["label", "label_date", "tier", "comune"]].copy()
        labels["label_date"] = pd.to_datetime(labels["label_date"])
        labels["clean_label"] = clean_labels(labels, events)
        p = scores(run_date, obs, labels, ctx, log)

        recent = register[register["confirmed_on"] >= run_date - pd.Timedelta(days=CHANGED_WINDOW_DAYS)]
        last_ev = register.sort_values("date").groupby("roof_id").last()
        roofs = inv.copy()
        roofs["official_label"] = roofs["label"]
        roofs["ac_probability"] = roofs["roof_id"].map(p)
        roofs["status"] = roof_status(roofs["roof_id"], set(labels.index[labels["clean_label"].isin(OFFICIAL_AC)]),
                                      p, set(recent["roof_id"]), SUSPECT_THRESHOLD).values
        last_clear = allobs.groupby("roof_id")["date"].max()
        roofs["last_clear_date"] = roofs["roof_id"].map(last_clear)
        roofs["view"] = np.where(roofs["last_clear_date"] >= run_date - pd.Timedelta(days=a.days), "clear", "no_clear_view")
        roofs["last_change_type"] = roofs["roof_id"].map(last_ev["change_type"]) if len(last_ev) else None
        roofs["last_change_date"] = roofs["roof_id"].map(last_ev["date"]) if len(last_ev) else pd.NaT
        keep = ["roof_id", "area_m2", "tier", "comune", "ac_probability", "status", "view", "last_clear_date",
                "official_label", "label_date", "last_change_type", "last_change_date", "geometry"]
        roofs = roofs[keep]
        roofs.to_file(OUT / "roofs.gpkg", layer="roofs", driver="GPKG")

        news_out = news.merge(roofs[["roof_id", "comune", "tier", "area_m2", "official_label", "ac_probability"]], on="roof_id", how="left")
        news_out.to_csv(OUT / f"changes_{run_date.date()}.csv", index=False)
        write_map(roofs, run_date, OUT / "map.html")

        log.update({
            "ok": True,
            "roofs_total": int(len(roofs)),
            "roofs_updated_this_run": int(len(roofs_seen)),
            "roofs_no_clear_view": int((roofs["view"] == "no_clear_view").sum()),
            "new_changes": int(len(news)),
            "new_changes_by_type": news["change_type"].value_counts().to_dict() if len(news) else {},
            "status_counts": roofs["status"].value_counts().to_dict(),
        })
    except Exception as e:  # T3.4: failures end up in the log and, optionally, as a notification
        log["error"] = repr(e)
        log["traceback"] = traceback.format_exc()
        if a.notify and sys.platform == "darwin":
            subprocess.run(["osascript", "-e", f'display notification "{repr(e)[:150]}" with title "Roof watch run failed"'])
    log["seconds"] = round(time.time() - t0, 1)
    (OUT / "run_log.json").write_text(json.dumps(log, indent=2, default=str))
    summary = {k: log.get(k) for k in ("run_date", "ok", "roofs_updated_this_run", "roofs_no_clear_view", "new_changes", "error", "seconds")}
    print(json.dumps(summary, default=str))
    return 0 if log["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
