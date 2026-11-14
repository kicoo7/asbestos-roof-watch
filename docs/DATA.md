# Data sources

Checked on 13 Nov 2026. Raw files live in `data/raw/` and are not committed.

## Asbestos-cement roof labels: ARPA Piemonte

The pilot uses Piedmont, not Lombardy, because I first found no public Lombardy source: it is not on dati.lombardia.it or on the `/arcgis*/` regional services, and the regional asbestos plan (PRAL) says the 2007/2012 map is distributed to Regione, ARPA and ASL only. A Lombardy service does exist, though, on a server path I had not checked. It is named in `docs/VALIDATION.md` (written outside this pipeline) and I confirmed it on 13 Nov 2026: `https://www.cartografia.servizirl.it/expo/rest/services/gpt/coperture_amianto/MapServer`. Its layer 1 is the 2020 mapping; layer 4 (50,131 polygons) holds the older mappings with change type and period in `decodifica`. It shows no licence. See RESULTS.md, section Lombardy.

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

## Addresses and businesses: Overture Maps

| Item | Value |
|---|---|
| Files | `data/raw/overture_addresses_pilot.parquet` (64,925 address points), `data/raw/overture_places_pilot.parquet` (6,046 places), Brescia pilot bbox |
| Places fields kept | `id`, `name`, `category`, `taxonomy`, `confidence`, `website`, `address`, `locality`, `postcode`, `operating_status`, `src`. The phone, e-mail and social fields were not downloaded |
| Website filter | The `website` field often holds a social page (for example Facebook) instead of the business's own site, and sometimes a WhatsApp or Telegram link or a URL carrying a phone number or e-mail address. Social pages are kept as links but not crawled. `roofwatch.urls.public_website` drops messaging links, cuts a URL back to the site root when its path carries a contact detail, and drops query strings, before anything is stored, fetched or shown. A phone number or e-mail address inside a business name is removed too |
| Places sources (`src`) | BrightQuery 3,035, Meta 2,307, Microsoft 348, AllThePlaces 176, Foursquare 138, others 42 |
| Licence | Places: CDLA Permissive 2.0. Addresses: see Overture's attribution page for the Italian source |
| Known gap | The DuckDB query that produced these two files is not in the repo yet |
| Used by | `scripts/contact_worksheet.py` -> `outputs/roof_occupants.csv`, `outputs/roof_addresses.csv` |

BrightQuery listings come from company-registry style data, so a "business name" can be a sole trader's personal name.

## Company public record: business websites and EU VIES

Checked on 14 Nov 2026. `scripts/enrich_companies.py` -> `outputs/company_register.csv`, one row per website domain.

| Item | Value |
|---|---|
| Source 1 | The business's own website, as listed in Overture places: the home page plus at most 2 contact or legal pages. Italian businesses must show their VAT number (Partita IVA) on their website (commonly cited as art. 35 DPR 633/1972; not verified here) |
| Crawling | robots.txt respected per host, including the target of a redirect (the redirected request itself is made before that check; the page is not used if disallowed). Crawl-delay honoured up to 30 s; longer means home page only. robots.txt answering 429 means no crawl in that run. User-Agent `roofwatch-prototype (research)`; at least 1 s between requests to a site and its subdomains; 4 sites in parallel; pages cut at 1.5 MB |
| Source 2 | EU VIES REST service, `https://ec.europa.eu/taxation_customs/vies/rest-api/ms/IT/vat/<number>`. No key; at most 1 request per second. Fields used: `isValid`, `userError`, `name`, `address` |
| Fields written | `domain`, `website`, `status`, `vat`, `legal_name`, `legal_form`, `entity_type`, `registered_office`, `vies_valid`, `vies_checked_on`, `rea`, `source_url`, `fetched_on`, `tls_unverified` |
| Not kept | Phones, emails, PEC addresses, names of directors or contact persons. For sole traders and unknown legal forms the VIES address is neither written nor cached |
| Suppression | Optional `outputs/company_suppress.csv` (columns `domain` and/or `vat`), kept by hand after an objection. A listed business gets status `suppressed` with every other column empty, as do its other websites with the same VAT number. Its cached record is replaced by a marker that holds no VAT number, name or URL, its VIES answer is deleted, and it is not fetched or looked up again |
| Caches | One small record per domain, `data/raw/company_pages/<domain>/record.json` (status, VAT, legal name, REA, source page, dates), and VIES answers in `data/raw/vies/<vat>.json`. Page bodies hold e-mail addresses and phone numbers, so they are deleted as soon as the record is written (`--keep-pages` keeps them, for debugging only). Private, not committed. A rerun makes no requests; `--retry-failed` retries failed fetches |
| Pilot run | 1,871 domains: VAT number found for 867, of which VIES confirmed 726 (553 companies, 173 sole traders or unknown form) and 141 are not in VIES. 509 sites could not be read (about 270 no longer exist) and 445 show no VAT number. 6 source pages were read without certificate verification (`tls_unverified`) |

`vies_valid` false does not mean a fake number: an Italian VAT number is in VIES only when its holder registered for intra-EU trade.
