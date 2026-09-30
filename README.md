# HydroGuard — Urban Waterbody Encroachment Risk Map

**GeoImpathon 1.0 (SRM) — Problem Statement 2.2**
Live demo: https://narendran-ds.github.io/HydroGuard-TN/

Built for Tamil Nadu, piloted on the Chennai-Kancheepuram-Chengalpattu belt (Pallikaranai marsh, Chembarambakkam, Puzhal/Red Hills, Madurantakam, Sholinganallur, OMR/GST lakes and more).

## Problem

Urban water bodies across Tamil Nadu are shrinking as built-up expansion encroaches on their beds and buffer zones, driving flooding, groundwater stress, and loss of ecological function. HydroGuard identifies *where* water bodies have lost area, distinguishes encroachment (replaced by built-up) from drying/siltation, and ranks lakes by encroachment risk to prioritize enforcement and restoration.

## Pipeline

```
Satellite Data -> Image Processing -> Geospatial Analysis -> Indicator/Model -> Interactive Map/Dashboard -> Decision/Recommendation
```

1. **Satellite Data** — Sentinel-2 SR Harmonized, Dynamic World, JRC Global Surface Water, CHIRPS daily rainfall, all pulled live via the Earth Engine Python API (no external downloads).
2. **Image Processing** — Per-scene cloud masking (Scene Classification Layer) and MNDWI = (Green-SWIR1)/(Green+SWIR1) computed for every clear observation in each season.
3. **Geospatial Analysis** — Water = MNDWI>0 in >=30% of clear observations per season; baseline (Nov 2019-Mar 2020) vs. recent post-monsoon season compared pixel-by-pixel; results cross-checked against JRC surface-water occurrence.
4. **Indicator/Model** — Lost water co-located with new Dynamic World built-up is classified as *encroachment* (vs. *drying/siltation* otherwise); a weighted Encroachment Risk Index (0-100) is computed per lake.
5. **Interactive Map/Dashboard** — Static Leaflet + Chart.js dashboard (`/docs`), zero backend, all data pre-computed and committed.
6. **Decision/Recommendation** — Rule-based recommendation per lake, from routine monitoring to immediate boundary enforcement, plus a ranked Top-10 priority table.

## Data sources

| Dataset | Earth Engine ID | Use |
|---|---|---|
| Sentinel-2 SR Harmonized | `COPERNICUS/S2_SR_HARMONIZED` | MNDWI water extent |
| Dynamic World v1 | `GOOGLE/DYNAMICWORLD/V1` | Built-up probability |
| JRC Global Surface Water | `JRC/GSW1_4/GlobalSurfaceWater` | Cross-validation |
| CHIRPS Daily | `UCSB-CHG/CHIRPS/DAILY` | Rainfall-comparability check |
| FAO GAUL Level 1 | `FAO/GAUL/2015/level1` | Land mask (excludes the sea) |

## Method

- **Study area**: lon 79.80-80.35, lat 12.45-13.25 (widened from the initial pilot box to include Madurantakam).
- **Seasons**: baseline Nov 2019-Mar 2020, recent Nov 2025-Mar 2026 (same wet-season window both times, so rainfall differences don't get mistaken for encroachment — see the CHIRPS ratio KPI on the dashboard).
- **Water extent**: MNDWI>0 on a cloud-masked (SCL) seasonal median composite per period — chosen over a per-scene frequency threshold for speed within the hackathon time-box (the spec explicitly allows a seasonal composite as a simpler alternative), intersected with JRC occurrence>0 (at least one historical 1984-2021 water observation) to exclude one-off flooded farmland, land-masked to exclude the Bay of Bengal, minimum lake size 2 ha.
- **Lake identity**: individual waterbody polygons are vectorized from the baseline water mask, then fragments matched to the same named lake (point-in-polygon against a small hand-coded gazetteer of Chennai-region lakes) are merged into one entity — vegetated marshes and partially-inundated reservoirs otherwise fragment into dozens of small polygons at 30 m connectivity.
- **Built-up**: Dynamic World seasonal mean "built" probability, thresholded at 0.5.
- **Risk Index** = 40% x %area lost + 30% x new built-up in 250m buffer + 20% x built-up density in buffer + 10% x inverse distance to nearest built-up, min-max normalized 0-100. Classes: Low <25, Moderate 25-50, High 50-75, Critical >75.

## Results

*(from `docs/data/summary.json` — see the live dashboard for interactive figures)*

- Baseline water extent: 23,933 ha
- Recent water extent: 25,246 ha
- Water lost: 4,000 ha (16.7% of baseline)
- Water gained: 5,313 ha
- Encroached (built-up replacing water): 8 ha
- Drying/siltation (non-built replacement): 3,992 ha
- JRC surface-water agreement: 28%
- CHIRPS rainfall ratio (recent/baseline): 1.09x (comparable rainfall — no drought confound)
- 730 named/unnamed waterbodies >=2 ha in the inventory; region-wide built fraction 21.5%
- Featured: Pallikaranai Marsh (104 -> 80 ha, 22.7% lost, Moderate risk) and Chembarambakkam Lake (418 -> 384 ha, 8.1% lost) — both pinned in the dashboard's Featured Lakes panel regardless of area rank, since Pallikaranai's small current extent *is* the encroachment story (it has already shrunk far below its historical footprint).

Region-wide, most water loss is classified as drying/siltation rather than encroachment — built-up replacement is concentrated in specific lakes near the urban core (see per-lake stats and the Top-10-by-risk table), not spread evenly across the rural belt.

## Limitations

- 10 m Sentinel-2 resolution — ponds smaller than the 2 ha cutoff are excluded from the lake inventory; the dashboard's Top-10/chart further filter to >=10 ha to keep noisy sub-pixel-scale ponds out of the priority ranking.
- Season choice affects apparent water extent; the CHIRPS ratio flags any residual rainfall mismatch between the two windows.
- JRC occurrence>0 filtering and lake-fragment merging are both approximations time-boxed for the hackathon — occurrence>0 is a low bar (kept to avoid excluding genuine but seasonal rain-fed tanks, common in this region), and fragment merging is by matched name only, not a true morphological reconnection of the water mask.
- No ground-truth field survey — encroachment classification is inferred purely from co-located built-up growth, not verified on the ground.
- Estuarine/tidal water bodies near the coastline may show apparent change driven by tidal state rather than encroachment.
- JRC cross-validation agreement (28%) is lower than a strict "core permanent lake" bar would suggest, largely because many Tamil Nadu irrigation tanks are seasonal/rain-fed and don't hold water year-round in JRC's multi-decadal record — this is a property of the region's hydrology, not necessarily an error in the water mask.

## Reproduce

```bash
cd gee
python pipeline.py                 # runs avail -> stats -> export in order (~10-15 min)
cd ../docs
python -m http.server              # serve the dashboard locally at localhost:8000
```

Each stage can also be run individually (`--step avail|stats|export`) — useful for resuming after a failure without recomputing everything from scratch, since `stats` caches its results to `gee/_cache/*.json` before `export` reads them.

Requires `earthengine-api` (see `requirements.txt`) and an authenticated Earth Engine account with access to a Cloud project (`EE_PROJECT` env var).

## Repo structure

```
gee/pipeline.py       Earth Engine analysis pipeline (avail / stats / export steps)
gee/README_gee.md     Pipeline implementation notes
docs/index.html       Dashboard
docs/app.js           Dashboard logic (Leaflet + Chart.js)
docs/style.css        Dashboard styling
docs/data/            Pipeline outputs (GeoJSON, PNG overlays, summary.json) — committed, zero backend
docs/demo_script.md   2-3 minute demo video script
```
