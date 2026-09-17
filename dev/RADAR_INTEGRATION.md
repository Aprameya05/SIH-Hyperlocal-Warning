# Radar / Storm Proximity Integration

## CSIR Thunderstorm Nowcasting — VOBL Airport

---

## Files to copy into `C:\Users\Atul\Desktop\csir-repo\`

```
fetch_himawari_realtime.py   → csir-repo/
fetch_imerg_realtime.py      → csir-repo/
backtest_himawari.py         → csir-repo/
radar_router.py              → csir-repo/
run_radar_scheduler.py       → csir-repo/
pages/radar_panel.py         → csir-repo/pages/
```

---

## Step 1 — Install dependencies

```bash
pip install boto3 requests numpy Pillow matplotlib h5py scikit-learn pandas
```

---

## Step 2 — Test the fetcher (network diagnostic first)

```bash
python fetch_himawari_realtime.py --diag
```

This prints which of S3 / JAXA / NICT is reachable from your network.
Then run:

```bash
python fetch_himawari_realtime.py
```

Expected output in `data/himawari_realtime/`:

- `himawari_latest.json`
- `himawari_vobl_YYYYMMDD_HHmm.json`
- `himawari_vobl_YYYYMMDD_HHmm.png`
- `himawari_vobl_YYYYMMDD_HHmm_bt.npy`
- `himawari_vobl_log.jsonl`

---

## Step 3 — IMERG setup (optional, needs free NASA account)

```bash
python fetch_imerg_realtime.py --setup-auth
```

Follow the printed instructions (2 min, free Earthdata account).
Then:

```bash
python fetch_imerg_realtime.py
```

---

## Step 4 — Historical backtest

```bash
# Quick test: monsoon 2022 only
python backtest_himawari.py --start 2022-06-01 --end 2022-09-30 --months 6,7,8,9

# Full dataset (takes ~20 min)
python backtest_himawari.py
```

Outputs in `data/backtest/`:

- `himawari_backtest_results.csv` — per-date BT + label
- `himawari_backtest_metrics.json` — AUROC, avg precision
- `himawari_backtest_plot.png` — ROC + distribution plot

If `bt_min_C AUROC > 0.65` → proxy has skill, safe to use operationally.

---

## Step 5 — Wire into FastAPI (`main.py`)

Add these two lines to your existing `main.py`:

```python
from radar_router import router as radar_router
app.include_router(radar_router, prefix="/radar", tags=["Radar / Proximity"])
```

New endpoints:

- `GET /radar/proximity` — merged Himawari + IMERG signal (JSON)
- `GET /radar/history?n=6` — last N frames
- `GET /radar/image/latest` — latest PNG
- `GET /radar/status` — freshness check

---

## Step 6 — Streamlit dashboard

`pages/radar_panel.py` is auto-discovered by Streamlit's multi-page app.
It appears as "Storm Proximity Radar" in the sidebar.

Run your existing dashboard normally:

```bash
streamlit run main_dashboard.py
```

---

## Step 7 — Keep it running (10-min scheduler)

**Option A — Python scheduler (simplest):**

```bash
python run_radar_scheduler.py
```

**Option B — Windows Task Scheduler:**

```
schtasks /create /tn "HimawariFetch" /tr "python C:\Users\Atul\Desktop\csir-repo\fetch_himawari_realtime.py" /sc minute /mo 10 /st 00:00
```

**Option C — Linux cron (if deployed on server):**

```
*/10 * * * * cd /path/to/csir-repo && python fetch_himawari_realtime.py >> logs/himawari.log 2>&1
*/30 * * * * cd /path/to/csir-repo && python fetch_imerg_realtime.py   >> logs/imerg.log 2>&1
```

---

## Alert levels

| Level     | Condition                                | Action            |
| --------- | ---------------------------------------- | ----------------- |
| 🔴 RED    | Deep cell (< –40°C) within 50 km of VOBL | Immediate alert   |
| 🟠 ORANGE | Deep cell detected, outside 50 km        | Monitor closely   |
| 🟡 YELLOW | Moderate cloud (< –20°C) in vicinity     | Awareness         |
| 🟢 GREEN  | No significant convective signature      | Normal operations |

---

## Limitations

1. **Not radar reflectivity** — this is IR brightness temperature (cloud-top proxy).
   Anvil cirrus from distant storms can trigger ORANGE alerts.
2. **IMERG lag** — GPM IMERG Early is ~4h behind real-time.
   Use as corroboration, not the primary signal.
3. **VOBL DWR** — once IMD commissions the Bengaluru Doppler radar,
   replace the Himawari source with the MOSDAC feed via the same JSON schema.
   The FastAPI endpoint and Streamlit panel need zero changes.
4. **Network dependency** — the fetcher cascades S3 → JAXA → NICT automatically.
   If all three fail, check `python fetch_himawari_realtime.py --diag`.

---

## Contact

IMD feed enquiry → Dr. Geeta Agnihotri (Scientist F, IMD Bengaluru)
Subject: _Query: VOBL radar status & possible data access for nowcasting system_
