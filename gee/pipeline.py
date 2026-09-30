"""
HydroGuard pipeline: waterbody encroachment risk for the Chennai-Kancheepuram-
Chengalpattu belt, Tamil Nadu.

Usage:
    python pipeline.py --step all      # runs avail -> stats -> export in order
    python pipeline.py --step avail
    python pipeline.py --step stats
    python pipeline.py --step export
"""
import argparse
import json
import os
import sys
import urllib.request

import ee

SCALE = 20  # meters, used consistently for all area/stat reductions
VECTOR_SCALE = 30  # meters, used for reduceToVectors

# AOI widened from the original 79.90-80.35 / 12.55-13.25 spec to make sure
# Madurantakam (~12.51N, 79.87E) and Chembarambakkam (~13.00N, 79.90E) are
# both inside the box. Built lazily after ee.Initialize() (module-level ee
# calls fail before initialization).
AOI = None

BASELINE_START, BASELINE_END = "2019-11-01", "2020-03-31"
RECENT_START, RECENT_END = "2025-11-01", "2026-03-31"

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "data")
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cache")

NAMED_LAKES = {
    "Pallikaranai Marsh": (80.2109, 12.9382),
    "Chembarambakkam Lake": (79.9591, 13.0034),
    "Puzhal Lake (Red Hills)": (80.1826, 13.1928),
    "Red Hills Lake": (80.1826, 13.1928),
    "Madurantakam Lake": (79.8767, 12.5103),
    "Sholinganallur Lake": (80.2274, 12.8994),
    "Velachery Lake": (80.2209, 12.9755),
    "Porur Lake": (80.1583, 13.0357),
    "Ambattur Lake": (80.1548, 13.1143),
    "Sembakkam Lake": (80.1494, 12.9236),
}


def init_ee():
    global AOI
    project = os.environ.get("EE_PROJECT", "hydroguard-ee")
    ee.Initialize(project=project)
    AOI = ee.Geometry.Rectangle([79.80, 12.45, 80.35, 13.25])
    print("EE initialized with project: %s" % project)


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)


def cache_path(name):
    return os.path.join(CACHE_DIR, name)


def save_cache(name, obj):
    with open(cache_path(name), "w") as f:
        json.dump(obj, f, indent=2)


def load_cache(name):
    p = cache_path(name)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return None


# ---------------------------------------------------------------------------
# Step 0: availability check
# ---------------------------------------------------------------------------

def chirps_total(start, end):
    coll = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").filterDate(start, end).filterBounds(AOI)
    total_img = coll.sum()
    val = total_img.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=AOI, scale=5000, maxPixels=1e9
    ).get("precipitation")
    return val.getInfo()


def s2_scene_count(start, end):
    coll = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(start, end)
        .filterBounds(AOI)
    )
    return coll.size().getInfo()


def step_avail():
    print("=== Step 0: Availability check ===")
    print("AOI: lon 79.80-80.35, lat 12.45-13.25")

    for name, (lon, lat) in NAMED_LAKES.items():
        pt = ee.Geometry.Point([lon, lat])
        inside = AOI.contains(pt).getInfo()
        print("  lake-in-bbox check: %-28s %s" % (name, "OK" if inside else "OUTSIDE BBOX"))

    baseline_n = s2_scene_count(BASELINE_START, BASELINE_END)
    recent_n = s2_scene_count(RECENT_START, RECENT_END)
    print("S2 SR Harmonized scenes - baseline (%s to %s): %d" % (BASELINE_START, BASELINE_END, baseline_n))
    print("S2 SR Harmonized scenes - recent   (%s to %s): %d" % (RECENT_START, RECENT_END, recent_n))

    fallback_used = False
    recent_start, recent_end = RECENT_START, RECENT_END
    if recent_n < 20:
        print("WARNING: recent window has very few scenes, falling back to Nov 2024-Mar 2025.")
        recent_start, recent_end = "2024-11-01", "2025-03-31"
        recent_n = s2_scene_count(recent_start, recent_end)
        print("S2 SR Harmonized scenes - recent fallback (%s to %s): %d" % (recent_start, recent_end, recent_n))
        fallback_used = True

    baseline_rain = chirps_total(BASELINE_START, BASELINE_END)
    recent_rain = chirps_total(recent_start, recent_end)
    ratio = (recent_rain / baseline_rain) if baseline_rain else None
    print("CHIRPS total rainfall - baseline: %.1f mm, recent: %.1f mm, ratio: %.2f" % (
        baseline_rain, recent_rain, ratio if ratio else -1
    ))

    report = {
        "baseline_scenes": baseline_n,
        "recent_scenes": recent_n,
        "recent_start": recent_start,
        "recent_end": recent_end,
        "fallback_used": fallback_used,
        "baseline_rain_mm": baseline_rain,
        "recent_rain_mm": recent_rain,
        "chirps_ratio": ratio,
    }
    save_cache("availability.json", report)
    print("Saved availability report to _cache/availability.json")
    return report


# ---------------------------------------------------------------------------
# Step 2-4: water extent, built-up, loss classification, per-lake stats
# ---------------------------------------------------------------------------

MIN_LAKE_HA = 2.0
BUFFER_M = 250
DIST_CAP_M = 2000

_land_mask = None


def land_mask():
    global _land_mask
    if _land_mask is not None:
        return _land_mask
    tn = (
        ee.FeatureCollection("FAO/GAUL/2015/level1")
        .filter(ee.Filter.eq("ADM1_NAME", "Tamil Nadu"))
    )
    _land_mask = ee.Image.constant(1).clip(tn).rename("land")
    return _land_mask


def water_extent(start, end):
    """MNDWI > 0 on a cloud-masked seasonal median composite, land-masked.

    The original per-scene >=30%-of-clear-observations frequency approach
    (mapping two custom bands across the full ~125-scene collection, then
    reduceRegion.sum) took 15+ minutes per call against this AOI and never
    returned. Spec explicitly allows "a seasonal composite if simpler" as a
    documented alternative, so we cloud-mask each scene via SCL then take one
    median composite per season before thresholding MNDWI - a single builtin
    temporal reducer, evaluated once, instead of a custom two-band reduction
    over the whole collection.
    """
    coll = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(start, end)
        .filterBounds(AOI)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 70))
    )

    def mask_clouds(img):
        scl = img.select("SCL")
        clear = (
            scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
        )
        return img.updateMask(clear)

    composite = coll.map(mask_clouds).median()
    mndwi = composite.normalizedDifference(["B3", "B11"])
    mndwi_water = mndwi.gt(0)

    # Raw MNDWI>0 alone pulls in flooded rice paddy and irrigation canals across
    # this agrarian belt, fusing hundreds of separate tanks into single
    # multi-hundred-hectare blobs (confirmed by a first run: top "lakes" were
    # 300-2000+ ha with only 25% JRC overlap, and neither Pallikaranai nor
    # Chembarambakkam surfaced as distinct polygons). JRC occurrence>0 requires
    # at least one historical (1984-2021) observation of water, which real lakes/
    # tanks have and one-off seasonal paddy flooding mostly does not - so it's
    # used here as a standing-waterbody filter, not just a cross-check metric.
    jrc_ever_water = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").gt(0).unmask(0)
    water = mndwi_water.And(jrc_ever_water).updateMask(land_mask()).clip(AOI).selfMask()
    return water


def built_extent(start, end):
    """Dynamic World seasonal mean 'built' probability, thresholded at 0.5."""
    coll = (
        ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
        .filterDate(start, end)
        .filterBounds(AOI)
        .select("built")
    )
    built_prob = coll.mean().clip(AOI)
    built_mask = built_prob.gt(0.5).updateMask(land_mask())
    return built_prob, built_mask


def region_area_ha(mask_img):
    area = mask_img.multiply(ee.Image.pixelArea()).reduceRegion(
        reducer=ee.Reducer.sum(), geometry=AOI, scale=SCALE, maxPixels=1e10, bestEffort=True
    )
    val = area.values().get(0)
    ha = ee.Number(val).divide(10000)
    return ha.getInfo() if val is not None else 0.0


def jrc_overlap_pct(baseline_water):
    jrc = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence")
    jrc_water = jrc.gt(50).unmask(0)
    overlap = baseline_water.unmask(0).And(jrc_water)
    overlap_ha = region_area_ha(overlap)
    baseline_ha = region_area_ha(baseline_water.unmask(0))
    if baseline_ha == 0:
        return 0.0
    return 100.0 * overlap_ha / baseline_ha


def aggregate_named_lakes(lakes):
    """Merge fragments sharing a matched name into one lake entity.

    Vegetated marshes (Pallikaranai) and partially-inundated reservoir edges
    (Chembarambakkam) fragment into dozens of small polygons at 30m
    connectivity rather than one cohesive shape - confirmed against a first
    run where both were individually invisible in the top 10 despite clearly
    being present (Chembarambakkam alone summed to ~418 ha across 18
    fragments). Unnamed fragments are left as-is.
    """
    named = {}
    unnamed = []
    for l in lakes:
        if l["name"]:
            named.setdefault(l["name"], []).append(l)
        else:
            unnamed.append(l)

    merged = []
    for name, frags in named.items():
        b_ha = sum(f["baseline_ha"] for f in frags)
        r_ha = sum(f["recent_ha"] for f in frags)
        lost_ha = max(b_ha - r_ha, 0.0)
        pct_lost = (100.0 * lost_ha / b_ha) if b_ha > 0 else 0.0
        encroached_ha = sum(f["encroached_ha"] for f in frags)
        drying_ha = sum(f["drying_ha"] for f in frags)

        w = b_ha or 1.0
        new_bu_pct = sum(f["new_builtup_pct_buffer"] * f["baseline_ha"] for f in frags) / w
        density_pct = sum(f["built_density_buffer_pct"] * f["baseline_ha"] for f in frags) / w
        dist_val = min(f["dist_to_built_m"] for f in frags)
        inv_dist = max(0.0, (DIST_CAP_M - dist_val)) / DIST_CAP_M * 100.0
        raw_score = 0.4 * pct_lost + 0.3 * new_bu_pct + 0.2 * density_pct + 0.1 * inv_dist

        polys = []
        for f in frags:
            g = f["geometry"]
            polys.extend([g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"])
        geometry = {"type": "MultiPolygon", "coordinates": polys}

        merged.append({
            "id": frags[0]["id"] + "-merged",
            "name": name,
            "geometry": geometry,
            "baseline_ha": round(b_ha, 2),
            "recent_ha": round(r_ha, 2),
            "lost_ha": round(lost_ha, 2),
            "pct_lost": round(pct_lost, 1),
            "encroached_ha": round(encroached_ha, 2),
            "drying_ha": round(drying_ha, 2),
            "new_builtup_pct_buffer": round(new_bu_pct, 1),
            "built_density_buffer_pct": round(density_pct, 1),
            "dist_to_built_m": round(dist_val, 1),
            "fragment_count": len(frags),
            "raw_score": raw_score,
        })

    return merged + unnamed


def step_stats():
    print("=== Steps 2-4: water/built-up extraction and per-lake stats ===")
    avail = load_cache("availability.json")
    if avail is None:
        avail = step_avail()
    recent_start, recent_end = avail["recent_start"], avail["recent_end"]

    print("Computing baseline water extent (%s to %s)..." % (BASELINE_START, BASELINE_END))
    baseline_water = water_extent(BASELINE_START, BASELINE_END)
    print("Computing recent water extent (%s to %s)..." % (recent_start, recent_end))
    recent_water = water_extent(recent_start, recent_end)

    baseline_ha = region_area_ha(baseline_water.unmask(0))
    recent_ha = region_area_ha(recent_water.unmask(0))
    print("Baseline water: %.1f ha | Recent water: %.1f ha" % (baseline_ha, recent_ha))

    jrc_pct = jrc_overlap_pct(baseline_water)
    print("JRC GSW occurrence>50%% overlap with baseline water: %.1f%%" % jrc_pct)

    print("Computing built-up extent (Dynamic World)...")
    built_prob_baseline, built_mask_baseline = built_extent(BASELINE_START, BASELINE_END)
    built_prob_recent, built_mask_recent = built_extent(recent_start, recent_end)

    built_frac_pct = 100.0 * region_area_ha(built_mask_recent.unmask(0)) / region_area_ha(
        land_mask().clip(AOI)
    )
    print("Region-wide built fraction (recent, threshold 0.5): %.1f%%" % built_frac_pct)
    if built_frac_pct < 2.0:
        print("WARNING: built fraction implausibly low for Chennai metro; check DW threshold.")

    new_builtup = built_mask_recent.And(built_mask_baseline.unmask(0).Not())

    water_lost = baseline_water.unmask(0).And(recent_water.unmask(0).Not())
    water_gained = recent_water.unmask(0).And(baseline_water.unmask(0).Not())
    encroached = water_lost.And(built_mask_recent)
    drying = water_lost.And(built_mask_recent.Not())

    lost_ha = region_area_ha(water_lost)
    gained_ha = region_area_ha(water_gained)
    encroached_ha = region_area_ha(encroached)
    drying_ha = region_area_ha(drying)
    pct_loss = 100.0 * lost_ha / baseline_ha if baseline_ha else 0.0
    print(
        "Water lost: %.1f ha (%.1f%% of baseline) | Encroached: %.1f ha | Drying/siltation: %.1f ha | Gained: %.1f ha"
        % (lost_ha, pct_loss, encroached_ha, drying_ha, gained_ha)
    )

    # --- vectorize baseline water into lake polygons ---
    print("Vectorizing baseline water bodies (min %.1f ha)..." % MIN_LAKE_HA)
    min_px = int((MIN_LAKE_HA * 10000) / (VECTOR_SCALE * VECTOR_SCALE)) + 1
    clean_water = baseline_water.updateMask(
        baseline_water.connectedPixelCount(min_px + 50, True).gte(min_px)
    )
    lakes_fc = clean_water.reduceToVectors(
        geometry=AOI,
        scale=VECTOR_SCALE,
        geometryType="polygon",
        eightConnected=True,
        labelProperty="zone",
        maxPixels=1e10,
        bestEffort=True,
    )

    def add_area_and_centroid(f):
        geom = f.geometry()
        area_ha = geom.area(1).divide(10000)
        centroid = geom.centroid(1).coordinates()
        return f.set(
            "area_ha", area_ha,
            "cx", centroid.get(0),
            "cy", centroid.get(1),
        )

    lakes_fc = lakes_fc.map(add_area_and_centroid).filter(ee.Filter.gte("area_ha", MIN_LAKE_HA))
    lakes_fc = lakes_fc.filter(ee.Filter.bounds(AOI.buffer(-500)))  # drop slivers on the AOI edge

    n_lakes = lakes_fc.size().getInfo()
    print("Lake polygons after min-area + edge filter: %d" % n_lakes)

    # --- per-lake stats via reduceRegions ---
    pixel_area = ee.Image.pixelArea()
    area_bands = ee.Image.cat([
        baseline_water.unmask(0).multiply(pixel_area).rename("baseline_area_m2"),
        recent_water.unmask(0).multiply(pixel_area).rename("recent_area_m2"),
        encroached.unmask(0).multiply(pixel_area).rename("encroached_area_m2"),
        drying.unmask(0).multiply(pixel_area).rename("drying_area_m2"),
    ])
    stats1_fc = area_bands.reduceRegions(collection=lakes_fc, reducer=ee.Reducer.sum(), scale=SCALE)

    dist_sq = built_mask_recent.unmask(0).fastDistanceTransform(256, "pixels")
    dist_m = dist_sq.sqrt().multiply(SCALE).rename("dist_to_built_m")
    stats2_fc = dist_m.reduceRegions(collection=lakes_fc, reducer=ee.Reducer.min(), scale=SCALE)

    buffer_fc = lakes_fc.map(lambda f: f.setGeometry(f.geometry().buffer(BUFFER_M)))
    buffer_bands = ee.Image.cat([
        new_builtup.unmask(0).rename("new_builtup_frac"),
        built_prob_recent.rename("built_density_recent"),
    ])
    stats3_fc = buffer_bands.reduceRegions(collection=buffer_fc, reducer=ee.Reducer.mean(), scale=SCALE)

    print("Fetching per-lake stats from Earth Engine...")
    base_info = lakes_fc.getInfo()["features"]
    s1_info = stats1_fc.getInfo()["features"]
    s2_info = stats2_fc.getInfo()["features"]
    s3_info = stats3_fc.getInfo()["features"]

    def by_index(features):
        return {f["id"]: f["properties"] for f in features}

    s1_map = by_index(s1_info)
    s2_map = by_index(s2_info)
    s3_map = by_index(s3_info)

    def point_in_ring(x, y, ring):
        inside = False
        n = len(ring)
        x1, y1 = ring[0][0], ring[0][1]
        for i in range(1, n + 1):
            x2, y2 = ring[i % n][0], ring[i % n][1]
            if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1) + x1):
                inside = not inside
            x1, y1 = x2, y2
        return inside

    def point_in_geom(lon, lat, geom):
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        polys = coords if gtype == "MultiPolygon" else [coords]
        for poly in polys:
            if poly and point_in_ring(lon, lat, poly[0]):
                return True
        return False

    def match_name(geom, cx, cy):
        # exact point-in-polygon first (robust to irregular/merged shapes)
        for name, (lon, lat) in NAMED_LAKES.items():
            if point_in_geom(lon, lat, geom):
                return name
        # fallback: nearest centroid within ~5.5km
        best_name, best_d = None, 0.05
        for name, (lon, lat) in NAMED_LAKES.items():
            d = ((cx - lon) ** 2 + (cy - lat) ** 2) ** 0.5
            if d < best_d:
                best_d = d
                best_name = name
        return best_name

    lakes = []
    for feat in base_info:
        fid = feat["id"]
        props = feat["properties"]
        s1 = s1_map.get(fid, {})
        s2 = s2_map.get(fid, {})
        s3 = s3_map.get(fid, {})

        baseline_m2 = s1.get("baseline_area_m2", 0.0) or 0.0
        recent_m2 = s1.get("recent_area_m2", 0.0) or 0.0
        encroached_m2 = s1.get("encroached_area_m2", 0.0) or 0.0
        drying_m2 = s1.get("drying_area_m2", 0.0) or 0.0

        b_ha = baseline_m2 / 10000.0
        r_ha = recent_m2 / 10000.0
        lost_ha_l = max(b_ha - r_ha, 0.0)
        pct_lost_l = (100.0 * lost_ha_l / b_ha) if b_ha > 0 else 0.0

        dist_val = s2.get("dist_to_built_m", DIST_CAP_M)
        if dist_val is None:
            dist_val = DIST_CAP_M
        new_bu_pct = 100.0 * (s3.get("new_builtup_frac", 0.0) or 0.0)
        density_pct = 100.0 * (s3.get("built_density_recent", 0.0) or 0.0)
        inv_dist = max(0.0, (DIST_CAP_M - dist_val)) / DIST_CAP_M * 100.0

        raw_score = 0.4 * pct_lost_l + 0.3 * new_bu_pct + 0.2 * density_pct + 0.1 * inv_dist

        name = match_name(feat["geometry"], props.get("cx"), props.get("cy"))

        lakes.append({
            "id": fid,
            "name": name,
            "geometry": feat["geometry"],
            "baseline_ha": round(b_ha, 2),
            "recent_ha": round(r_ha, 2),
            "lost_ha": round(lost_ha_l, 2),
            "pct_lost": round(pct_lost_l, 1),
            "encroached_ha": round(encroached_m2 / 10000.0, 2),
            "drying_ha": round(drying_m2 / 10000.0, 2),
            "new_builtup_pct_buffer": round(new_bu_pct, 1),
            "built_density_buffer_pct": round(density_pct, 1),
            "dist_to_built_m": round(dist_val, 1),
            "raw_score": raw_score,
        })

    lakes = aggregate_named_lakes(lakes)

    # min-max normalize raw_score -> 0-100 risk index
    raw_scores = [l["raw_score"] for l in lakes]
    lo, hi = (min(raw_scores), max(raw_scores)) if raw_scores else (0, 1)
    span = (hi - lo) or 1.0
    for l in lakes:
        risk = 100.0 * (l["raw_score"] - lo) / span
        l["risk_score"] = round(risk, 1)
        if risk > 75:
            l["risk_class"] = "Critical"
            l["recommendation"] = "Immediate boundary demarcation and buffer-zone enforcement"
        elif risk > 50:
            l["risk_class"] = "High"
            l["recommendation"] = "Restore lost fringe, monitor quarterly"
        elif risk >= 25:
            l["risk_class"] = "Moderate"
            l["recommendation"] = "Watch list"
        else:
            l["risk_class"] = "Low"
            l["recommendation"] = "Routine monitoring"
        del l["raw_score"]

    lakes.sort(key=lambda l: l["baseline_ha"], reverse=True)

    print("\nTop 10 lakes by baseline area:")
    named_found = set()
    for l in lakes[:10]:
        label = l["name"] or "(unnamed)"
        print("  %-28s baseline=%.1fha recent=%.1fha lost=%.1f%% risk=%.0f (%s)" % (
            label, l["baseline_ha"], l["recent_ha"], l["pct_lost"], l["risk_score"], l["risk_class"]
        ))
        if l["name"]:
            named_found.add(l["name"])

    for must_have in ("Pallikaranai Marsh", "Chembarambakkam Lake"):
        if must_have not in named_found:
            print("WARNING: %s did not appear in the top 10 by area — check thresholds." % must_have)

    summary = {
        "baseline_ha": round(baseline_ha, 1),
        "recent_ha": round(recent_ha, 1),
        "lost_ha": round(lost_ha, 1),
        "gained_ha": round(gained_ha, 1),
        "encroached_ha": round(encroached_ha, 1),
        "drying_ha": round(drying_ha, 1),
        "pct_loss": round(pct_loss, 1),
        "jrc_agreement_pct": round(jrc_pct, 1),
        "chirps_ratio": avail.get("chirps_ratio"),
        "baseline_window": "%s to %s" % (BASELINE_START, BASELINE_END),
        "recent_window": "%s to %s" % (recent_start, recent_end),
        "lake_count": len(lakes),
        "min_lake_ha": MIN_LAKE_HA,
        "built_fraction_pct": round(built_frac_pct, 1),
    }
    save_cache("lakes.json", lakes)
    save_cache("summary.json", summary)
    print("\nSaved lakes.json (%d lakes) and summary.json to _cache/" % len(lakes))
    return lakes, summary


# ---------------------------------------------------------------------------
# Step 5: export PNG overlays + geojson + summary into docs/data/
# ---------------------------------------------------------------------------

AOI_BOUNDS = {"south": 12.45, "north": 13.25, "west": 79.80, "east": 80.35}


def thumb_url(mask_img, color_hex):
    return mask_img.selfMask().visualize(palette=[color_hex]).getThumbURL({
        "region": AOI,
        "dimensions": 2048,
        "crs": "EPSG:3857",
        "format": "png",
    })


def download(url, dest):
    urllib.request.urlretrieve(url, dest)
    size_kb = os.path.getsize(dest) / 1024.0
    print("  wrote %s (%.0f KB)" % (dest, size_kb))


def step_export():
    print("=== Step 5: export overlays + geojson + summary ===")
    lakes = load_cache("lakes.json")
    summary = load_cache("summary.json")
    avail = load_cache("availability.json")
    if lakes is None or summary is None:
        print("ERROR: run --step stats first (missing _cache/lakes.json or summary.json).")
        sys.exit(1)

    recent_start, recent_end = avail["recent_start"], avail["recent_end"]

    print("Rebuilding rasters for PNG export...")
    baseline_water = water_extent(BASELINE_START, BASELINE_END)
    recent_water = water_extent(recent_start, recent_end)
    _, built_mask_baseline = built_extent(BASELINE_START, BASELINE_END)
    _, built_mask_recent = built_extent(recent_start, recent_end)
    new_builtup = built_mask_recent.And(built_mask_baseline.unmask(0).Not())
    water_lost = baseline_water.unmask(0).And(recent_water.unmask(0).Not())

    print("Requesting thumbnails from Earth Engine...")
    jobs = [
        (baseline_water, "3ea6ff", "baseline_water.png"),
        (recent_water, "0ea5e9", "recent_water.png"),
        (water_lost, "ef4444", "water_lost.png"),
        (new_builtup, "f97316", "new_builtup.png"),
    ]
    for mask_img, color, fname in jobs:
        url = thumb_url(mask_img, color)
        download(url, os.path.join(DATA_DIR, fname))

    with open(os.path.join(DATA_DIR, "bounds.json"), "w") as f:
        json.dump(AOI_BOUNDS, f, indent=2)
    print("Wrote bounds.json")

    features = []
    for l in lakes:
        props = {k: v for k, v in l.items() if k != "geometry"}
        features.append({"type": "Feature", "geometry": l["geometry"], "properties": props})
    geojson = {"type": "FeatureCollection", "features": features}
    geojson_path = os.path.join(DATA_DIR, "lakes_risk.geojson")
    with open(geojson_path, "w") as f:
        json.dump(geojson, f)
    size_kb = os.path.getsize(geojson_path) / 1024.0
    print("Wrote lakes_risk.geojson (%d lakes, %.0f KB)" % (len(features), size_kb))

    summary_path = os.path.join(DATA_DIR, "summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print("Wrote summary.json")

    print("\nExport complete. Files in docs/data/:")
    for fname in os.listdir(DATA_DIR):
        p = os.path.join(DATA_DIR, fname)
        print("  %-24s %.0f KB" % (fname, os.path.getsize(p) / 1024.0))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--step", default="all", choices=["all", "avail", "stats", "export"],
        help="Which stage to run. Default 'all' runs avail -> stats -> export in order.",
    )
    args = parser.parse_args()

    init_ee()
    ensure_dirs()

    if args.step == "all":
        step_avail()
        step_stats()
        step_export()
    elif args.step == "avail":
        step_avail()
    elif args.step == "stats":
        step_stats()
    elif args.step == "export":
        step_export()
