# CSIR Thunderstorm API — curl Examples

## Health Check
```bash
curl http://localhost:8000/
```

## Slot Info
```bash
curl http://localhost:8000/nowcast/slots/info
```

## Daily Prediction
```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"date":"2023-04-29","MAX":34.5,"MIN":22.2,"AW":3.0,"RF":0.0,"SSH":426.0,"RF_lag1":0.0,"MAX_lag1":33.5,"MIN_lag1":22.0,"LABEL_lag1":0}'
```

## Nowcast — Single Slot (Slot 2, afternoon)
```bash
curl -X POST http://localhost:8000/nowcast/predict/slot/2 \
  -H "Content-Type: application/json" \
  -d '{"date":"2023-04-29","slot":2,"MAX":34.5,"MIN":22.2,"AW":3.0,"RF":0.0,"EVP":6.0,"DRNRF":0.0,"SSH":426.0,"RF_3d":0.0,"RF_7d":6.8,"RF_lag1":0.0,"LABEL_lag1":0,"CAPE":177.25,"K_INDEX":39.38,"LIFTED_INDEX":-6.15,"TOTALS_TOTALS":46.78,"PRECIP_WATER":40.55,"ERA5_T2M":300.12,"ERA5_D2M":294.73,"ERA5_U10":-3.96,"ERA5_V10":2.39,"ERA5_CAPE":177.25,"ERA5_SP":91197.0,"ERA5_t_500hPa":268.44,"ERA5_t_700hPa":283.67,"ERA5_t_850hPa":293.79,"ERA5_q_500hPa":0.00235,"ERA5_q_700hPa":0.00947,"ERA5_q_850hPa":0.01404,"ERA5_u_500hPa":5.16,"ERA5_u_700hPa":0.44,"ERA5_u_850hPa":-7.54,"ERA5_v_500hPa":2.34,"ERA5_v_700hPa":-0.14,"ERA5_v_850hPa":4.21,"ts_label_lag1_slot":0,"ts_any_yesterday":0}'
```

## Expected Response (Slot 2 on 2023-04-29)
```json
{
  "date": "2023-04-29",
  "slot": 2,
  "slot_label": "1201-1800 IST",
  "ts_probability": 0.7553,
  "ts_predicted": true,
  "threshold_used": 0.34,
  "alert_level": "RED"
}
```