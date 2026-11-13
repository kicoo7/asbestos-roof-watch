# Asbestos roof watch

Weekly Sentinel-2 monitor of large roofs in Lombardy that are suspected to be asbestos-cement, with a list of the roofs that changed since the last run.

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

## Layout

| Path | Contents |
|---|---|
| `docs/` | Plan, `DATA.md` (T0.2), `RESULTS.md` (T1.7, T1.8) |
| `src/roofwatch/` | Python package |
| `notebooks/` | Exploration |
| `data/raw/`, `data/processed/` | Input data, not committed |
| `outputs/` | `roofs.gpkg`, `changes_*.csv`, `observations.parquet`, `map.html`, `run_log.json`. Not committed |

## Progress

Phase 0, setup:
- [x] T0.1 Repo and Python environment
- [ ] T0.2 ARPA Lombardia AC roof dataset documented in `docs/DATA.md`
- [ ] T0.3 Earth Search scene query for one Lombardy tile
- [ ] T0.4 Pilot area GeoJSON
