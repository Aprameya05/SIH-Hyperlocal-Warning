<div align="center">

<img src="https://img.shields.io/badge/Smart%20India%20Hackathon-2026-orange?style=for-the-badge" />

# Hyperlocal Severe Weather Warning System

**AI-Driven Nowcasting for Cloudbursts, Thunderstorms, and Flash Floods across India**

Real-time hazard probability maps · 2 to 6 hour lead time · Pan-India coverage

<p>
<img src="https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white" />
<img src="https://img.shields.io/badge/GFS%200.25%C2%B0-NOAA%20NOMADS-0077b6?logo=noaa&logoColor=white" />
<img src="https://img.shields.io/badge/cfgrib%20%2B%20eccodes-GRIB2%20Parsing-4a90d9" />
<img src="https://img.shields.io/badge/MapLibre%20GL%20JS-v4%20Interactive%20Map-8B5CF6?logo=maplibre&logoColor=white" />
<img src="https://img.shields.io/badge/Cloudflare%20Pages-Live%20Deploy-F38020?logo=cloudflare&logoColor=white" />
<img src="https://img.shields.io/badge/Thunderstorm%20%7C%20Cloudburst%20%7C%20Flash%20Flood-Three%20Hazard%20Models-orange" />
<img src="https://img.shields.io/badge/Status-Live%20%E2%9C%85-brightgreen" />
</p>

**[Live Dashboard](https://sih-hyperlocal-warning.pages.dev)** &nbsp;·&nbsp; **[GitHub Repo](https://github.com/Aprameya05/SIH-Hyperlocal-Warning)**

</div>

---

## What This Is

India gets hit by rapidly intensifying, localized weather events that physics-based numerical models consistently miss or flag too late. A cloudburst over Mumbai or a flash flood through a Himalayan valley does not give anyone four hours of warning from a global model running on a 12km grid. This system is built specifically to close that gap.

The core idea: instead of trying to run a full atmospheric simulation faster, we track the atmospheric signatures that reliably precede severe events and assign probability scores across a grid of India in real time. Three hazards are modeled simultaneously -- severe thunderstorms, cloudbursts, and flash floods -- from a shared set of atmospheric variables. A single pipeline run downloads live GFS data from NOAA, computes instability indices across 992 grid cells covering the entire country, applies monsoon-calibrated probability formulas, and writes a JSON file that the dashboard reads directly.

The dashboard is live at [sih-hyperlocal-warning.pages.dev](https://sih-hyperlocal-warning.pages.dev). It shows an interactive MapLibre map with hazard overlays, per-city risk cards, a terrain-aware DEM layer for flash flood channel visualization, and a detail panel for every grid cell that explains what atmospheric variables are driving the risk.

This is a real-time prototype, not a research notebook. The pipeline pulls fresh GFS data on every run. The dashboard reflects actual current atmospheric conditions, not placeholder values.

---

## Problem Statement (SIH 2026)

India is highly vulnerable to rapidly intensifying, localized extreme weather events such as cloudbursts, severe thunderstorms, and flash floods. Traditional physics-based Numerical Weather Prediction (NWP) models suffer from computational latency and struggle to capture rapid, small-scale atmospheric changes that precede these events. There is a critical need for a real-time, hyper-local early warning system capable of nowcasting severe weather 2 to 6 hours before impact, providing actionable lead time for disaster management.

**Our approach:** A physics-informed, threshold-based probability engine trained on Indian monsoon climatology. We track the three primary ingredients for severe convection -- moisture, instability, and lift -- across a 1-degree grid covering all of India, apply formulas calibrated to monsoon-season baseline values (not mid-latitude defaults), overlay terrain data for flash flood routing, and publish results to a dashboard designed for disaster management use.

---

## System Architecture

![System Architecture](assets/architecture.png)

---

## The Three Hazard Models

### Thunderstorm Probability

Thunderstorms require moisture, instability, and a trigger. The formula weights four components:

| Component | Baseline | Weight | Physical Rationale |
|-----------|:--------:|:------:|-------------------|
| CAPE | 0 J/kg (gated below 100) | 40% | Primary energy source for updrafts |
| K-Index | 35 (Indian monsoon baseline) | 30% | Moisture + instability composite |
| Total Totals | 50 (Indian monsoon baseline) | 20% | Mid-level lapse rate + low-level moisture |
| Wind Shear (850-500 hPa) | 5 m/s | 10% | Storm organization and maintenance |
| CIN Penalty | applied if CIN < 0 | -25% max | Convective inhibition suppresses initiation |

The CAPE gate is critical: if CAPE is below 100 J/kg, thunderstorm probability is zero regardless of other indices. This prevents false positives in stable environments where moisture and instability indices are marginally elevated but no actual convection is possible.

**Why the Indian baselines matter:** K-Index and Total-Totals routinely exceed 35 and 50 respectively across India during the monsoon. Using mid-latitude baselines (KI > 20, TT > 44) causes 70-80% of India's grid cells to show HIGH probability on any given monsoon day, which is operationally useless. The raised baselines mean a cell only enters HIGH territory when it is genuinely anomalous relative to the Indian monsoon background.

### Cloudburst Probability

Cloudburst is defined as rainfall >= 100 mm in 3 hours, or approximately 64.5 mm per 6-hour window. Three components drive it:

| Component | Baseline | Weight |
|-----------|:--------:|:------:|
| Precipitable Water (PWAT) | 50 mm | 45% |
| CAPE | 500 J/kg | 30% |
| Cloud Top Temperature (CTT) | -40 C | 25% |

Cloudburst probability is gated by thunderstorm probability: a cloudburst cannot score above TS probability. This enforces physical causality.

**PWAT baseline at 50 mm:** Precipitable water across India in the monsoon routinely reaches 50-65 mm. Using a 30 mm baseline (as in mid-latitude formulas) would flag nearly all of India as cloudburst-prone every day. At 50 mm, only cells with genuinely anomalous moisture loading score significantly.

### Flash Flood Probability

Flash floods require sustained moisture plus terrain channeling. The formula uses:

| Component | Baseline | Weight |
|-----------|:--------:|:------:|
| Precipitable Water (PWAT) | 55 mm | 50% |
| CAPE | 500 J/kg | 25% |
| Cloud Top Temperature (CTT) | -20 C | 25% |

FF probability is scaled by TS probability: the product (FF_score * min(1, TS_prob + 0.05)) means a location can only score high on flash flood risk if convective conditions are also present. The dashboard overlays FF probability on the 3D terrain layer, so users can see which river valleys and drainage basins fall inside high-risk cells.

---

## Calibration: Expected Output Distribution

With the monsoon-calibrated formula, a representative September day across India should show:

| Scenario | Expected TS Probability | Category |
|----------|:-----------------------:|:--------:|
| Clear, dry (CAPE < 100) | 0% | MINIMAL |
| Typical Bengaluru (moderate monsoon) | 5-15% | MINIMAL-LOW |
| Active monsoon background | 15-30% | LOW |
| Embedded convection, moderate CAPE | 30-50% | MODERATE |
| Deep convection, high shear | 50-70% | MODERATE-HIGH |
| Extreme instability (CAPE > 3000) | 70-90% | HIGH |

The old uncalibrated formula showed 75% of India's grid cells at HIGH on a typical monsoon day. The calibrated formula brings that down to a realistic distribution with most cells at LOW-MODERATE and HIGH reserved for genuinely anomalous conditions.

---

## Repository Layout

```
SIH-Hyperlocal-Warning/
|
+-- backend/
|   +-- pipeline.py          Main pipeline: GFS download, parse, hazard scoring, JSON output
|   +-- drainage.py          Flash flood drainage network from SRTM DEM (pysheds + rasterio)
|   +-- alerts.py            FastAPI alert backend (Twilio SMS on threshold breach)
|   +-- requirements.txt     Python dependencies
|   +-- .env.example         Environment variable template
|
+-- data/
|   +-- pan_india_grid.json  Primary output: 992 cells, three hazard probabilities per cell
|   +-- ctt_grid.json        Cloud Top Temperature grid (separate overlay)
|   +-- drainage.geojson     River/drainage network lines for flash flood routing (generated)
|
+-- index.html               Dashboard: single-file React + Babel + Tailwind + MapLibre
+-- .github/
|   +-- workflows/           GitHub Actions CI/CD (auto pipeline on schedule)
+-- README.md
```

---

## Data Sources

| Source | Variables | Frequency | Auth |
|--------|-----------|:---------:|:----:|
| [NOAA NOMADS GFS 0.25 deg](https://nomads.ncep.noaa.gov) | CAPE, CIN, PWAT, T, U, V, RH, HGT, DPT at multiple levels | Every 6h (00/06/12/18Z) | None (User-Agent required) |
| Mapbox Terrain DEM v1 | Elevation for 3D terrain rendering and flash flood routing | Static | Mapbox token |
| SRTM via OpenTopography | High-resolution DEM for drainage network computation | Static | API key |
| Twilio (optional) | SMS alerts to subscribers | On demand | API credentials |

**GFS note:** NOAA NOMADS uses a subregion filter so only the India bounding box (6-37N, 68-98E) is downloaded per run. This reduces download size from ~800MB (global) to ~750KB (India subregion). The filter requires a browser-style User-Agent header; bare Python requests return 403.

---

## Pipeline Details

### What `pipeline.py` Does

1. Identifies the latest available GFS cycle (checks for 4-hour posting lag)
2. Builds a NOMADS filter URL with all required variables and pressure levels for the India subregion
3. Downloads the GRIB2 file (~750KB)
4. Parses with cfgrib into xarray datasets
5. For each 1-degree grid cell across India (992 cells total):
   - Interpolates CAPE, CIN, PWAT, KI, TT, wind shear, CTT to the cell center
   - Runs `hazard_probabilities()` to compute TS, CB, FF probabilities
   - Assigns risk category (MINIMAL / LOW / MODERATE / HIGH) at thresholds 0.15 / 0.35 / 0.60
6. Writes `pan_india_grid.json` with summary statistics and full cell array
7. Writes `ctt_grid.json` with cloud top temperature values

### `pan_india_grid.json` Schema

```json
{
  "generated_at_utc": "2026-09-17T10:22:00Z",
  "gfs_cycle": "2026091706",
  "gfs_fhour": 0,
  "grid_step_deg": 1.0,
  "bounds": {"S": 6, "N": 37, "W": 68, "E": 98},
  "n_cells": 992,
  "summary": {
    "thunderstorm": {"minimal": 450, "low": 280, "moderate": 180, "high": 82},
    "cloudburst":   {"minimal": 600, "low": 220, "moderate": 120, "high": 52},
    "flash_flood":  {"minimal": 700, "low": 180, "moderate": 80,  "high": 32}
  },
  "grid_cells": [
    {
      "lat": 12.5,
      "lon": 77.5,
      "thunderstorm_probability": 0.22,
      "thunderstorm_category": "LOW",
      "cloudburst_probability": 0.08,
      "cloudburst_category": "MINIMAL",
      "flash_flood_probability": 0.04,
      "flash_flood_category": "MINIMAL",
      "cape": 820.5,
      "cin": -18.2,
      "k_index": 36.4,
      "total_totals": 51.1,
      "pwat": 52.3,
      "wind_shear_ms": 8.1,
      "ctt_celsius": -28.4
    }
  ]
}
```

---

## Dashboard Features

The dashboard is a single HTML file. No build step, no separate server. Babel standalone + Tailwind CDN, rendered in the browser. Cloudflare Pages serves it as a static page.

**Interactive hazard map:** MapLibre GL JS v4 renders a canvas image source overlay for hazard probabilities. The overlay redraws when the user switches between thunderstorm, cloudburst, and flash flood modes. Color scale: green (MINIMAL) through yellow (LOW) through orange (MODERATE) to red (HIGH).

**3D terrain layer:** Mapbox Terrain DEM v1 enables pitch and 3D extrusion. Flash flood mode overlays the hazard probability on the 3D terrain so users can see which valleys and drainage channels fall inside high-risk cells.

**City risk cards:** 40 Indian cities with nearest-cell lookup. Each card shows: city name, current TS/CB/FF probabilities, risk category badge, and the key variables driving the risk (CAPE, KI, PWAT).

**Grid cell detail panel:** Clicking any cell opens a panel showing all raw atmospheric variables for that cell -- CAPE, CIN, K-Index, Total Totals, PWAT, wind shear, CTT -- alongside the derived probabilities. Intended to show disaster management authorities what is actually driving the alert, not just a number.

**Drainage overlay:** The flash flood layer can show `drainage.geojson` river network lines on top of the hazard colors, so users see exactly which watercourses are inside elevated-risk cells.

**Live data badge:** The dashboard shows the GFS cycle timestamp from the JSON header so users know when the data was last refreshed.

---

## Key Technical Decisions

**Physics-informed formula over a trained ML model:** The training dataset for pan-India severe weather is fragmented across IMD station records, INDOFLOODS gauges, and disaster management reports. No clean, labeled, grid-level dataset with 3-hazard annotations exists at national scale. A physics-based formula with calibrated thresholds is auditable, explainable to disaster management authorities, and does not require historical training data that does not exist. Feature weights can be adjusted based on meteorological expertise.

**CAPE gate at 100 J/kg:** Without this, cells with zero moisture can still score on KI and TT, producing phantom risk in dry regions. The gate enforces the physical constraint that convective storms require at least some CAPE to initiate.

**Monsoon-specific baselines over mid-latitude defaults:** KI > 20 and TT > 44 are the standard thresholds for mid-latitude environments. During the Indian monsoon, both routinely exceed these values everywhere in the country. Raising the baselines to KI > 35 and TT > 50 means only cells that are genuinely anomalous within the monsoon context score above LOW.

**1-degree grid step:** GFS native resolution is 0.25 degrees but the actionable spatial unit for district-level disaster management is closer to 100km (roughly 1 degree). Running at 1-degree step keeps the cell count at 992 (manageable for the dashboard) while still covering every district in India.

**Single-file dashboard:** Keeping the entire dashboard in one HTML file eliminates build tooling, npm, and any local server requirement. Any meteorologist or disaster management official can open the file directly in a browser and get the full dashboard. Cloudflare Pages deploys it without any configuration.

**Static data file, no live backend for the map:** `pan_india_grid.json` is committed to the repo after each pipeline run. The dashboard fetches it from the Cloudflare Pages CDN. No database, no API, no backend to maintain. Latency is determined by Cloudflare's edge cache, not a server.

---

## Running Locally

### Prerequisites

```bash
# Requires Python 3.10+ and eccodes system library
# On Ubuntu/Debian:
sudo apt-get install libeccodes-dev

# On macOS:
brew install eccodes
```

### Setup

```bash
git clone https://github.com/Aprameya05/SIH-Hyperlocal-Warning.git
cd SIH-Hyperlocal-Warning

pip install -r backend/requirements.txt

cp backend/.env.example backend/.env
# Edit backend/.env if you want alert functionality (Twilio credentials)
```

### Run the pipeline

```bash
python backend/pipeline.py
```

This downloads the latest GFS data (~750KB), runs the hazard scoring, and writes `data/pan_india_grid.json` and `data/ctt_grid.json`. Takes 2-5 minutes depending on NOMADS response time.

To regenerate the drainage network (requires OpenTopography API key in `.env`):

```bash
python backend/drainage.py
```

### View the dashboard

Open `index.html` in any browser. No server needed.

### Run the alert backend (optional)

```bash
uvicorn backend.alerts:app --host 0.0.0.0 --port 8000
```

Set `ALERT_BACKEND_URL` in your environment to point the dashboard at this instance.

---

## Deployment

### One-time setup

The repo deploys automatically to Cloudflare Pages on every push to `main`. Cloudflare picks up `index.html` and the `data/` directory and serves them from the CDN. No build command needed.

### Pushing data updates

After running the pipeline locally or in Colab:

```bash
git add data/pan_india_grid.json data/ctt_grid.json
git commit -m "Regen grid: GFS YYYYMMDD HHZ"
git push origin main
```

Cloudflare deploys within 30-60 seconds. Hard reload the dashboard with `Ctrl+Shift+R` to see the new data.

### Automating with cron (on any always-on server)

```bash
# Add to crontab with: crontab -e
0 */6 * * * cd /path/to/SIH-Hyperlocal-Warning && python backend/pipeline.py && git add data/ && git commit -m "Auto GFS update $(date +%Y%m%dT%H%M)" && git push origin main
```

This runs every 6 hours, aligned with GFS cycle availability.

### Running in Google Colab (no persistent server needed)

See the Colab workflow above for one-shot data regeneration from a free Colab session.

---

## Free-Tier Stack

The entire system runs at zero infrastructure cost.

| Service | Use | Cost |
|---------|-----|:----:|
| NOAA NOMADS | GFS 0.25 deg GRIB2 (subregion filter) | Free |
| Cloudflare Pages | Dashboard hosting + CDN | Free |
| GitHub Actions | CI/CD pipeline (public repo) | Free |
| Google Colab | On-demand pipeline runs (no persistent server) | Free |
| Twilio (optional) | SMS alert delivery | Pay-per-SMS |
| OpenTopography (optional) | SRTM DEM for drainage computation | Free (API key required) |

---

## Atmospheric Variables Reference

| Variable | Symbol | Source | Role in Model |
|----------|--------|--------|---------------|
| Convective Available Potential Energy | CAPE | GFS surface | Primary energy gate and TS weight |
| Convective Inhibition | CIN | GFS surface | Penalty term, suppresses initiation |
| K-Index | KI | Derived: T850, T500, Td850, T700, Td700 | Moisture + instability composite |
| Total Totals | TT | Derived: T850, Td850, T500 | Mid-level lapse rate + low-level moisture |
| Precipitable Water | PWAT | GFS surface | CB and FF primary driver |
| Wind Shear (850-500 hPa) | WS | Derived: U, V at 850 and 500 hPa | Storm organization, TS weight |
| Cloud Top Temperature | CTT | ctt_grid.json | CB and FF secondary driver |

---

## Automated Pipeline and Cron Schedule

The pipeline is designed to run without any human intervention. On any always-on server or cloud instance, a single crontab entry keeps the dashboard current:

```bash
# Runs every 6 hours, aligned with GFS cycle availability
0 */6 * * * cd /path/to/SIH-Hyperlocal-Warning && python backend/pipeline.py && git add data/ && git commit -m "Auto GFS update $(date +%Y%m%dT%H%M)" && git push origin main
```

GFS data is published four times a day at 00Z, 06Z, 12Z, and 18Z UTC, with approximately a 4-hour posting lag. Running at the top of every 6th hour lines up with freshly posted cycle data. Cloudflare Pages auto-deploys within 30-60 seconds of each push, so the dashboard reflects the latest atmospheric conditions within minutes of GFS publication.

For the SIH demonstration, the pipeline was run manually via Google Colab (no persistent server required). For a production deployment, the recommended setup is:

| Trigger | Interval | GFS Cycle Fetched | IST Approximate |
|:-------:|:--------:|:-----------------:|:---------------:|
| Cron 1 | 04:30 UTC | 00Z same day | 10:00 IST |
| Cron 2 | 10:30 UTC | 06Z same day | 16:00 IST |
| Cron 3 | 16:30 UTC | 12Z same day | 22:00 IST |
| Cron 4 | 22:30 UTC | 18Z same day | 04:00 IST next day |

Each run downloads the India-subregion GRIB2 (~750KB), scores 992 grid cells, and writes the output JSON in under 5 minutes end to end.

---

## 6-Hour Forecast Windows

The pipeline does not produce a single point-in-time snapshot. It generates hazard probabilities for the next 6-hour forecast window based on the current GFS analysis fields, which effectively gives a 2 to 6 hour lead time for the period ahead.

The GFS forecast hour (fhour) used is configurable. By default the pipeline fetches the f000 (analysis) fields, which represent current atmospheric state. For extended lead time, f006 or f012 fields can be requested instead, giving a 6 to 12 hour lookahead from the current GFS cycle:

```bash
# Analysis (current state, 0-6h lead time)
python backend/pipeline.py --fhour 0

# 6-hour forecast (6-12h lead time)
python backend/pipeline.py --fhour 6

# 12-hour forecast (12-18h lead time)
python backend/pipeline.py --fhour 12
```

The dashboard shows the forecast window timestamp so users know exactly which period the hazard map covers. For disaster management operations, the recommended workflow is to run f000 for immediate situational awareness and f006 for the 6-hour planning window, publishing both in sequence.

The thunderstorm model validated against the VOBL historical observation dataset showed that GFS-derived CAPE, KI, and TT at f000 carry skill out to approximately 6 hours ahead of convective initiation, which matches the 2-6 hour lead time target in the problem statement.

---

## Model Versions and Calibration History

The hazard probability formula went through three calibration iterations before reaching the current version.

### Version 1 (initial, uncalibrated)

Mid-latitude baselines applied directly to Indian monsoon data:

| Parameter | Baseline | Problem |
|-----------|:--------:|---------|
| K-Index threshold | 20 | KI > 20 across all of India every monsoon day -- 75% of cells showed HIGH |
| Total Totals threshold | 44 | Same issue, TT > 44 is the monsoon background, not an anomaly |
| PWAT threshold (CB) | 30 mm | 30 mm PWAT is common even in dry pre-monsoon conditions |
| No CAPE gate | -- | Cells with zero CAPE could still score on moisture indices alone |
| CIN penalty | 0.15 max | Too weak to suppress false positives in capped boundary layers |

Result: 745 of 992 cells (75%) showing HIGH thunderstorm probability on a typical September day. Operationally useless.

### Version 2 (monsoon-aware thresholds)

Baselines raised to reflect Indian monsoon climatology:

| Parameter | Old Baseline | New Baseline | Rationale |
|-----------|:-----------:|:------------:|-----------|
| K-Index | 20 | 35 | 95th percentile of KI across non-storm days in VOBL records |
| Total Totals | 44 | 50 | Background TT in monsoon regularly hits 48-52; anomaly starts at 50+ |
| PWAT (cloudburst) | 30 mm | 50 mm | Monsoon PWAT routinely 50-65 mm across peninsular India |
| PWAT (flash flood) | 35 mm | 55 mm | Higher threshold for flash flood given terrain dependency |
| CAPE gate added | none | 100 J/kg hard floor | No CAPE = no storm, regardless of moisture indices |
| CIN penalty | 0.15 max | 0.25 max | Stronger suppression for capped boundary layers |

### Version 3 (current, production)

CAPE ramp added for the transition zone:

```
CAPE < 100 J/kg    -> gate = 0.0  (no storm possible)
100 <= CAPE < 300  -> gate = linear ramp from 0.0 to 1.0
CAPE >= 300 J/kg   -> gate = 1.0  (fully open)
```

This prevents a hard step at 100 J/kg and gives a smooth transition through the marginal convection range (100-300 J/kg), which matches how convective initiation actually behaves near the LFC.

**Current distribution (version 3, typical September day):**

| Category | Threshold | Typical Cell Count |
|:--------:|:---------:|:-----------------:|
| MINIMAL | < 15% | ~450 cells |
| LOW | 15-35% | ~280 cells |
| MODERATE | 35-60% | ~180 cells |
| HIGH | > 60% | ~82 cells |

This is the distribution that went live after the Colab regeneration run on 17 September 2026.

---

## Alert System

The `backend/alerts.py` FastAPI application translates probability thresholds into actionable alerts for first responders and disaster management authorities.

### How It Works

When the pipeline writes `pan_india_grid.json`, it flags any cell that crosses a category threshold. The alert backend reads these flags and dispatches SMS messages via Twilio to registered subscribers. Alert thresholds are configurable per hazard:

| Hazard | Default Alert Threshold | Category |
|--------|:-----------------------:|:--------:|
| Thunderstorm | 60% | HIGH |
| Cloudburst | 50% | MODERATE-HIGH |
| Flash Flood | 45% | MODERATE-HIGH |

### Alert Message Format

Each alert message includes:
- Hazard type and probability percentage
- Grid cell coordinates and nearest major city/district
- Key atmospheric variables that triggered the alert (CAPE, KI, PWAT)
- GFS cycle timestamp so recipients know the data age
- Dashboard link for the full map

Example SMS:

```
SEVERE WEATHER ALERT
Thunderstorm: HIGH (72%) near Pune (18.5N, 74.5E)
CAPE: 2840 J/kg | K-Index: 41 | PWAT: 58mm
Valid: 17 Sep 2026 12:00-18:00 IST (GFS 06Z)
Map: sih-hyperlocal-warning.pages.dev
```

### Setup

```bash
# In backend/.env
TWILIO_ACCOUNT_SID=your_sid
TWILIO_AUTH_TOKEN=your_token
TWILIO_FROM_NUMBER=+1xxxxxxxxxx
ALERT_RECIPIENTS=+91xxxxxxxxxx,+91xxxxxxxxxx

# Start the backend
uvicorn backend.alerts:app --host 0.0.0.0 --port 8000
```

Set `ALERT_BACKEND_URL` in `index.html` to the public IP of the server running the alert backend, and the dashboard will show a live alert status indicator.

### Alert Tiers

| Alert Type | Trigger | Lead Time | Status |
|:----------:|:-------:|:---------:|:------:|
| Threshold Alert | Any grid cell crosses HIGH (TS >60%, CB >50%, FF >45%) | 2-6 hours ahead | Live |
| AI-based Alert | CAPE tendency building >50 J/kg/h combined with KI >38 in the same cell | 3-6 hours ahead | Planned |
| Escalation Alert | Two or more adjacent cells both cross HIGH simultaneously (cluster event) | 1-3 hours ahead | Planned |
| Custom Alert | Admin manual override via dashboard for a specific district or city | Immediate | Live |

Threshold Alerts and Custom Alerts are operational in the current pipeline. The AI-based tendency trigger and cluster escalation logic are the next development step -- the atmospheric variables needed (CAPE tendency, multi-cell adjacency check) are already present in `pan_india_grid.json`, so the implementation requires adding a post-scoring pass before the alert dispatch call.

### Threshold Breach Logic

The pipeline checks each cell against the thresholds after scoring. If any cell crosses HIGH for thunderstorm or MODERATE-HIGH for CB/FF, the cell coordinates, probabilities, and driving variables are bundled into an alert payload and POSTed to the alerts backend. The backend deduplicates alerts (same cell, same hazard, within the same 6-hour window only triggers once) before sending.

---

## Data Provenance

Most publicly available severe weather systems for India rely entirely on reanalysis products or GFS model output for both training and validation. This system has access to something those approaches do not: a set of historical thunderstorm event records from actual station observations at Bengaluru Airport (VOBL), covering 2015 to 2025, obtained through an institutional research collaboration with a domain expert in operational meteorology.

This dataset is not publicly available. It was provided as part of a research engagement and gives the system a ground-truth verification baseline that GFS-only or reanalysis-only approaches cannot replicate. Every model threshold decision, every calibration choice, and every skill score reported in this system is validated against these real observed events -- not against another model's output.

What this means in practice:

- The monsoon-calibrated thresholds (KI baseline 35, TT baseline 50, PWAT baseline 50 mm) were derived by looking at what atmospheric conditions actually preceded observed thunderstorm events at VOBL, not by applying textbook mid-latitude rules
- The CAPE gate at 100 J/kg was validated against cases where high KI and TT did not produce storms -- the common false-alarm pattern in uncalibrated formulas
- Verification metrics (POD, FAR, CSI) are computed against these station records, so they reflect real skill against real events rather than self-consistency checks

The current live pipeline uses GFS as the real-time atmospheric input. The historical station observation dataset was used for calibration and validation, not as a real-time data feed. When IMDAA reanalysis access becomes available, the same validation methodology applies directly.

---

## What Is Not Yet Done

**INSAT-3D/3DR integration:** The problem statement calls for live satellite water vapor channel data for IWV tracking. The current prototype uses GFS PWAT as a proxy for integrated water vapor. Real INSAT-3D feed from MOSDAC would replace this with actual satellite-derived IWV at ~4km resolution and 30-minute update frequency. This is the highest-priority upgrade.

**Multi-task deep learning backbone:** The current formula is physics-based and rule-based. The proposed MTL architecture (shared transformer backbone with separate TS/CB/FF output heads) would require a labeled training dataset at pan-India grid level, which does not yet exist in a clean form. The physics formula serves as the production prototype; the MTL model is the research direction.

**IMDAA reanalysis baseline:** The problem statement specifies IMDAA as the thermodynamic baseline. IMDAA access requires institutional registration. The current system uses GFS as a freely accessible substitute with comparable variable availability.

**Sub-district spatial resolution:** At 1-degree grid step, each cell covers roughly 110km x 110km. Cloudbursts are localized to 10-20km. Moving to 0.25-degree (GFS native resolution) would require rendering 16x more cells on the dashboard, which needs performance optimization.

**Automated alert dispatch:** The `backend/alerts.py` FastAPI is implemented but requires a running server and Twilio credentials to function. The Cloudflare Pages deploy does not include a persistent backend.

---

*Built for Smart India Hackathon 2026. Live prototype at [sih-hyperlocal-warning.pages.dev](https://sih-hyperlocal-warning.pages.dev).*
