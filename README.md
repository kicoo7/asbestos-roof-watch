# Asbestos roof watch

Weekly Sentinel-2 monitor of large roofs that are suspected to be asbestos-cement, with a list of the roofs that changed since the last run. The pilot runs on 19 municipalities around Turin (Piedmont), because Piedmont's official asbestos map is open data and Lombardy's is not (see [docs/DATA.md](docs/DATA.md)).

The full plan is in [docs/PLAN.md](docs/PLAN.md). Every result is "suspected": only an inspection or a lab test confirms asbestos.

## Setup

Requires Python 3.11 to 3.13. Geospatial wheels may not exist yet for newer versions.

```sh
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -c "import rasterio, geopandas, pystac_client"
```

On macOS, LightGBM also needs `brew install libomp`.

## Running it

```sh
python scripts/fetch_labels.py                       # ARPA Piemonte labels -> data/raw/
# Overture buildings and GHSL tiles: see docs/DATA.md for the exact sources
python scripts/build_inventory.py                    # roofs, tiers, labels, pixel lookups
python scripts/context_features.py                   # building age and context
python scripts/extract_observations.py 2025-11-01 2026-11-13            # 12 months
python scripts/extract_observations.py 2017-01-01 2025-10-31 --max-cloud 30 --best-per-month \
    --out outputs/observations_archive.parquet       # archive for change validation
python scripts/train_eval.py                         # T1.3-T1.8 -> outputs/, docs/RESULTS.md numbers
python scripts/validate_change.py                    # T2.2
python run_weekly.py                                 # the weekly job
pytest -q
```

`ops/install_schedule.sh` installs a launchd job that runs `run_weekly.py` on Mondays at 07:00.

## Layout

| Path | Contents |
|---|---|
| `docs/` | Plan, `DATA.md` (sources, fields, licences), `RESULTS.md` (classifier, go/no-go, change validation) |
| `src/roofwatch/` | Python package: labels, footprints, inventory, extract, observe, store, features, model, change, weekly, pipeline |
| `scripts/` | One script per plan step |
| `run_weekly.py` | Weekly entry point |
| `ops/` | launchd job |
| `notebooks/` | Exploration |
| `data/raw/`, `data/processed/` | Input data, not committed |
| `outputs/` | `roofs.gpkg`, `changes_*.csv`, `observations.parquet`, `map.html`, `run_log.json`. Not committed |
