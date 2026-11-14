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

Aerial pilot (Brescia), after `pip install -e ".[aerial]"`:

```sh
python scripts/lombardy_build.py        # roofs and 2020 labels -> data/processed/lombardy_roofs.gpkg
python scripts/fetch_aerial_chips.py    # AGEA 2024 image chip per roof
python scripts/embed_chips.py           # DINOv2 embeddings
python scripts/train_aerial.py          # asbestos scores -> outputs/aerial_oof.parquet
python scripts/pv_dataset.py && python scripts/embed_chips.py --chips data/processed/pv_chips --out data/processed/pv_chip_embeddings.parquet
python scripts/train_pv.py && python scripts/score_pv_pilot.py          # solar-panel scores
python scripts/contact_worksheet.py     # parcel, address, businesses per roof -> worksheet, roof_occupants, roof_addresses
python scripts/enrich_companies.py      # company public record per business website (about 40 min the first time, then cached)
python scripts/aerial_map.py            # outputs/aerial_map.html: click a roof for the owner and occupant panel
```

Open `outputs/aerial_map.html` from the `outputs/` folder on the machine that holds `data/processed/chips/`. `#roof=<id>` in the address opens one roof. How to use the panel and the worksheet: [docs/CONTACTING.md](docs/CONTACTING.md).

## Layout

| Path | Contents |
|---|---|
| `docs/` | Plan, `DATA.md` (sources, fields, licences), `RESULTS.md` (classifier, go/no-go, change validation) |
| `src/roofwatch/` | Python package: labels, footprints, inventory, extract, observe, store, features, model, change, weekly, pipeline; aerial pilot: aerial, lombardy, cadastre, occupants, company, panel, urls |
| `scripts/` | One script per plan step |
| `run_weekly.py` | Weekly entry point |
| `ops/` | launchd job |
| `notebooks/` | Exploration |
| `data/raw/`, `data/processed/` | Input data, not committed |
| `outputs/` | `roofs.gpkg`, `changes_*.csv`, `observations.parquet`, `map.html`, `run_log.json`; aerial pilot: `aerial_map.html`, `contact_worksheet.csv`, `roof_occupants.csv`, `roof_addresses.csv`, `company_register.csv`. Not committed |

## Status (13 Nov 2026)

- Phases 0 to 3 are built and run on the Turin pilot. Results and the go/no-go decision are in [docs/RESULTS.md](docs/RESULTS.md).
- **No-go for asbestos detection** (tier A precision 0.30 at recall 0.5, bar 0.6). The weekly job therefore runs change monitoring on officially mapped roofs; `--with-detection` turns the classifier back on.
- **Aerial pilot (Brescia, 14 Nov 2026):** detection from AGEA aerial photos passes the gate for roofs of 500 m2 or more. The map links each roof to its parcel, address, businesses and their public company records; owner names still need a visura.
- Not done: installing the schedule (`ops/install_schedule.sh`), Phase 4 (whole region) and Phase 5 (other regions).
