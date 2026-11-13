# Data sources

Checked on 13 Nov 2026. Raw files live in `data/raw/` and are not committed.

## Asbestos-cement roof labels: ARPA Piemonte

The pilot uses Piedmont, not Lombardy. I could not find a public download of ARPA Lombardia's AC roof map. It is not on dati.lombardia.it or on the regional ArcGIS services, and the regional asbestos plan (PRAL) says the 2007/2012 map is distributed to Regione, ARPA and ASL. However, a file that appeared in `outputs/validation/brescia_sample_50.csv` during this work (not produced by this pipeline) cites ARPA Lombardia layers "Mappatura 2020 (layer 1)" and "Mappature precedenti (layer 4)" with object IDs. So a newer, queryable Lombardy service probably exists. With its URL, `labels.fetch_arcgis_layer` can read it and the pilot can move to Lombardy.

| Item | Value |
|---|---|
| Service | `https://webgis.arpa.piemonte.it/server/rest/services/amianto/amianto_PRA_pubblico/FeatureServer` (also MapServer, WMS, WFS) |
| Download | `scripts/fetch_labels.py` -> `data/raw/piemonte_labels.gpkg` (layers `checked`, `candidates`, `municipalities`) |
| Format, CRS | Points (roofs) and polygons (municipalities); EPSG:32632 (WGS 84 / UTM 32N) |
| Licence | CC BY 4.0, attribution "Arpa Piemonte" (INSPIRE metadata record; copyright field of the service says "Arpa Piemonte") |
| Update | Continuous; ARPA staff check roofs on site |
| Opens in | GDAL / geopandas (QGIS reads the same GeoPackage through GDAL) |

### Layer `checked` (service layer 5), 70,190 points

| Field | Meaning |
|---|---|
| `esito_tot`, `desc_esito` | Outcome. `1` "Presenza MCA - non bonificato" (57,051); `3` "Completamente bonificato" (12,675), "Parzialmente bonificato" (342), "Bonifica in corso" (122) |
| `label` | Added by this project: `ac`, `removed`, `partial` (`labels.label_from_esito`) |
| `mq_stimati` | Estimated AC area in m2 (47,457 filled; median 150, 968 points >= 2,000) |
| `data_sopr` | Date of the site visit. Median 2016 for `ac`, 2020 for `removed`. Some values are typos (years 204, 219, 2108); dates outside 2000 to today are treated as unknown |
| `edif_tipo`, `desc_tipo_edif` | Building type (agricultural 18,924, residential 15,095, artisan/service 12,827, industrial 6,945, ...) |
| `comune`, `comune_nom`, `provin_nom`, `indirizzo`, `num_civico` | Location. **Addresses make this building-level personal-adjacent data: keep it private.** |

The points mark addresses or roofs, not outlines. 92% fall inside a building footprint (see footprints below).

### Layer `candidates` (service layer 3), 15,707 points

Remote-sensing hits not yet verified ("Da verificare"). 15,241 come from 2020 imagery and 466 from 2018. Fields as above, plus `esito_rn` / `anno_rn` (neural-network outcome and year). Used only to mark roofs as `candidate`, never as training labels.

### Layer `municipalities` (service layer 2), 1,180 polygons

Municipalities covered by the mapping (`comune_ist`, `comune_nom`, `provin_nom`, `pop`).

## Pilot area

`data/pilot_area.geojson`: 19 municipalities of the Turin belt, 546 km2. They hold 989 AC points with >= 500 m2 estimated AC area and about 200 remediated ones.

## Imagery: Sentinel-2 L2A, Element84 Earth Search

| Item | Value |
|---|---|
| STAC | `https://earth-search.aws.element84.com/v1`, collection `sentinel-2-c1-l2a` (Collection 1: consistent reprocessing) |
| Access | No account; Cloud-Optimised GeoTIFFs on S3 us-west-2, read by window |
| Scaling | Each asset's `raster:bands` gives `scale` 0.0001 and `offset` -0.1. The code uses these values and falls back to the processing-baseline rule (>= 04.00 -> -0.1) only when they are missing |
| Tiles | The pilot touches 32TLQ, 32TLR, 32TMQ, 32TMR. They share one 10 m grid (UTM 32N), so each roof is read from a single "home" tile |
| Cloud mask | SCL classes 4, 5, 6, 7 count as clear |

## Building footprints: Overture Maps

| Item | Value |
|---|---|
| Release | `2026-10-21.0`, theme `buildings`, read with DuckDB from `s3://overturemaps-us-west-2/release/...` |
| File | `data/raw/overture_buildings.parquet` (pilot bbox, 214,747 buildings) |
| Content | OpenStreetMap 156,354 + Microsoft ML Buildings 58,393 |
| Licence | ODbL (OSM-derived) and CDLA Permissive 2.0 (Microsoft) |
| Coverage of AC labels >= 500 m2 | 92.4% within 15 m (OSM alone: 76.6%) |

Piedmont's regional topographic database (BDTRE) was not compared on its own. Piedmont's OSM buildings were imported from BDTRE in 2015, so OSM is close to it.

## Building age: GHSL

`GHS_BUILT_S_E{1975..2020}_GLOBE_R2023A_54009_100_V1_0`, tile R4_C19, from the JRC FTP server. 100 m built-up surface per 5-year epoch. Feature `built_year` = the first epoch whose built-up surface reaches half of the 2020 value at the roof's location. Licence: CC BY 4.0 (European Commission, JRC).
