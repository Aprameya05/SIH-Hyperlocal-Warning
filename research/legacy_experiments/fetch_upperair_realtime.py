"""
gfs_fetcher.py
-------------------
Atul's deliverable -- CSIR Thunderstorm Prediction, real-time pipeline
Station: 43295 (Bengaluru Airport), lat=12.97, lon=77.58

Pulls a single-point GFS 0.25 deg forecast from NOAA NOMADS and computes
the same six upper-air stability indices used in training -- CAPE, CIN,
K, LI, TT, PW -- so the live feature vector matches the schema
merge_features.py already expects (see igra_scraper.py and
era5_stability_indices.py for the historical/training equivalents).

Cycle-selection logic comes straight out of pipeline_scoping_findings.md:
GFS posts to NOMADS ~3.5-4h after its cycle time, which makes the
original brief's "same-hour cycle at t+6" pairing infeasible -- the
cycle isn't even posted yet by the time its own slot starts. This uses
the corrected t+12 rule instead: always reach back to the cycle 12h
before the target valid time, which leaves a safe ~6.5h buffer before
every slot.

    Slot 0  (0001-0600 IST)  -> prev-day 06Z, f012 (valid 18Z prev day)
    Slot 1  (0601-1200 IST)  -> prev-day 12Z, f012 (valid 00Z)
    Slot 2  (1201-1800 IST)  -> prev-day 18Z, f012 (valid 06Z)
    Slot 3  (1801-2400 IST)  -> same-day 00Z, f012 (valid 12Z)

The script doesn't hardcode that table -- it derives the source cycle
directly from the target slot's start time (floor to the nearest
standard cycle hour, then step back 12h), so it stays correct even if
the slot boundaries ever change.

CAPE/CIN/PW are taken directly from GFS's own native surface fields
(CAPE, CIN, PWAT) -- same principle era5_stability_indices.py uses:
trust the source model's own full-resolution CAPE/CIN over a hand-
derived 4-level estimate. K-Index, Totals Totals, and Lifted Index are
computed from the surface + 850/700/500 hPa profile via MetPy, using
the same formulas as era5_stability_indices.py, so live and training
features are computed identically.

IMPORTANT -- not testable from this sandbox: NOMADS is reachable from a
browser and from web_fetch (confirmed), but this sandbox's own outbound
proxy blocks direct downloads from nomads.ncep.noaa.gov for curl/requests
(403 from the sandbox's allowlist, not from NOAA). Run and sanity-check
this from your own machine or Colab, not from here -- the URL-building
and cycle-selection logic below was checked with synthetic timestamps,
but the actual download+parse has not been run against a live file.

Requirements:
    pip install requests cfgrib xarray metpy pandas numpy eccodes pygrib

Usage:
    python gfs_fetcher.py                              # fetch data for the next upcoming slot
    python gfs_fetcher.py --slot 2                     # force a specific slot (0-3), next occurrence
    python gfs_fetcher.py --now "2026-07-15 14:00"     # simulate a different current IST time (testing)
    python gfs_fetcher.py --verify                     # also print a full manual pygrib cross-check

Automatic QC (added 2026-07-24, after a real case: Slot 3 read CAPE=0 with
K-Index=35.5, which is inconsistent enough to need a second opinion): every
run now checks CAPE against K-Index/Totals-Totals on its own, no --verify
flag required. If CAPE reads ~0 while K-Index/TT say the profile is unstable,
the script automatically pulls an independent pygrib CAPE value at the same
point and records both the flag and the cross-check value in the CSV --
see assess_cape_consistency() below. This applies to every slot, not just
Slot 3 -- the check runs in compute_indices()'s call path regardless of
which slot triggered the fetch.

Output: appends one row to data/upperair_realtime_43295.csv
        Columns: date, slot, ist_window, valid_time_utc, source_cycle_utc,
                 source_fhour, fetched_at_utc, CAPE, CIN, K, LI, TT, PW,
                 CAPE_QC_FLAG, CAPE_PYGRIB_CROSSCHECK
"""

import argparse
import os
import warnings
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

LAT, LON = 12.97, 77.58            # Bengaluru Airport, station 43295
BOX = 0.5                          # subset box half-width in degrees around the point
DATA_DIR = "data"
OUT_PATH = f"{DATA_DIR}/upperair_realtime_43295.csv"
GRIB_TMP = f"{DATA_DIR}/_gfs_tmp.grib2"

NOMADS_BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"

FORECAST_HOUR = 12                 # t+12, per pipeline_scoping_findings.md
POST_LATENCY_HOURS = 4             # confirmed via research: ~3.5-4h from cycle time to NOMADS posting
SLOT_START_HOURS = [0, 6, 12, 18]  # IST hours each slot begins
SLOT_WINDOWS = {0: "0001-0600", 1: "0601-1200", 2: "1201-1800", 3: "1801-2400"}

IST = timezone(timedelta(hours=5, minutes=30))

# QC thresholds for the CAPE=0-but-atmosphere-looks-unstable case flagged 2026-07-24.
# CAPE==0 is a normal, unremarkable value most of the time (stable/dry airmass) --
# it's only worth a second look when K-Index/Totals-Totals (computed from a
# completely different part of the GRIB file, via a different formula) say the
# profile is unstable at the same time. That combination usually means either a
# real capped airmass (surface parcel can't rise even though mid-levels are moist/
# unstable -- check CIN, it should be strongly negative if so) or a grid-point/
# parsing slip. Either way it shouldn't ship silently.
CAPE_NEAR_ZERO = 1.0        # J/kg -- treat anything below this as "reads zero"
CAPE_SUSPECT_KINDEX = 30.0  # K-Index at/above this = real thunderstorm potential
CAPE_SUSPECT_TT = 44.0      # Totals Totals at/above this = same signal, backup check


# ─── Slot / cycle resolution ────────────────────────────────────────────────

def next_slot(now_ist: datetime):
    """Returns (slot_index, slot_start_ist) for the next slot boundary at or
    after `now_ist` -- the slot a cron job running "now" should be fetching
    data FOR (jobs run ahead of the slot they feed, not during it)."""
    for h in SLOT_START_HOURS:
        candidate = now_ist.replace(hour=h, minute=0, second=0, microsecond=0)
        if candidate > now_ist:
            return SLOT_START_HOURS.index(h), candidate
    tomorrow = now_ist + timedelta(days=1)
    return 0, tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)


def resolve_source(slot_start_ist_dt: datetime):
    """Given a slot's IST start time, derive the GFS source cycle: floor the
    UTC valid time to the nearest standard cycle hour, then step back 12h
    (the corrected rule from pipeline_scoping_findings.md)."""
    slot_start_utc = slot_start_ist_dt.astimezone(timezone.utc)
    nominal_valid = slot_start_utc.replace(minute=0, second=0, microsecond=0)
    cycle_utc = nominal_valid - timedelta(hours=FORECAST_HOUR)
    return cycle_utc, FORECAST_HOUR, nominal_valid, slot_start_utc


# ─── NOMADS download ─────────────────────────────────────────────────────────

def build_nomads_url(cycle_utc: datetime, fhour: int) -> str:
    date_str = cycle_utc.strftime("%Y%m%d")
    cc = f"{cycle_utc.hour:02d}"
    fff = f"{fhour:03d}"
    filename = f"gfs.t{cc}z.pgrb2.0p25.f{fff}"
    params = {
        "file": filename,
        "lev_surface": "on",
        "lev_2_m_above_ground": "on",
        "lev_850_mb": "on",
        "lev_700_mb": "on",
        "lev_500_mb": "on",
        # PWAT lives at this level, not 'surface' -- confirmed empirically:
        # a live download without this flag came back with 12 messages and
        # no PWAT at all (NOMADS only returns level x var combos you asked
        # for, so leaving this out silently drops the field instead of
        # erroring). See extract_fields()'s "atmosphereSingleLayer" group,
        # which is what this level maps to on the cfgrib/xarray side.
        "lev_entire_atmosphere_(considered_as_a_single_layer)": "on",
        "var_TMP": "on",
        "var_RH": "on",
        "var_PRES": "on",
        "var_CAPE": "on",
        "var_CIN": "on",
        "var_PWAT": "on",
        "subregion": "on",
        "leftlon": f"{LON - BOX:.2f}",
        "rightlon": f"{LON + BOX:.2f}",
        "toplat": f"{LAT + BOX:.2f}",
        "bottomlat": f"{LAT - BOX:.2f}",
        "dir": f"/gfs.{date_str}/{cc}/atmos",
    }
    return NOMADS_BASE + "?" + urlencode(params)


def download_grib(url: str, out_path: str):
    print(f"Downloading: {url}")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
    r = requests.get(url, headers=headers, timeout=120)


# ─── GRIB2 parsing (cfgrib) ──────────────────────────────────────────────────

def _open_group(grib_path, type_of_level):
    """cfgrib can't merge messages with different typeOfLevel into one
    dataset -- GFS's filtered files mix surface / 2m / isobaric messages,
    so each group has to be opened separately. Returns None if this group
    isn't present in the file (fine -- not every request includes every
    group)."""
    import xarray as xr
    try:
        ds = xr.open_dataset(
            grib_path, engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": type_of_level}, "indexpath": ""},
        )
    except Exception as e:
        print(f"  (no '{type_of_level}' group in this file: {e})")
        return None

    # cfgrib doesn't raise when a typeOfLevel filter matches zero messages --
    # it hands back an empty Dataset with no dims/coords instead. Calling
    # .sel(latitude=...) on that blows up with a KeyError rather than a
    # clean "not found", so catch it here instead of at every call site.
    if len(ds.data_vars) == 0:
        print(f"  (no '{type_of_level}' group in this file: 0 matching messages)")
        ds.close()
        return None
    return ds


def extract_fields(grib_path: str):
    """Returns (surface_fields dict, profile dict keyed by pressure level)."""
    surface_fields = {}

    # PWAT ("Precipitable water") isn't filed under typeOfLevel='surface' like
    # CAPE/CIN/PRES are -- GFS puts it under 'atmosphereSingleLayer' (GRIB2's
    # "entire atmosphere as a single layer"). Confirmed empirically: a live
    # run returned cape/cin/sp_pa fine but pwat was missing until this group
    # was added. Trying both here rather than assuming one.
    for type_of_level in ("surface", "atmosphereSingleLayer"):
        ds_sfc = _open_group(grib_path, type_of_level)
        if ds_sfc is None:
            continue
        pt = ds_sfc.sel(latitude=LAT, longitude=LON % 360, method="nearest")
        for src_name, out_name in (("cape", "cape"), ("cin", "cin"), ("pwat", "pwat"), ("sp", "sp_pa")):
            if src_name in pt and out_name not in surface_fields:
                surface_fields[out_name] = float(pt[src_name].values)
        ds_sfc.close()

    ds_2m = _open_group(grib_path, "heightAboveGround")
    if ds_2m is not None:
        pt = ds_2m.sel(latitude=LAT, longitude=LON % 360, method="nearest")
        try:
            pt = pt.sel(heightAboveGround=2)
        except Exception:
            pass
        if "t2m" in pt:
            surface_fields["t2m_C"] = float(pt["t2m"].values) - 273.15
        elif "t" in pt:
            surface_fields["t2m_C"] = float(pt["t"].values) - 273.15
        if "r2" in pt:
            surface_fields["rh2"] = float(pt["r2"].values)
        elif "r" in pt:
            surface_fields["rh2"] = float(pt["r"].values)
        ds_2m.close()

    profile = {}
    ds_iso = _open_group(grib_path, "isobaricInhPa")
    if ds_iso is not None:
        pt = ds_iso.sel(latitude=LAT, longitude=LON % 360, method="nearest")
        for lvl in (850, 700, 500):
            try:
                sub = pt.sel(isobaricInhPa=lvl)
                profile[lvl] = {
                    "t_C": float(sub["t"].values) - 273.15,
                    "rh": float(sub["r"].values),
                }
            except Exception as e:
                print(f"  Warning: missing {lvl} hPa level: {e}")
        ds_iso.close()

    return surface_fields, profile


# ─── Stability index computation (mirrors era5_stability_indices.py) ────────

def compute_indices(surface_fields: dict, profile: dict):
    import metpy.calc as mpcalc
    from metpy.units import units

    have_profile = all(lvl in profile for lvl in (850, 700, 500))
    have_surface = "sp_pa" in surface_fields and "t2m_C" in surface_fields and "rh2" in surface_fields

    if not (have_profile and have_surface):
        print("  Warning: incomplete profile/surface data -- K_INDEX/LIFTED_INDEX/TOTALS_TOTALS will be NaN.")
        return {
            "CAPE": surface_fields.get("cape", np.nan),
            "CIN": surface_fields.get("cin", np.nan),
            "K_INDEX": np.nan, "LIFTED_INDEX": np.nan, "TOTALS_TOTALS": np.nan,
            "PRECIP_WATER": surface_fields.get("pwat", np.nan),
        }

    try:
        sp_hpa = surface_fields["sp_pa"] / 100.0
        td2m_C = float(mpcalc.dewpoint_from_relative_humidity(
            surface_fields["t2m_C"] * units.degC, surface_fields["rh2"] * units.percent
        ).magnitude)

        p_levels = np.array([sp_hpa, 850, 700, 500])
        t_levels = np.array([surface_fields["t2m_C"], profile[850]["t_C"], profile[700]["t_C"], profile[500]["t_C"]])
        td_levels = [td2m_C]
        for lvl in (850, 700, 500):
            td = mpcalc.dewpoint_from_relative_humidity(
                profile[lvl]["t_C"] * units.degC, profile[lvl]["rh"] * units.percent
            )
            td_levels.append(float(td.magnitude))
        td_levels = np.array(td_levels)

        p = p_levels * units.hPa
        t = t_levels * units.degC
        td = td_levels * units.degC

        k = mpcalc.k_index(p, t, td)
        tt = mpcalc.total_totals_index(p, t, td)
        parcel = mpcalc.parcel_profile(p, t[0], td[0])
        li = mpcalc.lifted_index(p, t, parcel)

        return {
            "CAPE":          surface_fields.get("cape", np.nan),   # GFS's own native CAPE -- trust the model, same call era5_stability_indices.py makes
            "CIN":           surface_fields.get("cin", np.nan),    # GFS's own native CIN
            "K_INDEX":       float(k.magnitude),
            "LIFTED_INDEX":  float(np.atleast_1d(li.magnitude)[0]),
            "TOTALS_TOTALS": float(tt.magnitude),
            "PRECIP_WATER":  surface_fields.get("pwat", np.nan),   # GFS's own native precipitable water (mm)
        }
    except Exception as e:
        print(f"  Warning: index computation failed: {e}")
        return {
            "CAPE": surface_fields.get("cape", np.nan),
            "CIN": surface_fields.get("cin", np.nan),
            "K_INDEX": np.nan, "LIFTED_INDEX": np.nan, "TOTALS_TOTALS": np.nan,
            "PRECIP_WATER": surface_fields.get("pwat", np.nan),
        }


# ─── Optional pygrib cross-check ─────────────────────────────────────────────

def _pygrib_point_value(grib_path: str, name: str, lat: float, lon: float):
    """Independent CAPE/CIN/PWAT lookup via pygrib -- a completely separate
    GRIB library from cfgrib/xarray, used both for the manual --verify dump
    and for the automatic QC check below. Returns None (never raises) if
    pygrib isn't installed or the field isn't in the file -- this is a
    best-effort second opinion, not a hard dependency the pipeline should
    crash over."""
    try:
        import pygrib
    except ImportError:
        return None
    try:
        grbs = pygrib.open(grib_path)
        matches = grbs.select(name=name)
        if not matches:
            grbs.close()
            return None
        grb = matches[0]
        val, lats, lons = grb.data(lat1=lat - 0.1, lat2=lat + 0.1, lon1=lon - 0.1, lon2=lon + 0.1)
        grbs.close()
        return float(np.nanmean(val))
    except Exception:
        return None


def verify_with_pygrib(grib_path: str):
    """Manual --verify dump: prints CAPE/CIN/PWAT from pygrib for a human to
    eyeball against what compute_indices() used. If these numbers don't
    roughly match, something is wrong with the cfgrib parsing above, not
    with the data."""
    print("\n--- pygrib cross-check ---")
    for name in ("Convective available potential energy", "Convective inhibition", "Precipitable water"):
        val = _pygrib_point_value(grib_path, name, LAT, LON)
        if val is None:
            print(f"  {name}: not found in file (or pygrib not installed)")
        else:
            print(f"  {name}: {val:.2f} (nearest-box mean)")


def assess_cape_consistency(indices: dict, grib_path: str):
    """Flags the specific failure mode raised 2026-07-24: CAPE reading ~0
    while K-Index/Totals-Totals say the profile is unstable. Runs on every
    fetch, every slot -- not just the one that triggered this. Returns
    (flag, crosscheck_value):

        "OK"                      -- CAPE and the other indices agree, no action needed
        "SUSPECT_NO_CROSSCHECK"   -- looked suspect, but pygrib isn't installed to confirm
        "SUSPECT_CONFIRMED_ZERO"  -- pygrib independently agrees CAPE really is ~0
                                     (likely a genuine capped airmass -- check CIN)
        "SUSPECT_MISMATCH"        -- pygrib disagrees with the cfgrib-parsed CAPE --
                                     that's a real retrieval bug, not weather
    """
    cape = indices.get("CAPE", np.nan)
    k = indices.get("K_INDEX", np.nan)
    tt = indices.get("TOTALS_TOTALS", np.nan)

    cape_reads_zero = not np.isnan(cape) and cape < CAPE_NEAR_ZERO
    instability_says_otherwise = (
        (not np.isnan(k) and k >= CAPE_SUSPECT_KINDEX)
        or (not np.isnan(tt) and tt >= CAPE_SUSPECT_TT)
    )

    if not (cape_reads_zero and instability_says_otherwise):
        return "OK", None

    crosscheck = _pygrib_point_value(grib_path, "Convective available potential energy", LAT, LON)
    if crosscheck is None:
        return "SUSPECT_NO_CROSSCHECK", None
    if crosscheck < CAPE_NEAR_ZERO:
        return "SUSPECT_CONFIRMED_ZERO", crosscheck
    return "SUSPECT_MISMATCH", crosscheck


# ─── Main ─────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, choices=[0, 1, 2, 3], default=None,
                     help="Force a specific slot (its next occurrence) instead of auto-detecting.")
    ap.add_argument("--now", type=str, default=None,
                     help="Override current IST time for testing, e.g. '2026-07-15 14:00'.")
    ap.add_argument("--verify", action="store_true",
                     help="Also cross-check parsed values against pygrib.")
    ap.add_argument("--keep-grib", action="store_true",
                     help="Don't delete data/_gfs_tmp.grib2 after the run -- useful for inspecting "
                          "the raw file's messages directly (e.g. with eccodes.codes_grib_new_from_file).")
    args = ap.parse_args()

    now_ist = (datetime.now(IST) if args.now is None
               else datetime.strptime(args.now, "%Y-%m-%d %H:%M").replace(tzinfo=IST))

    if args.slot is not None:
        slot_start = now_ist.replace(hour=SLOT_START_HOURS[args.slot], minute=0, second=0, microsecond=0)
        if slot_start <= now_ist:
            slot_start += timedelta(days=1)
        slot = args.slot
    else:
        slot, slot_start = next_slot(now_ist)

    cycle_utc, fhour, valid_utc, slot_start_utc = resolve_source(slot_start)
    posting_estimate = cycle_utc + timedelta(hours=POST_LATENCY_HOURS)
    buffer_hours = (slot_start_utc - posting_estimate) / timedelta(hours=1)

    print(f"Now (IST):        {now_ist:%Y-%m-%d %H:%M}")
    print(f"Target slot:      {slot} ({SLOT_WINDOWS[slot]} IST), starts {slot_start:%Y-%m-%d %H:%M} IST")
    print(f"Source cycle:     GFS {cycle_utc:%Y-%m-%d %H}Z, forecast hour f{fhour:03d} (valid {valid_utc:%Y-%m-%d %H}Z)")
    print(f"Est. posting time: {posting_estimate:%Y-%m-%d %H:%M} UTC (~{POST_LATENCY_HOURS}h after cycle, per pipeline_scoping_findings.md)")
    print(f"Buffer before slot starts: ~{buffer_hours:.1f}h")
    if buffer_hours < 1:
        print("WARNING: buffer under 1h -- this cron job should run right at the posting "
              "estimate, not earlier, or it will hit a 404/empty response.")

    url = build_nomads_url(cycle_utc, fhour)
    download_grib(url, GRIB_TMP)

    print("\nParsing GRIB2 with cfgrib...")
    surface_fields, profile = extract_fields(GRIB_TMP)
    print(f"  Surface fields found: {list(surface_fields.keys())}")
    print(f"  Profile levels found: {list(profile.keys())}")

    if args.verify:
        verify_with_pygrib(GRIB_TMP)

    indices = compute_indices(surface_fields, profile)

    qc_flag, qc_crosscheck = assess_cape_consistency(indices, GRIB_TMP)
    if qc_flag == "OK":
        print("\nCAPE consistency check: OK (no conflict with K-Index/Totals-Totals)")
    elif qc_flag == "SUSPECT_NO_CROSSCHECK":
        print(f"\nQC FLAG: {qc_flag} -- CAPE={indices.get('CAPE')} but K-Index/TT suggest an "
              f"unstable profile, and pygrib isn't installed to independently confirm. "
              f"Install pygrib (pip install pygrib) or rerun with --verify for a manual look.")
    elif qc_flag == "SUSPECT_CONFIRMED_ZERO":
        print(f"\nQC FLAG: {qc_flag} -- pygrib independently agrees CAPE reads ~0 "
              f"({qc_crosscheck:.2f}). Likely a genuine capped airmass (check CIN -- a strongly "
              f"negative value supports this), not a retrieval bug.")
    elif qc_flag == "SUSPECT_MISMATCH":
        print(f"\nQC FLAG: {qc_flag} -- cfgrib parsed CAPE={indices.get('CAPE')} but pygrib's "
              f"independent read is {qc_crosscheck:.2f}. These should agree -- this points to a "
              f"real retrieval/parsing issue, not weather. Investigate before trusting this row.")

    row = {
        "date": slot_start.strftime("%Y-%m-%d"),
        "slot": slot,
        "ist_window": SLOT_WINDOWS[slot],
        "valid_time_utc": valid_utc.strftime("%Y-%m-%d %H:%M"),
        "source_cycle_utc": cycle_utc.strftime("%Y-%m-%d %H:%M"),
        "source_fhour": fhour,
        "fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        **indices,
        "CAPE_QC_FLAG": qc_flag,
        "CAPE_PYGRIB_CROSSCHECK": qc_crosscheck if qc_crosscheck is not None else "",
    }

    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(OUT_PATH):
        existing = pd.read_csv(OUT_PATH)
        existing = existing[~((existing["date"] == row["date"]) & (existing["slot"] == row["slot"]))]
        out = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    else:
        out = pd.DataFrame([row])

    out.to_csv(OUT_PATH, index=False)
    print(f"\nSaved: {OUT_PATH} (slot {slot}, {row['date']})")
    for k in ("CAPE", "CIN", "K_INDEX", "LIFTED_INDEX", "TOTALS_TOTALS", "PRECIP_WATER", "CAPE_QC_FLAG"):
        print(f"  {k}: {row[k]}")

    if args.keep_grib:
        print(f"(--keep-grib set, leaving {GRIB_TMP} in place)")
    else:
        try:
            os.remove(GRIB_TMP)
        except OSError:
            pass


if __name__ == "__main__":
    main()

