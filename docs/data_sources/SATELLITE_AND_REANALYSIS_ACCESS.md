# Satellite and reanalysis access findings

| Source | Finding | Decision |
|---|---|---|
| INSAT-3D/3DR (MOSDAC) | Requires registered account; no anonymous bulk access | Adapter reports `unavailable`; `MOSDAC_USER/PASS` placeholders in `.env.example` |
| IMDAA | Requires registration | Not used |
| GMGSI (NOAA global IR mosaic) | Reachable, but 8-bit scaled counts without a verified calibration table | Withdrawn (`backend/data_sources/gmgsi.py` reports why) rather than guessing brightness temperatures |
| Himawari-8/9 | Calibrated path blocked/unimplemented in this environment | Unavailable |
| NOAA CDR CMORPH | Open, hourly 0.25deg | Used for rainfall **labels** only |
| IEM METAR | Open | Used for thunderstorm **labels** only |

To enable satellite predictors: obtain MOSDAC credentials, implement a calibrated brightness-temperature reader with a stored availability timestamp, build a training archive, and pass the gate in `HAZARD_MODEL_VALIDATION.md`.
