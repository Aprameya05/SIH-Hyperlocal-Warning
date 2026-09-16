import pandas as pd
import json
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent
FORECAST_LOG = BASE / "data" / "forecast_log.csv"
OUTPUT_JSON = BASE / "forecast.json"

SLOTS = [
    {"slot": 0, "label": "Late Night", "time": "0001-0600 IST"},
    {"slot": 1, "label": "Morning", "time": "0601-1200 IST"},
    {"slot": 2, "label": "Afternoon", "time": "1201-1800 IST"},
    {"slot": 3, "label": "Evening", "time": "1801-2400 IST"},
]

def main():
    df = pd.read_csv(FORECAST_LOG)

    today = datetime.now().strftime("%Y-%m-%d")

    today_df = df.tail(4)

    slots_data = []
    peak_slot = None
    peak_prob = -1

    for _, row in today_df.iterrows():
        slot_info = next(s for s in SLOTS if s["slot"] == row["slot"])
        entry = {
            "slot": row["slot"],
            "label": slot_info["label"],
            "time": slot_info["time"],
            "probability": round(row["probability"], 3),
            "predicted": bool(row["predicted"]),
            "threshold": round(row["threshold"], 2)
        }
        if "primary" in row and row["primary"]:
            entry["primary"] = True
        slots_data.append(entry)

        if row["probability"] > peak_prob:
            peak_prob = row["probability"]
            peak_slot = row["slot"]

    forecast_json = {
        "date": today,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M IST"),
        "slots": slots_data,
        "alert_active": any(s["predicted"] for s in slots_data),
        "peak_slot": peak_slot,
        "peak_probability": round(peak_prob, 3),
        "model_version": "v3_calibrated"
    }

    OUTPUT_JSON.write_text(json.dumps(forecast_json, indent=2))
    print(f"Exported forecast.json for {today}")

if __name__ == "__main__":
    main()
