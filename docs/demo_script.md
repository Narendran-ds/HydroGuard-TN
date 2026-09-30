# HydroGuard — 2-3 minute demo script

Follows the mandatory pipeline order. On the live dashboard, click **"Demo Mode"** to auto-advance through these same six stages with on-screen captions — this script narrates what to say alongside it.

## 1. Satellite Data (~20s)
"HydroGuard pulls everything live from Earth Engine — no downloads. Sentinel-2 for water extent, Dynamic World for built-up, JRC Global Surface Water to cross-check, and CHIRPS rainfall to make sure we're not confusing a dry year with encroachment."

## 2. Image Processing (~25s)
"Every Sentinel-2 scene is cloud-masked, and we compute MNDWI — the water index — per scene. A pixel counts as water if it's wet in at least 30% of clear observations in a season. Here's the baseline water extent from the 2019-20 wet season." *(baseline layer visible)*

## 3. Geospatial Analysis (~30s)
"Now compare that to the most recent wet season. Toggling recent water on, and the before/after swipe slider, you can see exactly where water has receded." *(toggle recent + water lost layer, drag swipe slider)*

## 4. Indicator / Model (~30s)
"Not all water loss is encroachment — some is just drying or siltation. We only count it as encroachment where built-up has moved in. Each lake gets a weighted Encroachment Risk Index from 0 to 100, combining % area lost, new built-up in a 250m buffer, built-up density, and proximity to development." *(lakes layer + new built-up overlay visible, colored by risk)*

## 5. Interactive Map (~30s)
"This whole map is interactive — click any lake for its full stats, or use the Top-10 priority list on the left." *(click a Critical-risk lake, e.g. Pallikaranai or Chembarambakkam if flagged; show side panel)*

## 6. Decision / Recommendation (~20s)
"Each lake gets a concrete recommendation — from routine monitoring up to immediate boundary demarcation for Critical-risk lakes. This turns a satellite analysis into something a city planner can act on directly." *(show recommendation box + Top-10 table)*

## Closing (~10s)
"HydroGuard is built for all of Tamil Nadu, piloted here on Chennai. Live at narendran-ds.github.io/HydroGuard-TN, fully open source."
