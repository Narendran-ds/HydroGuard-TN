# HydroGuard Earth Engine pipeline

`pipeline.py` runs in three staged steps so a late failure never forces a full recompute:

```
python pipeline.py                 # default: avail -> stats -> export, in order
python pipeline.py --step avail    # scene availability + CHIRPS rainfall comparability check
python pipeline.py --step stats    # water/built-up extraction + per-lake risk stats -> _cache/*.json
python pipeline.py --step export   # PNG overlays + GeoJSON + summary.json -> ../docs/data/
```

## Key implementation choices

- **Single scale throughout**: 20 m for all area/reduceRegion stats, 30 m for `reduceToVectors`. Mixing scales makes focal/connected-pixel operations inconsistent.
- **Land mask**: FAO GAUL Level-1 "Tamil Nadu" boundary is used to exclude the Bay of Bengal and tidal estuary mouths from all water/area statistics — otherwise the ocean dominates the "water lost/gained" numbers.
- **Water extent**: per-scene SCL cloud mask, then a single median composite per season, MNDWI = normalizedDifference(B3, B11), water = MNDWI>0 on the composite, AND'd with JRC occurrence>0. (The originally-planned per-scene ">=30% of clear observations" frequency approach — mapping two custom bands across the ~125-scene collection then `reduceRegion.sum` — took 15+ minutes per call against this AOI without returning; switched to the seasonal-composite alternative the spec explicitly allows. The JRC occurrence>0 term was added after a first run: raw MNDWI>0 alone fused hundreds of separate tanks into single multi-hundred-hectare blobs via flooded rice paddy and irrigation canals, confirmed by only 25% JRC>50% overlap and neither Pallikaranai nor Chembarambakkam surfacing as distinct polygons.)
- **Lake fragment merging**: `aggregate_named_lakes()` groups vectorized polygons matched to the same named lake (via point-in-polygon, falling back to nearest-centroid within ~5.5km) into one entity, summing areas and area-weighting buffer stats. Needed because vegetated marshes (Pallikaranai) and partially-inundated reservoir edges (Chembarambakkam) still fragment into dozens of small polygons at 30m connectivity even after the JRC filter — confirmed when both were individually invisible in the top-10 despite clearly being present (Chembarambakkam alone summed to ~418 ha across 18 fragments).
- **Noise removal**: rather than a separate small-patch raster filter, the 0.5-2 ha minimum-size threshold is applied via `connectedPixelCount` before vectorization and again as a polygon-area filter after — this simultaneously removes speckle noise and keeps the lake inventory to a manageable, hackathon-timebox-friendly feature count.
- **Built-up**: Dynamic World seasonal mean "built" band, threshold 0.5; the pipeline prints the region-wide built fraction as a sanity check.
- **Distance to nearest built-up**: computed via `fastDistanceTransform` on the built-up mask rather than iterative buffer rings, for a single cheap raster pass.
- **Exports**: `water_lost` and `new_builtup` are exported as `getThumbURL` PNG overlays (not vectorized) to avoid slow/oversized vector exports of thousands of small polygon slivers. All thumbnails use `crs: EPSG:3857` (matching Leaflet's own Web Mercator tiles) and `selfMask()` for a transparent background, so they align pixel-for-pixel with the dashboard's Leaflet map.
- **No Drive round-trips**: everything uses `getInfo()` / `getThumbURL()` directly against the pilot AOI, which is small enough to stay within Earth Engine's interactive request limits.

## Known simplifications (time-boxed hackathon build)

- No OSM lake-name merge — lakes are named by nearest-point match against a small hand-coded dictionary of well-known Chennai-region lakes.
- "Distance to nearest built-up" is a raster-distance-transform minimum over the lake polygon, not a true centroid-to-nearest-built-pixel geodesic distance — a reasonable proxy given the time budget.
