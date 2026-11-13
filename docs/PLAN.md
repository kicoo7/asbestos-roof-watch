# Asbestos roof watch — Italy (pilot: Lombardy)

Plan v1, 13 Nov 2026. Status: reviewed, ready to start at task T0.1. No code has been written yet.

## 1. Goal

Every week, read the newest free satellite imagery, keep a map of large roofs that are probably asbestos-cement (AC, "eternit"), and produce a list of roofs that changed since the last run (removed, replaced, covered with solar panels, demolished).

Decisions already taken:

| Question | Decision |
|---|---|
| Imagery | Free only: Sentinel-2 L2A. Imagery up to 2 weeks old is acceptable. |
| Area | One region first, then scale to Italy. This plan proposes **Lombardy** (see 3.1). |
| Weekly output | Updated map + change list. |
| First build | Working Python prototype (to be built by Krste from the tasks in section 8). |

## 2. What is realistic (read this first)

**Sentinel-2 cannot see small roofs.** Pixels are 10 m (visible, NIR) and 20 m (red-edge, SWIR). A roof needs to be about 30 x 30 m to contain a few clean 10 m pixels, and about 40 x 40 m for one clean 20 m pixel. So the project covers warehouses, factories, farm sheds and similar. Houses are out of scope with free satellite data.

**Nobody has shown that Sentinel-2 alone identifies asbestos reliably.** The published work uses much sharper data: airborne hyperspectral (about 97% accuracy for AC), WorldView-3 at 1.2 m (about 74% in one study, about 93% F1 with deep learning in a Lombardy thesis), or 5 to 40 cm aerial photos with CNNs (above 90%). I found no Sentinel-2-only study with comparable results. The main confusions are concrete, asphalt, gravel roofs and, hardest of all, post-1992 fibre-cement sheets that look the same but contain no asbestos.

**What Sentinel-2 is good at is change.** A grey weathered roof turning into bright new metal panels or dark solar panels is a large, persistent spectral jump, easy to detect at 10 m. This is the part where a weekly run adds real value.

So the design is:

1. **Where** the roofs are and their outlines: taken from existing building footprints, not detected from satellite.
2. **Which** roofs are probably AC: a classifier trained on official regional asbestos maps, using Sentinel-2 spectra plus context (building age, size, land use). Output is a probability, never a confirmation.
3. **What changed** this week: per-roof time series and change detection.

Phase 1 is a go/no-go test of step 2. If Sentinel-2 cannot separate AC roofs well enough, the project still works as a **change monitor on the officially mapped AC roofs**, which is useful on its own.

Only an on-site inspection or lab test confirms asbestos. Every output must say "suspected".

## 3. Data

### 3.1 Why Lombardy

- ARPA Lombardia mapped AC roofs in 2007 (airborne hyperspectral MIVIS) and updated in 2012 with AGEA aerial photos, over an east-west strip holding about 30% of the regional population. Published as open data on the regional geoportal.
- The labels already carry the change classes this project wants: unchanged, removed, removed + photovoltaic, removed by demolition. That gives training data for both the classifier and the change detector.
- A 2024 Politecnico di Milano thesis used these same labels for satellite classification, so the route is proven.
- Very high density of large industrial and agricultural roofs.

Alternative: **Piedmont**. ARPA Piemonte has about 57,000 mapped potential or partially remediated roofs and about 12,500 remediated ones, updated daily from field checks, with download plus WMS/WFS. Fresher labels than Lombardy, and a good second region for testing whether the model transfers.

### 3.2 Sources

| Data | Source | Notes |
|---|---|---|
| Imagery | Sentinel-2 L2A (surface reflectance) | Revisit about 5 days, so several passes per 2-week window. |
| Imagery access, option A | Element84 Earth Search STAC on AWS | No account. Cloud-optimised GeoTIFFs, so only roof pixels are read, no full tile downloads. |
| Imagery access, option B | Copernicus Data Space (CDSE) STAC / S3 | Free account. Quota: 12 TB per 30 days, 4 connections, plus 10,000 openEO credits per month. |
| AC labels | ARPA Lombardia "Mappatura delle coperture in cemento-amianto" | Geoportale Lombardia. Verify format, date, licence (T0.2). |
| AC labels, second region | ARPA Piemonte asbestos portal open data | Download + WMS/WFS. Licence not stated on the pages read. |
| Building footprints | Regional topographic database (DBT Lombardia), or Overture / OpenStreetMap | Pick the one with best coverage of industrial buildings (T1.1). |
| Building age | GHSL built-up epochs (JRC) or historical orthophotos | Asbestos was banned in Italy in 1992 (Law 257/1992). A building that first appears after about 1994 is very unlikely to have an AC roof. Strong, cheap feature. |

### 3.3 Known data pitfalls

- **Labels are old** (2007/2012 for Lombardy; Piedmont remote-sensing points reflect 2018). Many labelled roofs have been removed since. Treat labels as "was AC at date X" and clean them before training (T1.3).
- **Sentinel-2 reflectance offset.** Scenes processed with baseline 04.00 or later (from January 2022) carry a +1000 offset in the digital numbers. Mixing old and new scenes without harmonising creates fake changes.
- **Clouds, shadow, snow, haze.** Use the SCL mask and require more than one clear observation before flagging anything. The Po valley has long winter fog periods, so some weeks will produce no update for many roofs. This is normal, report it as "no clear view".
- **Geolocation.** Sentinel-2 is accurate to roughly one pixel. Shrink each footprint inward by about 10 m before sampling so edge pixels do not pollute the roof signal.

## 4. Method

### 4.1 Roof inventory (one-off, refreshed occasionally)

1. Load footprints for the region, compute area and shape.
2. Keep roofs in two tiers: **A** at least 2,000 m2 (all bands usable), **B** 500 to 2,000 m2 (10 m bands only, lower confidence). Drop everything smaller.
3. Buffer inward, record which Sentinel-2 pixels fall inside each roof, and store that lookup so weekly runs are fast.
4. Join labels and building-age data.

### 4.2 Features per roof

- Median reflectance of clear pixels per band, from seasonal cloud-free composites (summer and winter) of the last 12 months.
- Indices: brightness, NDVI (moss and lichen on old AC), SWIR ratios, visible slope.
- Temporal: seasonal amplitude, wet versus dry behaviour.
- Texture and within-roof variance (10 m bands).
- Context: area, tier, building age, land-use class.

### 4.3 Classifier

- Start simple: gradient-boosted trees on the per-roof feature table. A CNN on image chips is a later option, not needed for the go/no-go.
- Split train and test **by municipality**, not at random, or scores will be inflated by neighbouring look-alike roofs.
- Report precision and recall per tier, plus a precision-recall curve. Overall accuracy is misleading because AC roofs are a minority.

### 4.4 Go/no-go gate (end of Phase 1)

Proposed bar, adjust once the first numbers exist: on held-out municipalities, tier A roofs, **precision at least 0.6 at recall at least 0.5**.

- Pass: continue with full region detection + change monitoring.
- Fail: drop detection, keep change monitoring on officially mapped roofs, and consider aerial orthophotos (AGEA / regional, 20 to 50 cm, free) as a yearly detection layer.

### 4.5 Weekly run

1. Query scenes from the last 14 days over the region, cloud cover under a threshold.
2. For each roof, read its pixels, mask with SCL, harmonise the offset, store one row per roof per date (reflectances + clear-pixel count).
3. Update each roof's rolling baseline.
4. Change rule: a roof is flagged when its spectrum departs from its own baseline by more than a threshold on **at least 2 consecutive clear dates**. Classify the change type from the new spectrum (bright metal, dark PV, bare ground / demolition, vegetation).
5. Re-score AC probability monthly or quarterly, not weekly. Roof material does not change that fast and weekly re-scoring only adds noise.
6. Write outputs (section 5).

Expected confirmation delay for a real change: 2 to 6 weeks depending on clouds.

## 5. Outputs

- `roofs.gpkg` — one polygon per roof: id, area, tier, municipality, AC probability, status (suspected / official AC / changed / no clear view), last clear date.
- `changes_YYYY-MM-DD.csv` — roofs whose status changed this run, with change type, before and after values, dates.
- `observations.parquet` — append-only per-roof per-date table (the project's memory).
- `map.html` — interactive map with filters by status and probability.
- `run_log.json` — scenes used, roofs updated, roofs skipped for cloud, errors.

## 6. Architecture and operations

- Python: `pystac-client`, `rasterio`, `geopandas`, `numpy`, `scikit-learn` or `lightgbm`, `pyarrow`. Map with `folium` or MapLibre.
- Storage is small: per-roof tables, no archived rasters. A few GB for the region.
- Scheduler: GitHub Actions weekly cron (free, runs without your laptop) or `launchd` on the Mac.
- Runs must be idempotent: re-running a week must not duplicate rows or re-flag old changes.
- Note: the Claude cloud workspace used to write this plan blocks the imagery hosts and PyPI, so development and the weekly job need to run on your machine or on CI.

## 7. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Sentinel-2 cannot separate AC from concrete or non-asbestos fibre cement | Detector unusable | Go/no-go gate; building-age feature; fallback to change monitoring only |
| Stale labels | Model learns wrong examples | Clean labels with a current-imagery check (T1.3) |
| False change alarms from clouds, shadow, snow, wet roofs | Noisy weekly list | 2 consecutive clear dates, SCL mask, per-roof baseline |
| Footprints missing or misaligned | Roofs skipped or polluted | Compare sources, inward buffer |
| Privacy and reputational harm from naming buildings as "asbestos" | Legal exposure | Keep building-level results private; publish only aggregates; label everything "suspected"; check data licences |
| Model does not transfer to other regions | Italy scale-up stalls | Test on Piedmont before scaling |

## 8. Tasks

Each task has a "done when" so progress is checkable. Order matters within a phase.

### Phase 0 — Setup (about half a day)

- [ ] **T0.1** Create repo and Python environment; install the libraries in section 6. *Done when:* `import rasterio, geopandas, pystac_client` works.
- [ ] **T0.2** Download the ARPA Lombardia AC roof dataset from the Geoportale; record format, CRS, attribute meanings, reference dates, licence. *Done when:* a short `DATA.md` documents these and the file opens in QGIS.
- [ ] **T0.3** Choose imagery access (start with Earth Search, no account) and confirm a search for one Lombardy tile over the last 14 days returns scenes. *Done when:* a scene list prints with dates and cloud cover.
- [ ] **T0.4** Pick a pilot area inside the labelled strip (suggestion: 3 to 5 municipalities with many industrial roofs, for example around Brescia or Bergamo). *Done when:* pilot boundary saved as GeoJSON.

### Phase 1 — Feasibility on the pilot area (about 1 week)

- [ ] **T1.1** Compare footprint sources (DBT, Overture, OSM) on the pilot; pick one. *Done when:* chosen source covers at least 95% of labelled AC roofs above 500 m2.
- [ ] **T1.2** Build the roof inventory: area filter, tiers, inward buffer, pixel lookup. *Done when:* `roofs.gpkg` exists with tier counts reported.
- [ ] **T1.3** Join labels to footprints and clean them: drop roofs that visibly changed since the label date (use current free orthophoto or a simple Sentinel-2 before/after check). *Done when:* a labelled table with AC / not AC / removed and counts per class.
- [ ] **T1.4** Extract 12 months of Sentinel-2 per-roof observations with SCL masking and offset harmonisation. *Done when:* `observations.parquet` has at least 10 clear dates for most tier A roofs.
- [ ] **T1.5** Plot mean spectra of AC versus other roof types. *Done when:* a chart shows whether the classes separate at all. If they overlap completely, stop and go to the fallback.
- [ ] **T1.6** Add building-age and context features.
- [ ] **T1.7** Train baseline classifier with municipality-level split; report precision, recall, PR curve per tier. *Done when:* results table written to `RESULTS.md`.
- [ ] **T1.8** **Go/no-go decision** against section 4.4. *Done when:* decision and reasoning recorded in `RESULTS.md`.

### Phase 2 — Change detection (about 3 to 4 days)

- [ ] **T2.1** Build per-roof baseline and the 2-consecutive-dates change rule.
- [ ] **T2.2** Validate on the label classes "removed", "removed + PV", "demolished" using archive imagery from 2017 onward. *Done when:* detection rate and false-alarm rate per 1,000 roofs per year are reported.
- [ ] **T2.3** Add change-type classification (metal, PV, demolition, other).

### Phase 3 — Weekly pipeline (about 3 to 4 days)

- [ ] **T3.1** Single entry point `run_weekly.py` implementing section 4.5, idempotent.
- [ ] **T3.2** Write the outputs in section 5, including `map.html`.
- [ ] **T3.3** Schedule it (GitHub Actions cron or `launchd`). *Done when:* two consecutive unattended runs succeed.
- [ ] **T3.4** Add a run summary (roofs updated, skipped for cloud, new changes) and a failure alert.

### Phase 4 — Whole region (about 1 week)

- [ ] **T4.1** List the Sentinel-2 tiles covering Lombardy (expect roughly 8 to 10) and scale the inventory to the region.
- [ ] **T4.2** Run detection outside the labelled strip; manually check a random sample of 100 flagged roofs on orthophotos. *Done when:* real-world precision estimate is recorded.
- [ ] **T4.3** Measure run time and storage; optimise only if a weekly run exceeds about 1 hour.

### Phase 5 — Towards Italy (later)

- [ ] **T5.1** Test the Lombardy model on Piedmont using ARPA Piemonte labels, with no retraining. Record the drop in precision and recall.
- [ ] **T5.2** Retrain with both regions; decide whether a single national model is viable.
- [ ] **T5.3** Collect label sources for other regions and a national footprint source.
- [ ] **T5.4** Decide on publication format and legal review before anything is shared outside the project.

## 9. Review notes

Self-review of the first draft, and what changed as a result:

1. **Original framing was "detect asbestos roofs from satellite every week".** The evidence does not support Sentinel-2 as a stand-alone asbestos detector, and roofs do not change weekly. Reframed as inventory + probability + change monitoring, with an explicit go/no-go gate and a fallback that still delivers value.
2. **Roof outlines were going to be detected from imagery.** At 10 m that is poor. Replaced with existing building footprints.
3. **Label age was ignored.** Added label cleaning (T1.3) and treated it as a top risk.
4. **Random train/test split would have overstated accuracy.** Changed to split by municipality.
5. **Weekly AC re-scoring removed**; only change detection runs weekly.
6. **Added** the 1992 ban as a building-age feature, the Sentinel-2 offset pitfall, and the privacy risk.
7. **Open points I could not verify and that T0.2 / T0.3 must settle:** exact download link, format, latest reference date and licence of the Lombardy dataset (the INSPIRE catalogue page could not be read; the 2018 date comes from the thesis); licence of the Piedmont data; exact Sentinel-2 tile list for Lombardy; current Earth Search collection name. The go/no-go threshold in 4.4 is my proposal, not an established standard.
8. Time estimates are rough and assume part-time solo work.

## 10. Sources

- [ARPA Lombardia AC roof mapping, 2007 and 2012 results](https://www.quotidianosicurezza.it/approfondimenti/amianto-lotta/dati-mappature-amianto-lombardia-coperture.htm)
- [Lombardy regional asbestos plan, annex 3: MIVIS remote-sensing method](https://www.ats-valpadana.it/documents/1654672/20630563/PRAL+Allegato+3+-+Mappatura+mediante+telerilevamento+delle+coperture+in+cemento+amianto.pdf/96303c8b-b543-285e-3f46-8aae564b1a66)
- [Geoportale Lombardia, data download](https://www.geoportale.regione.lombardia.it/download-dati)
- [Mazzola 2024, Politecnico di Milano thesis: deep learning on WorldView-3 / Pléiades Neo with ARPA Lombardia labels](https://www.politesi.polimi.it/retrieve/5eeb2913-ced7-4184-9ccf-4585e44657b9/2024_12_Mazzola_Thesis_01.pdf)
- [ARPA Piemonte: rapid mapping of potential asbestos roofs](https://www.arpa.piemonte.it/notizia/pubblicata-mappatura-speditiva-delle-coperture-potenzialmente-contenenti-amianto)
- [Piedmont state of the environment 2026: AC roof mapping status](https://relazione.ambiente.piemonte.it/2026/mappatura-delle-coperture-cemento-amianto)
- [ARPA Piemonte asbestos portal](https://webgis.arpa.piemonte.it/portale_amianto/amiantoantropico)
- [Ecoscienza 2018: the Piedmont asbestos mapping project](https://arpae.it/it/ecoscienza/numeri-ecoscienza/anno-2018/numero-1-anno-2018/amianto/il-progetto-di-mappatura-dellamianto-in-piemonte)
- [Saba et al. 2025: hyperspectral versus WorldView-3 for AC roofs](https://www.sciencedirect.com/science/article/pii/S2352938525000175)
- [Krówczyńska and Wilk 2024: CNN on 5 cm aerial photos, Warsaw](https://isprs-archives.copernicus.org/articles/XLVIII-3-2024/273/2024/)
- [Gluski et al. 2024: RoofClassify plugin evaluation, Colombia](https://www.documentation.ird.fr/hor/fdi:010091365)
- [Copernicus Data Space quotas](https://documentation.dataspace.copernicus.eu/Quotas.html)
