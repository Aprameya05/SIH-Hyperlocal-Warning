"""
Re-runnable check that reproduces the label-count findings in REPORT.md section 6.
Run from the repo root: python3 data_inventory/label_verification.py
"""
import pandas as pd

df = pd.read_csv("data/bengaluru_6hr_training_dataset_cb_ff.csv")
print("rows:", len(df))
print("\nTS label:\n", df["ts_label"].value_counts())
print("\nCB label:\n", df["cb_label"].value_counts())
print("\nFF label:\n", df["ff_label"].value_counts())
print("\nyear range:", df["year"].min(), "-", df["year"].max())
