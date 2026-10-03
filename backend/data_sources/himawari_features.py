"""
backend/data_sources/himawari_features.py
============================================
Phase 2 — real Himawari-9 satellite feature extractor.

TRACED SOURCE (from fetch_himawari_realtime.py at the repo root; this
module does not re-implement or duplicate that fetch logic, it only reads
what that fetcher already writes):

  Product           : AHI-L1b-FLDK (Himawari-9 AHI, full-disk, Level-1b),
                       R20 resolution segments, HSD format (.DAT.bz2),
                       parsed with satpy's 'ahi_hsd' reader.
  Band              : Band 13 ONLY (10.4 micron IR window channel).
                       No other band is fetched by the existing pipeline --
                       in particular, NO water-vapor channel (B08/B09/B10,
                       ~6.2/6.9/7.3 micron) is ever downloaded. Any
                       "water vapor" feature below is therefore reported
                       UNAVAILABLE, never fabricated.
  Spatial coverage  : cropped to a 50km radius around VOBL (13.1986N,
                       77.7066E) from segments 4-6 of the full disk.
  Pixel resolution  : R20 product -> 2km nominal at the Himawari-9
                       sub-satellite point (140.7E); VOBL is well off-nadir
                       so the true ground-projected pixel footprint is
                       coarser than 2km in practice (not independently
                       re-derived here -- stated as "2km nominal (R20)").
  Primary source    : NOAA S3 (s3://noaa-himawari9, anonymous/unsigned).
  Fallback source   : JAXA P-Tree HTTP
                       (https://www.eorc.jaxa.jp/ptree/userspace/FULL/GEO/HIMAWARI/B13/FLDK).
  Failure behavior  : if BOTH S3 and JAXA fail for both the latest and one
                       prior 10-min slot, the fetcher writes an explicit
                       placeholder record with data_source containing
                       "UNAVAILABLE" and every numeric field set to None --
                       it never fabricates a brightness temperature.
  Persisted output  : data/himawari_realtime.json (latest frame, scalar
                       fields only -- the raw pixel array is NOT persisted,
                       only summary statistics computed by the fetcher's
                       own analyse() function: vobl_bt_celsius, min_bt_50km,
                       mean_bt_50km, cold_pixels_count, storm_detected,
                       nearest_pixel_dist_km, bt_trend_1h) and
                       data/himawari_history.json (rolling buffer of the
                       last 6 frames, one record per fetcher run).
  Actual cadence    : the AHI full-disk instrument natively repeats every
                       10 minutes, and the fetcher's own docstring assumes
                       a 10-min cron. In practice (see
                       data/himawari_history.json as committed in this
                       repo), the production schedule is gated by
                       forecast_update.yml's ~5x/day cadence, so real
                       consecutive frames are OFTEN more than 10 minutes
                       apart, sometimes by hours. This module NEVER assumes
                       a frame exists at exactly t-10/20/30 minutes --- it
                       searches the real history for the closest actual
                       frame to each target offset and only accepts a match
                       within OFFSET_TOLERANCE_MINUTES.

==============================================================================
SCIENTIFIC TERMINOLOGY NOTE (read before touching any UI label)
==============================================================================
The existing project (fetch_himawari_realtime.py, forecast.json, index.html,
docs/LIVE_OPERATIONAL_AUDIT.md) and the separate GFS-derived field in
backend/pipeline.py both use the term "CTT" (cloud-top temperature).

For the Himawari Band 13 values specifically: what is actually measured is
the TOP-OF-ATMOSPHERE BRIGHTNESS TEMPERATURE in the 10.4 micron IR window
channel -- i.e. the equivalent blackbody temperature of the radiance the
sensor receives, uncorrected for atmospheric absorption/emission, cloud
emissivity (<1 for thin cirrus), and viewing-angle path length. No cloud-top
retrieval algorithm (e.g. CO2-slicing, H2O-intercept, or a radiative-transfer
-based cloud-top height/temperature retrieval) is applied anywhere in this
codebase. For optically thick, high clouds (as in deep convection) raw IR
brightness temperature is a reasonable and widely-used operational PROXY for
cloud-top temperature, but it is not the same thing as a validated CTT
retrieval, and it reads noticeably warmer than true cloud-top temperature
for thin cirrus or partially-filled pixels.

Conclusion: the existing "CTT" terminology is a defensible shorthand ONLY
in the sense that IR brightness temperature is a standard, if imprecise,
operational proxy for cloud-top temperature in nowcasting -- it is NOT a
scientifically rigorous cloud-top temperature retrieval. This module
therefore names its own fields `*_brightness_temperature_*` or
`ir_cloud_temperature_proxy_*` rather than bare "CTT", and does not
silently rename anything in the existing UI/pipeline (`backend/pipeline.py`,
`index.html`) -- that is an explicit non-goal of this phase, left for an
explicit later decision once this document has been reviewed.
"""
from __future__ import annotations

import dataclasses
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import numpy as _np
except ImportError:  # pragma: no cover -- numpy is a repo-wide dependency already
    _np = None

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REALTIME_PATH = REPO_ROOT / "data" / "himawari_realtime.json"
HISTORY_PATH = REPO_ROOT / "data" / "himawari_history.json"

SATELLITE = "Himawari-9"
INSTRUMENT = "AHI (Advanced Himawari Imager)"
BAND_PRODUCT = "Band 13 (10.4um IR window), AHI-L1b-FLDK R20"

# Matches fetch_himawari_realtime.py's own RADIUS_KM and THRESHOLD_C exactly
# -- this module never invents its own analysis geometry or threshold, it
# reuses the fetcher's real, already-deployed definitions.
ANALYSIS_RADIUS_KM = 50.0
COLD_PIXEL_THRESHOLD_C = -40.0
# -40C is the fetcher's own pragmatic deep-convection detection threshold
# (used for storm_detected). It has not been independently validated here
# as a cloud-mask retrieval threshold (that would require a radiative
# transfer-based cloud/no-cloud classification), so features derived from
# it are named "cold_pixel_*", never "cloud_fraction" or "cloud_mask".

# Physically plausible IR window brightness-temperature range for an Earth
# scene (Kelvin). Outside this range is almost certainly a corrupt or
# garbage pixel, never a real observation -- used for QC flagging only,
# never to "correct" a value.
BT_MIN_PLAUSIBLE_K = 170.0
BT_MAX_PLAUSIBLE_K = 340.0

NOMINAL_PIXEL_KM = 2.0            # R20 product nominal nadir resolution
NATIVE_CADENCE_MINUTES = 10       # Himawari-9 AHI full-disk native repeat cycle

TARGET_OFFSETS_MINUTES = (10, 20, 30)
OFFSET_TOLERANCE_MINUTES = 3.0    # how close a real frame must be to "count" as t-N

STALE_MINUTES = 6 * 60            # matches HimawariSource.CACHED_MAX_MINUTES

# Heuristic-only, not independently validated against a labeled event set
# in this repository -- flagged explicitly wherever used.
RAPID_COOLING_THRESHOLD_C_PER_HOUR = 15.0


class HimawariFeatureError(ValueError):
    """Raised only for programmer errors (bad arguments), never for data problems --
    data problems become quality_flags, not exceptions."""


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_ts(ts: str) -> str:
    dt = _parse_ts(ts)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ts


def _c_to_k(c: Optional[float]) -> Optional[float]:
    return None if c is None else c + 273.15


def _safe_float(val) -> Tuple[Optional[float], Optional[str]]:
    """Returns (value, flag). flag is set for non-numeric/NaN/Inf input."""
    if val is None:
        return None, None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None, "not_numeric"
    if math.isnan(f):
        return None, "nan"
    if math.isinf(f):
        return None, "inf"
    return f, None


def _load_json(path: Path):
    """Returns (data, error_message_or_None). Never raises."""
    if not path.exists():
        return None, f"{path} does not exist"
    try:
        with open(path) as f:
            return json.load(f), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{path} could not be parsed: {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Real pixel-level spatial statistics
# ---------------------------------------------------------------------------
#
# fetch_himawari_realtime.py's analyse() function computes a 50km-radius
# haversine mask over the satpy-derived lat/lon grid and a brightness-
# temperature array, then reduces it to a handful of scalars (vobl_bt,
# min_bt, mean_bt, cold_pixels_count) before discarding the pixel array --
# nothing below reconstructs those scalars from thin air. This function
# performs the SAME masking the fetcher already does, directly on a raw
# {"bt", "lats", "lons"} array dict of the kind fetch_segments_s3() /
# fetch_segments_jaxa() already return in-process -- it is designed to be
# called with that real array right after a fetch, in the same process,
# before the array is discarded. It is NOT wired into fetch_himawari_
# realtime.py in this phase (that file is left untouched per the Phase 2
# scope), so in practice, today, callers that only have the persisted
# JSON (no raw array) will get every field below as None with an explicit
# "no raw array available" flag -- this function exists so a *future*
# change that does pass the real array through gets real statistics
# immediately, with no further design work, and so this capability can be
# unit-tested now against deterministic synthetic arrays.

def compute_spatial_statistics_from_pixels(
    bt_kelvin: "_np.ndarray",
    lats: "_np.ndarray",
    lons: "_np.ndarray",
    center_lat: float,
    center_lon: float,
    radius_km: float = ANALYSIS_RADIUS_KM,
    cold_threshold_c: float = COLD_PIXEL_THRESHOLD_C,
) -> Tuple[Dict[str, object], List[str]]:
    """
    Compute real, pixel-traceable spatial statistics from an actual
    brightness-temperature array and its real lat/lon grid (the same
    satpy-derived arrays fetch_himawari_realtime.py already produces
    in-process). Returns (stats_dict, quality_flags). Never fabricates --
    if numpy is unavailable or the array is empty/all-NaN, returns a
    stats dict of None values with an explicit flag, never zeros.
    """
    flags: List[str] = []
    stats: Dict[str, object] = {
        "valid_pixel_count_50km": None,
        "cold_pixel_count_50km_raw": None,
        "cold_pixel_fraction_50km": None,
        "brightness_temperature_min_c": None,
        "brightness_temperature_mean_c": None,
        "brightness_temperature_std_c": None,
        "brightness_temperature_p10_c": None,
        "brightness_temperature_p25_c": None,
        "brightness_temperature_p50_c": None,
        "brightness_temperature_p75_c": None,
        "brightness_temperature_p90_c": None,
        "spatial_gradient_std_c": None,
    }

    if _np is None:
        flags.append("numpy_unavailable_spatial_statistics_skipped")
        return stats, flags

    if bt_kelvin is None or lats is None or lons is None:
        flags.append("raw_pixel_array_not_provided")
        return stats, flags

    bt = _np.asarray(bt_kelvin, dtype=float)
    lat_arr = _np.asarray(lats, dtype=float)
    lon_arr = _np.asarray(lons, dtype=float)

    if bt.shape != lat_arr.shape or bt.shape != lon_arr.shape:
        flags.append("pixel_array_shape_mismatch")
        return stats, flags
    if bt.size == 0:
        flags.append("pixel_array_empty")
        return stats, flags

    # Real haversine distance from every pixel to the center point -- the
    # exact method fetch_himawari_realtime.py's analyse() already uses.
    dlat = _np.radians(lat_arr - center_lat)
    dlon = _np.radians(lon_arr - center_lon)
    a = (_np.sin(dlat / 2) ** 2
         + _np.cos(_np.radians(center_lat)) * _np.cos(_np.radians(lat_arr))
         * _np.sin(dlon / 2) ** 2)
    dist_km = 6371.0 * 2 * _np.arcsin(_np.sqrt(_np.clip(a, 0, 1)))

    mask = (dist_km <= radius_km) & _np.isfinite(bt)
    # Reject pixels outside the physically plausible IR window range --
    # corrupt/garbage pixels must not silently pollute a percentile or std.
    implausible = mask & ((bt < BT_MIN_PLAUSIBLE_K) | (bt > BT_MAX_PLAUSIBLE_K))
    n_implausible = int(_np.sum(implausible))
    if n_implausible:
        flags.append(f"excluded_{n_implausible}_pixels_outside_physically_plausible_range")
    mask = mask & ~implausible

    valid = bt[mask]
    n_valid = int(valid.size)
    stats["valid_pixel_count_50km"] = n_valid

    if n_valid == 0:
        flags.append("no_valid_pixels_within_radius")
        return stats, flags

    valid_c = valid - 273.15
    cold_mask_c = valid_c < cold_threshold_c
    n_cold = int(_np.sum(cold_mask_c))

    stats["cold_pixel_count_50km_raw"] = n_cold
    stats["cold_pixel_fraction_50km"] = round(n_cold / n_valid, 4)
    stats["brightness_temperature_min_c"] = round(float(_np.min(valid_c)), 2)
    stats["brightness_temperature_mean_c"] = round(float(_np.mean(valid_c)), 2)
    stats["brightness_temperature_std_c"] = round(float(_np.std(valid_c)), 2)
    for p in (10, 25, 50, 75, 90):
        stats[f"brightness_temperature_p{p}_c"] = round(float(_np.percentile(valid_c, p)), 2)

    # Simple texture proxy: std of the local gradient magnitude over the
    # masked region (not a published texture algorithm like GLCM -- a
    # defensible, cheap, well-documented proxy for spatial heterogeneity,
    # named accordingly rather than as a validated "texture" metric).
    if n_valid >= 9:  # need enough pixels for gradient() to be meaningful
        bt_for_grad = _np.where(mask, bt, _np.nan)
        try:
            gy, gx = _np.gradient(bt_for_grad)
            grad_mag = _np.sqrt(gx ** 2 + gy ** 2)
            grad_valid = grad_mag[mask & _np.isfinite(grad_mag)]
            if grad_valid.size > 0:
                stats["spatial_gradient_std_c"] = round(float(_np.std(grad_valid)), 3)
        except Exception:  # pragma: no cover -- defensive only, never fatal
            flags.append("spatial_gradient_computation_failed")
    else:
        flags.append("too_few_valid_pixels_for_texture_metric")

    return stats, flags


# ---------------------------------------------------------------------------
# Per-frame QC
# ---------------------------------------------------------------------------

def _validate_frame(frame: dict) -> List[str]:
    """Inspect one raw fetcher-output record and return quality flags.
    Never raises, never mutates the frame."""
    flags: List[str] = []

    dt = _parse_ts(frame.get("timestamp_utc"))
    if dt is None:
        flags.append("invalid_or_missing_timestamp")

    data_source = str(frame.get("data_source") or "").lower()
    if "unavailable" in data_source:
        flags.append("source_reported_unavailable")
    if "stale" in data_source:
        flags.append("source_reported_stale_fallback")

    for key in ("vobl_bt_celsius", "min_bt_50km", "mean_bt_50km"):
        raw = frame.get(key)
        val, numeric_flag = _safe_float(raw)
        if numeric_flag:
            flags.append(f"{key}_{numeric_flag}")
            continue
        if val is None:
            continue
        k = _c_to_k(val)
        if k < BT_MIN_PLAUSIBLE_K or k > BT_MAX_PLAUSIBLE_K:
            flags.append(f"{key}_outside_physically_plausible_range")

    if frame.get("vobl_bt_celsius") is None and "unavailable" not in data_source:
        flags.append("missing_vobl_pixel")
    if frame.get("min_bt_50km") is None and "unavailable" not in data_source:
        flags.append("missing_spatial_coverage_50km")

    cold_count = frame.get("cold_pixels_count")
    if cold_count is not None and (not isinstance(cold_count, int) or cold_count < 0):
        flags.append("cold_pixels_count_invalid")

    return flags


def _dedupe_and_sort(frames: List[dict]) -> Tuple[List[Tuple[datetime, dict]], List[str]]:
    """Parse timestamps, drop frames with unparseable timestamps (flagged
    elsewhere via _validate_frame), drop exact-timestamp duplicates (keep
    first occurrence), and sort ascending by real timestamp. Never
    reorders/alters field VALUES -- only reorders/dedupes whole records by
    their own stated timestamp."""
    flags: List[str] = []
    seen = set()
    dated: List[Tuple[datetime, dict]] = []
    original_order: List[datetime] = []

    for fr in frames:
        dt = _parse_ts(fr.get("timestamp_utc"))
        if dt is None:
            continue
        original_order.append(dt)
        key = dt.isoformat()
        if key in seen:
            flags.append("duplicate_timestamp_dropped")
            continue
        seen.add(key)
        dated.append((dt, fr))

    was_sorted = all(
        original_order[i] <= original_order[i + 1] for i in range(len(original_order) - 1)
    )
    dated.sort(key=lambda pair: pair[0])
    if not was_sorted:
        flags.append("frames_were_out_of_order_corrected")

    return dated, flags


def _find_nearest(
    dated_frames: List[Tuple[datetime, dict]],
    target_dt: datetime,
    exclude_dt: Optional[datetime],
    tolerance_minutes: float,
) -> Tuple[Optional[Tuple[datetime, dict]], Optional[float]]:
    """Closest real frame to target_dt, excluding exclude_dt itself. Returns
    (None, best_gap_or_None) if nothing is within tolerance -- NEVER
    interpolates or fabricates a frame."""
    best = None
    best_gap = None
    for dt, fr in dated_frames:
        if exclude_dt is not None and dt == exclude_dt:
            continue
        gap = abs((dt - target_dt).total_seconds()) / 60.0
        if best_gap is None or gap < best_gap:
            best_gap, best = gap, (dt, fr)
    if best is None or best_gap > tolerance_minutes:
        return None, best_gap
    return best, best_gap


# ---------------------------------------------------------------------------
# Result contract
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class HimawariFeatureSet:
    source_name: str
    satellite: str
    instrument: str
    band_product: str
    observation_time_utc: Optional[str]
    acquisition_time_utc: Optional[str]
    feature_values: Dict[str, object]
    temporal_coverage: Dict[str, object]
    spatial_resolution: str
    quality_flags: List[str]
    provenance: List[str]

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _empty_feature_values() -> Dict[str, object]:
    return {
        # Single-frame / current-time
        "brightness_temperature_vobl_c": None,
        "ir_cloud_temperature_proxy_min_50km_c": None,
        "ir_cloud_temperature_proxy_mean_50km_c": None,
        "cold_pixel_count_50km": None,
        "storm_detected_threshold_minus40c": None,
        "nearest_cold_pixel_km": None,
        "water_vapor_brightness_temperature_c": None,

        # Temporal -- fixed-offset view (explicit per-offset drop; the
        # offset is in the field name AND temporal_coverage carries the
        # real gap/timestamp used, so there is never ambiguity about what
        # interval a number actually spans)
        "brightness_temperature_drop_10m_c": None,
        "brightness_temperature_drop_20m_c": None,
        "brightness_temperature_drop_30m_c": None,
        "cooling_rate_c_per_hour_by_offset": {},

        # Temporal -- single "headline" best-matched window. Deliberately
        # NOT named with an assumed interval (no "_1h", no "_10m") --
        # brightness_temperature_drop_interval_minutes always carries the
        # real elapsed time this specific drop/rate was computed over.
        "brightness_temperature_drop_c": None,
        "brightness_temperature_drop_interval_minutes": None,
        "cooling_rate_c_per_hour": None,
        "rapid_cloud_growth_flag": None,

        # Spatial pixel-level statistics -- only populated when a real raw
        # pixel array is supplied to extract_himawari_features(); see
        # compute_spatial_statistics_from_pixels() above.
        "analysis_radius_km": ANALYSIS_RADIUS_KM,
        "pixel_resolution_km": NOMINAL_PIXEL_KM,
        "valid_pixel_count_50km": None,
        "cold_pixel_fraction_50km": None,
        "brightness_temperature_std_c": None,
        "brightness_temperature_p10_c": None,
        "brightness_temperature_p25_c": None,
        "brightness_temperature_p50_c": None,
        "brightness_temperature_p75_c": None,
        "brightness_temperature_p90_c": None,
        "spatial_gradient_std_c": None,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def extract_himawari_features(
    realtime_path: Path = REALTIME_PATH,
    history_path: Path = HISTORY_PATH,
    now: Optional[datetime] = None,
    raw_pixels: Optional[Dict[str, object]] = None,
) -> HimawariFeatureSet:
    """
    Extract real, traceable Himawari-9 Band 13 features from the existing
    fetcher's persisted output. Makes no network call and reads no raw
    satellite file by default -- it is a feature layer over
    fetch_himawari_realtime.py's already-written JSON, exactly like the
    Phase 1 HimawariSource adapter, but producing richer, QC'd,
    temporally-aware features rather than a single freshness verdict.

    raw_pixels: optional {"bt": array, "lats": array, "lons": array} of the
    exact kind fetch_segments_s3()/fetch_segments_jaxa() already return
    in-process. When supplied (e.g. by a future caller that runs this
    right after a live fetch, before the array is discarded), real
    pixel-level spatial statistics (cold-pixel fraction, percentiles, a
    texture proxy) are computed via compute_spatial_statistics_from_pixels()
    and merged in. This Phase 2 pass does NOT modify
    fetch_himawari_realtime.py to pass this through automatically -- that
    would be a production-fetcher change outside this phase's scope and
    untestable without live network access from this environment. Without
    raw_pixels, every pixel-level field is None with an explicit flag.
    """
    now = now or datetime.now(timezone.utc)
    quality_flags: List[str] = []
    provenance: List[str] = []
    feature_values = _empty_feature_values()
    temporal_coverage: Dict[str, object] = {
        "frames_available": 0,
        "frames_used_utc": [],
        "requested_offsets_minutes": list(TARGET_OFFSETS_MINUTES),
        "offsets_satisfied_minutes": [],
        "offsets_unavailable_minutes": list(TARGET_OFFSETS_MINUTES),
        # actual_gap_minutes = true elapsed time between the latest frame
        # and the frame actually used for that offset (NOT the distance
        # between that frame and the nominal target -- that distinction
        # matters: a frame found at t-18m for a "20m" target has an actual
        # gap of 18 minutes, not 2). Initialized to None for every offset
        # up front so the single-frame case also reports them explicitly.
        "offset_actual_gap_minutes": {str(o): None for o in TARGET_OFFSETS_MINUTES},
        "offset_actual_frame_time_utc": {str(o): None for o in TARGET_OFFSETS_MINUTES},
    }

    if raw_pixels is not None:
        spatial_stats, spatial_flags = compute_spatial_statistics_from_pixels(
            raw_pixels.get("bt"), raw_pixels.get("lats"), raw_pixels.get("lons"),
            center_lat=13.1986, center_lon=77.7066,  # VOBL -- matches fetch_himawari_realtime.py exactly
        )
        feature_values["valid_pixel_count_50km"] = spatial_stats["valid_pixel_count_50km"]
        feature_values["cold_pixel_fraction_50km"] = spatial_stats["cold_pixel_fraction_50km"]
        feature_values["brightness_temperature_std_c"] = spatial_stats["brightness_temperature_std_c"]
        feature_values["brightness_temperature_p10_c"] = spatial_stats["brightness_temperature_p10_c"]
        feature_values["brightness_temperature_p25_c"] = spatial_stats["brightness_temperature_p25_c"]
        feature_values["brightness_temperature_p50_c"] = spatial_stats["brightness_temperature_p50_c"]
        feature_values["brightness_temperature_p75_c"] = spatial_stats["brightness_temperature_p75_c"]
        feature_values["brightness_temperature_p90_c"] = spatial_stats["brightness_temperature_p90_c"]
        feature_values["spatial_gradient_std_c"] = spatial_stats["spatial_gradient_std_c"]
        quality_flags.extend(f"spatial_pixel_stats_{f}" for f in spatial_flags)
        provenance.append("spatial pixel statistics computed from a real raw_pixels array supplied by the caller")
    else:
        quality_flags.append("spatial_pixel_statistics_unavailable_no_raw_array_provided_this_run")

    realtime, realtime_err = _load_json(realtime_path)
    if realtime_err:
        quality_flags.append("no_realtime_data" if "does not exist" in realtime_err else "corrupt_realtime_file")
        provenance.append(f"{realtime_path}: {realtime_err}")
        realtime = None
    else:
        provenance.append(f"{realtime_path} (reused from fetch_himawari_realtime.py)")

    history, history_err = _load_json(history_path)
    if history_err:
        quality_flags.append("no_history_data" if "does not exist" in history_err else "corrupt_history_file")
        provenance.append(f"{history_path}: {history_err}")
        history = []
    else:
        provenance.append(f"{history_path} (reused from fetch_himawari_realtime.py)")
        if not isinstance(history, list):
            quality_flags.append("corrupt_history_file")
            history = []

    # Build the combined frame list in natural chronological append order
    # (history, then realtime) so that an already-ordered on-disk history
    # file is never spuriously flagged "out of order" purely because of
    # how this function concatenates its two input files. The realtime
    # frame is normally just a duplicate of history's own last entry
    # (save_outputs() appends it there each run) and gets deduped below;
    # on a first-ever run (empty history) it is the only frame.
    all_raw_frames: List[dict] = list(history)
    if realtime:
        all_raw_frames.append(realtime)

    if not all_raw_frames:
        quality_flags.append("no_data_available")
        return HimawariFeatureSet(
            source_name="himawari_features",
            satellite=SATELLITE,
            instrument=INSTRUMENT,
            band_product=BAND_PRODUCT,
            observation_time_utc=None,
            acquisition_time_utc=None,
            feature_values=feature_values,
            temporal_coverage=temporal_coverage,
            spatial_resolution=f"{NOMINAL_PIXEL_KM} km nominal (R20), 50km VOBL subset only",
            quality_flags=quality_flags,
            provenance=provenance,
        )

    # Per-frame QC (collected but not fatal -- feed into the aggregate list)
    for fr in all_raw_frames:
        for flag in _validate_frame(fr):
            if flag not in quality_flags:
                quality_flags.append(flag)

    dated_frames, dedupe_flags = _dedupe_and_sort(all_raw_frames)
    for flag in dedupe_flags:
        if flag not in quality_flags:
            quality_flags.append(flag)

    if not dated_frames:
        quality_flags.append("no_data_available")
        return HimawariFeatureSet(
            source_name="himawari_features",
            satellite=SATELLITE,
            instrument=INSTRUMENT,
            band_product=BAND_PRODUCT,
            observation_time_utc=None,
            acquisition_time_utc=None,
            feature_values=feature_values,
            temporal_coverage=temporal_coverage,
            spatial_resolution=f"{NOMINAL_PIXEL_KM} km nominal (R20), 50km VOBL subset only",
            quality_flags=quality_flags,
            provenance=provenance,
        )

    latest_dt, latest_frame = dated_frames[-1]
    temporal_coverage["frames_available"] = len(dated_frames)
    temporal_coverage["frames_used_utc"] = [dt.strftime("%Y-%m-%dT%H:%M:%SZ") for dt, _ in dated_frames]

    # Cross-check realtime vs the max of history, if both present
    if realtime is not None and history:
        realtime_dt = _parse_ts(realtime.get("timestamp_utc"))
        hist_dts = [_parse_ts(fr.get("timestamp_utc")) for fr in history]
        hist_dts = [d for d in hist_dts if d is not None]
        if realtime_dt and hist_dts and realtime_dt < max(hist_dts):
            quality_flags.append("realtime_older_than_latest_history_frame")

    # Staleness, based on the actual latest real observation time
    freshness_minutes = (now - latest_dt).total_seconds() / 60.0
    if freshness_minutes > STALE_MINUTES:
        quality_flags.append("stale_imagery")
    if freshness_minutes < 0:
        quality_flags.append("observation_time_in_the_future")

    latest_source = str(latest_frame.get("data_source") or "")
    if "unavailable" in latest_source.lower():
        quality_flags.append("latest_frame_unavailable_placeholder")

    # --- Single-frame (current-time) features -----------------------------
    vobl_bt, _ = _safe_float(latest_frame.get("vobl_bt_celsius"))
    min_bt, _ = _safe_float(latest_frame.get("min_bt_50km"))
    mean_bt, _ = _safe_float(latest_frame.get("mean_bt_50km"))
    cold_count = latest_frame.get("cold_pixels_count")
    storm_detected = latest_frame.get("storm_detected")
    nearest_km, _ = _safe_float(latest_frame.get("nearest_pixel_dist_km"))

    feature_values["brightness_temperature_vobl_c"] = vobl_bt
    feature_values["ir_cloud_temperature_proxy_min_50km_c"] = min_bt
    feature_values["ir_cloud_temperature_proxy_mean_50km_c"] = mean_bt
    feature_values["cold_pixel_count_50km"] = cold_count if isinstance(cold_count, int) else None
    feature_values["storm_detected_threshold_minus40c"] = bool(storm_detected) if storm_detected is not None else None
    feature_values["nearest_cold_pixel_km"] = nearest_km

    # Water vapor: the fetcher never ingests a WV band -- always unavailable.
    # Per Phase 2 hardening: if a verified WV endpoint/band ever becomes
    # testable, investigate B08/B09/B10 then -- never guess a key now.
    quality_flags.append("water_vapor_channel_unavailable")

    if min_bt is None:
        quality_flags.append("single_frame_features_incomplete_missing_min_bt")

    # --- Temporal features (only using REAL matched frames) ---------------
    if len(dated_frames) < 2:
        quality_flags.append("temporal_features_unavailable_single_frame_only")
    else:
        offset_drop_keys = {
            10: "brightness_temperature_drop_10m_c",
            20: "brightness_temperature_drop_20m_c",
            30: "brightness_temperature_drop_30m_c",
        }
        satisfied = []
        unavailable = []
        cooling_by_offset: Dict[str, float] = {}

        for offset in TARGET_OFFSETS_MINUTES:
            target = latest_dt - timedelta(minutes=offset)
            # target_distance_minutes is ONLY used to decide whether a
            # candidate frame is close enough to the nominal offset to
            # count as "the t-Nm frame" at all (the tolerance check). It is
            # NOT the same thing as the real elapsed time between the two
            # frames actually used -- that is actual_gap_minutes below,
            # which is what gets stored/reported and used for the rate.
            match, target_distance_minutes = _find_nearest(
                dated_frames, target, exclude_dt=latest_dt, tolerance_minutes=OFFSET_TOLERANCE_MINUTES
            )

            if match is None:
                unavailable.append(offset)
                continue

            match_dt, match_frame = match
            actual_gap_minutes = round((latest_dt - match_dt).total_seconds() / 60.0, 2)
            temporal_coverage["offset_actual_gap_minutes"][str(offset)] = actual_gap_minutes
            temporal_coverage["offset_actual_frame_time_utc"][str(offset)] = match_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            match_bt, _ = _safe_float(match_frame.get("min_bt_50km"))
            if match_bt is None or min_bt is None:
                unavailable.append(offset)
                continue

            if actual_gap_minutes <= 0:
                unavailable.append(offset)
                continue

            drop = round(match_bt - min_bt, 2)  # positive = cooled since that frame
            feature_values[offset_drop_keys[offset]] = drop
            rate = round(drop / (actual_gap_minutes / 60.0), 2)
            cooling_by_offset[str(offset)] = rate
            satisfied.append(offset)

        temporal_coverage["offsets_satisfied_minutes"] = satisfied
        temporal_coverage["offsets_unavailable_minutes"] = unavailable
        feature_values["cooling_rate_c_per_hour_by_offset"] = cooling_by_offset

        if cooling_by_offset:
            # Prefer the offset with the actual gap closest to its nominal
            # target (best match) as the single headline window. The
            # headline fields always carry their OWN actual interval
            # explicitly -- never an assumed "1h"/"10m" label.
            best_offset = min(
                satisfied,
                key=lambda o: abs((temporal_coverage["offset_actual_gap_minutes"][str(o)] or 1e9) - o),
            )
            best_gap_minutes = temporal_coverage["offset_actual_gap_minutes"][str(best_offset)]
            headline_rate = cooling_by_offset[str(best_offset)]
            headline_drop = feature_values[offset_drop_keys[best_offset]]
            feature_values["brightness_temperature_drop_c"] = headline_drop
            feature_values["brightness_temperature_drop_interval_minutes"] = best_gap_minutes
            feature_values["cooling_rate_c_per_hour"] = headline_rate
            feature_values["rapid_cloud_growth_flag"] = headline_rate >= RAPID_COOLING_THRESHOLD_C_PER_HOUR
            quality_flags.append(f"headline_window_is_t_minus_{best_offset}m_target_actual_gap_{best_gap_minutes}m")
        else:
            quality_flags.append("no_temporal_offset_within_tolerance")

        if unavailable:
            quality_flags.append(f"offsets_unavailable_{','.join(str(o) for o in unavailable)}m_no_frame_within_tolerance")

    provenance.append(
        f"latest observation frame used: {latest_dt.strftime('%Y-%m-%dT%H:%M:%SZ')} "
        f"(data_source={latest_source!r})"
    )
    if temporal_coverage["offsets_satisfied_minutes"]:
        for offset in temporal_coverage["offsets_satisfied_minutes"]:
            gap = temporal_coverage["offset_actual_gap_minutes"].get(str(offset))
            provenance.append(f"t-{offset}m matched to a real frame with actual gap {gap}m (tolerance {OFFSET_TOLERANCE_MINUTES}m)")

    return HimawariFeatureSet(
        source_name="himawari_features",
        satellite=SATELLITE,
        instrument=INSTRUMENT,
        band_product=BAND_PRODUCT,
        observation_time_utc=_normalize_ts(latest_frame.get("timestamp_utc")),
        acquisition_time_utc=_normalize_ts(latest_frame.get("timestamp_utc")),
        feature_values=feature_values,
        temporal_coverage=temporal_coverage,
        spatial_resolution=f"{NOMINAL_PIXEL_KM} km nominal (R20 product); 50km-radius VOBL subset only, not pan-India",
        quality_flags=quality_flags,
        provenance=provenance,
    )


__all__ = ["HimawariFeatureSet", "HimawariFeatureError", "extract_himawari_features"]
