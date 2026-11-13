# Results: Turin (Sentinel-2) and Brescia (aerial) pilots

Run date 13 Nov 2026 (Sentinel-2, Turin) and 14 Nov 2026 (aerial, Brescia). Sentinel-2 pilot: 19 municipalities, 546 km2. Labels from ARPA Piemonte. Sentinel-2 L2A from Earth Search (`sentinel-2-c1-l2a`). Numbers come from `scripts/train_eval.py` and `scripts/validate_change.py`. Charts and tables are in `outputs/` (not committed).

## Update 14 Nov 2026: aerial photos pass the gate (Brescia pilot)

Following the plan's fail branch, detection was retried on free aerial orthophotos instead of Sentinel-2.

**Setup**

| | |
|---|---|
| Imagery | Ortofoto AGEA 2024, CC BY 4.0, from AGEA's public image service. Cached tiles at level 18 (~0.42 m on the ground, RGB); full-resolution export returns a placeholder |
| Labels | Lombardy asbestos mapping, 2020 survey (layer 1): 4,207 polygons in 16 Brescia-plain municipalities. Roofs come from Overture footprints inside the surveyed area. A roof counts as AC when AC polygons cover >= 50% of it, and polygons covering 5-50% are excluded |
| Roofs | 26,632 roofs of at least 100 m2 (2,178 AC) |
| Model | DINOv2 ViT-S/14 image features (averaged over 4 rotations) + LightGBM; 5-fold GroupKFold by municipality |

**Asbestos results (held-out municipalities)**

| Size | AC | Base rate | Average precision | Precision @ recall 0.5 | Precision @ recall 0.8 | Mapped AC among top 25 |
|---|---|---|---|---|---|---|
| 100-500 m2 | 1,197 | 0.056 | 0.47 | 0.47 | 0.23 | 21 |
| 500-2,000 m2 | 818 | 0.180 | 0.71 | **0.79** | 0.52 | 24 |
| >= 2,000 m2 | 163 | 0.197 | 0.60 | **0.66** | 0.47 | 18 |

The linear probe gives 0.76 and 0.54 on the two larger bands. LightGBM was chosen on the same folds, which flatters it slightly.

What the numbers mean:
- **Gate:** passes for roofs >= 500 m2 (bar: 0.6 at recall 0.5). Sentinel-2 reached 0.30 on large roofs in Turin. Roofs of 100-500 m2 do not pass, but the top of their ranking is still mostly AC.
- **Measured accuracy is a lower bound.** 23 of the 24 top-scored roofs are mapped AC, and the 24th looks identical. The mapped AC roofs scored lowest are mostly red tiles, new white roofs or solar panels in 2024, i.e. labels gone stale since the 2020 survey. Only 39 AC labels now carry panels (solar detector, score >= 0.9); removing them changes precision by under 0.01 (`outputs/aerial_results_clean.csv`). Most stale labels are therefore replacements by roofs other than solar.
- **Small sample at the top.** 163 AC roofs >= 2,000 m2 is small; 0.66 is uncertain.
- **One area only.** Transfer to other parts of Lombardy, other years or other regions is untested.

**Solar-panel detector**

Labels: Lombardy layer 4. Positives are 1,926 roofs replaced with PV by 2018. Negatives are 3,000 roofs replaced without PV and 3,000 still AC in 2018. 302 municipalities, region-wide.

| Negatives | Average precision | Precision @ recall 0.8 |
|---|---|---|
| replaced, no PV | 0.89 | 0.89 |
| still AC in 2018 | 0.92 | 0.92 |
| both | 0.85 | 0.83 |

The 12 highest-scored "no PV" roofs all show panels in 2024, installed after the 2018 labels. The 12 lowest-scored "PV" roofs show none. So these numbers are also lower bounds.

Applied to the Brescia pilot, 4,418 of 5,781 roofs >= 500 m2 show no panels (about 5.4 km2 of roof).

**Owner and occupant linkage** (`scripts/contact_worksheet.py`, `docs/CONTACTING.md`)

Each candidate roof (score >= 0.7, >= 500 m2) gets three things:
- its cadastral parcel (town code, sheet, parcel) from the Agenzia delle Entrate's open cadastral WFS (CC BY 4.0)
- the nearest address from Overture
- the business located at the building from Overture places

Owner names are not open data. Look them up one at a time with a visura per immobile. Phones and emails are left out on purpose; see `docs/CONTACTING.md`.

Outputs (local, not committed): `outputs/aerial_map.html`, `outputs/contact_worksheet.csv`, `outputs/aerial_top_predictions.png`, `outputs/pv_top_predictions.png`.

## Decision (T1.8): no-go for asbestos detection from Sentinel-2

The bar in PLAN 4.4 was precision >= 0.6 at recall >= 0.5 on held-out municipalities, tier A. The pilot reaches **0.30** (0.23 with uncleaned labels).

Following the plan's fail branch:

- `run_weekly.py` runs **change monitoring on officially mapped roofs** by default.
- The AC classifier runs only with `--with-detection`.
- A yearly detection layer from aerial orthophotos (20 to 50 cm) is the route to look at next, if detection is still wanted.

## Inventory and labels (T1.1 to T1.3)

| | Tier A (>= 2,000 m2) | Tier B (500 to 2,000 m2) |
|---|---|---|
| Roofs | 2,844 | 2,253 |
| AC, ARPA estimate >= 50% of roof, unchanged since label | 97 | 66 |
| AC, changed since label date (dropped by cleaning) | 33 | 28 |
| AC on a small part of the roof (excluded) | 197 | 114 |
| Remediated | 266 | 123 |
| No label point (used as "not AC") | 2,160 | 1,896 |

How the inventory was built:

- **Footprints:** Overture 2026-10-21 (OSM + Microsoft ML). They cover 92.4% of AC label points >= 500 m2 within 15 m, under the 95% target (DATA.md).
- **Dropped roofs:** roofs narrower than about 20 m have no pixel left after the 10 m inward buffer, so they were dropped (2,232 roofs).
- **Label noise:** "no label point" roofs include AC roofs ARPA has not mapped. That noise depresses the measured precision.

## Spectra (T1.5)

`outputs/spectra_ac_vs_other.png`: AC and other roofs overlap heavily. AC roofs are brighter in SWIR, most clearly in winter. The best single feature is `swir22_winter`, with an AUC of 0.71.

## Classifier (T1.7)

LightGBM on per-roof seasonal medians, indices, texture and context. Five-fold GroupKFold by municipality, 3 seeds averaged, scores out of fold.

| Model | Tier | AC | Other | Base rate | Average precision | Precision @ recall 0.5 | Precision @ recall 0.3 |
|---|---|---|---|---|---|---|---|
| all features | A | 97 | 2,160 | 0.043 | 0.357 | **0.299** | 0.588 |
| all features | B | 66 | 1,896 | 0.034 | 0.382 | 0.412 | 0.625 |
| spectral only | A | 97 | 2,160 | 0.043 | 0.367 | 0.321 | 0.600 |
| context only | A | 97 | 2,160 | 0.043 | 0.070 | 0.064 | 0.065 |
| all features, uncleaned labels | A | 130 | 2,160 | 0.057 | 0.291 | 0.226 | 0.557 |
| all features, uncleaned labels | B | 94 | 1,896 | 0.047 | 0.333 | 0.297 | 0.509 |

What the numbers say:

- **Signal is real but weak.** Average precision is 7 to 11 times the base rate. At recall 0.2, precision is about 0.7 (`outputs/pr_curves.png`). So the top of the ranking could still serve as a "check these first" list, but not as a map of AC roofs.
- **Context adds almost nothing.** Building age (GHSL 100 m), area and Overture class add almost nothing beyond the spectra.
- **Cleaning flatters the numbers.** Label cleaning raises the scores (0.23 -> 0.30), partly because it removes positives whose spectra are unstable. Treat the uncleaned row as the honest lower bound.
- **Tier B is a small sample.** It scores higher than tier A, but on 66 positives. The difference is not meaningful at this size.

## Change detection (T2.1 to T2.3)

The detector compares each roof against its own rolling baseline (median of up to 8 earlier clear dates). Before scoring, each band is divided by the median of all roofs in the same scene, which cancels haze, illumination and seasonal shifts.

A change is confirmed only when every clear observation departs in the same direction for at least 10 days. That is stricter than the plan's "2 consecutive dates", because with three satellites consecutive clear dates are often 3 days apart, i.e. the same weather.

How the validation works:

- **Remediated roofs** (ARPA "completamente bonificato", inspected 2019 or later): a roof counts as detected if a change is flagged before its inspection date, plus 60 days.
- **Still-AC roofs** (inspected 2018 or later): any flag before the inspection date is a false alarm.
- **Archive:** clearest scene per tile per month, Apr 2018 to Oct 2025, plus the last 12 months.

| Setting | Detected A | Chance A (still-AC roofs with any flag) | False alarms per 1,000 roofs per year, A | Detected B | Chance B | False alarms, B |
|---|---|---|---|---|---|---|
| raw reflectance, threshold 4 | 0.198 | 0.068 | 23 | 0.185 | 0.041 | 13 |
| normalised, floor 0.10, threshold 3 | 0.500 | 0.164 | 79 | 0.426 | 0.327 | 144 |
| **normalised, floor 0.15, threshold 4, 10 days (chosen)** | **0.267** | **0.041** | **14** | **0.222** | **0.041** | **13** |
| normalised, floor 0.20, threshold 3 | 0.293 | 0.041 | 14 | 0.222 | 0.061 | 20 |

What the numbers say:

- **It separates, but misses most changes.** At the chosen setting, a remediated roof is about 6 times as likely to be flagged as a roof that stayed AC. It still misses most remediations.
- **The detection rate is a lower bound.** The archive starts in April 2018, and some roofs were remediated before that.
- **False-alarm figures are rough.** They rest on 122 still-AC roofs (367 roof-years), so they carry large uncertainty.
- **Change types look plausible.** On detected remediated roofs, 37 of 43 were typed `bright_new_roof`, 3 `dark_pv`, 2 `other` and 1 `vegetation`.
- **Volume.** Across all 5,097 roofs the detector raises 2,804 events over about 7.5 years, roughly 7 per week. Many are on roofs with no label, where real replacements also happen.
- **Next.** The setting is a starting point; tune it on a better label set (see Lombardy below).

## Weekly run (T3.1 to T3.4)

`python run_weekly.py`:

- **What it does:** extracts scenes from the last 14 days, runs change detection over the whole history, and writes `roofs.gpkg`, `changes_YYYY-MM-DD.csv`, `map.html` and `run_log.json`.
- **First run (13 Nov 2026):** listed 39 changes confirmed in the last 60 days (30 `bright_new_roof`, 6 `other`, 2 `demolition_bare`, 1 `dark_pv`). 4,184 of 5,097 roofs had no clear view in the last 14 days (November fog), which is expected.
- **Idempotent:** three runs on the same date gave the same change list, the same register (2,804 events) and no new rows.
- **Safe against overlap and crashes:** one lock covers the weekly run and the extraction scripts. The event register is written last; news from a run that crashed before publishing carries over to the next run.
- **Speed:** about 95 s with no new scenes, plus about 5 to 10 s per new scene.
- **Not done (T3.3):** the launchd job is in `ops/` but not installed, so "two consecutive unattended runs" has not happened yet.

## Lombardy

The plan chose Lombardy. I could not find its data at first, so the pilot moved to Piedmont. Later in the work, a file not produced by this pipeline (`docs/VALIDATION.md`) pointed to a public ArcGIS service:

`https://www.cartografia.servizirl.it/expo/rest/services/gpt/coperture_amianto/MapServer`

I checked it on 13 Nov 2026:

- Layer 4 "Mappature precedenti" holds 50,131 polygons. Its `decodifica` field gives the change type and period: "non variato al 2018" 22,385; "variato fra 2015 e 2018" 5,530; PV and demolition classes with periods.
- Layer 1 "Mappatura 2020" holds the 2020 mapping.
- The service shows no licence. Check the Geoportale metadata before use.

Polygons that follow the regional topographic database avoid the point-to-footprint matching losses seen in Piedmont. The 2015 to 2018 change classes overlap the Sentinel-2 archive, which makes them a much better test of the change detector than the Piedmont inspection dates. The code is region-agnostic: only a label adapter for this service and a new pilot area are needed.

## Not done

- **T3.3:** install the schedule (needs your go-ahead; it changes `~/Library/LaunchAgents`).
- **Phase 4 (whole region):** reads would need per-cluster windows instead of one window per tile. A region-wide 12-month extraction is several hours and many GB.
- **Phase 5 (transfer and national scale):** not started. T5.4 (publication and legal review) is a decision for you.
