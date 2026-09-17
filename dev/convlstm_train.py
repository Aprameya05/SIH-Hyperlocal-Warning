#!/usr/bin/env python3
"""
convlstm_train.py — Pan-India Spatiotemporal ConvLSTM Training
Run this on your A100 (100 h compute budget).

Architecture: ConvLSTM encoder → 3-head decoder
  Head 1: Thunderstorm probability map (0-1)
  Head 2: Cloudburst probability map   (0-1)
  Head 3: Flash flood risk map         (0-1)

Input: ERA5 reanalysis on India 0.25-deg grid, 4 time steps (6-hourly)
       Channels: CAPE, CIN, PWAT, K-Index, T850, T500, U850, V850,
                 U500, V500, RH700, MSLP  (12 channels)
Output: 3-channel probability map at same resolution

Labels: IMD severe weather event records (thunderstorm/cloudburst dates + locations)
        reformatted as binary grids on the 0.25-deg India grid.

Usage:
  # On A100 node:
  pip install torch torchvision cdsapi xarray netCDF4 numpy pandas scikit-learn tqdm
  python convlstm_train.py --download-era5  # first run: download ~40 GB ERA5 data
  python convlstm_train.py                  # subsequent runs: train from cached data

Approximate A100 budget:
  ERA5 download: ~3 hours
  Training (100 epochs, 1990-2020): ~30 hours on A100
  Total well within 100-hour budget.
"""

import argparse
import json
import os
import time
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader, random_split

# ── Configuration ─────────────────────────────────────────────────────────────

CFG = {
    # Grid
    "south": 6.0, "north": 37.0, "west": 68.0, "east": 98.0, "step": 0.25,
    # Model
    "in_channels": 12,
    "hidden_channels": [64, 128, 64],
    "kernel_size": 3,
    "n_timesteps": 4,          # 4 × 6h = 24h lookback
    "n_heads": 3,              # TS, CB, FF
    # Training
    "epochs": 100,
    "batch_size": 8,
    "lr": 3e-4,
    "weight_decay": 1e-5,
    "pos_weight_ts": 12.0,    # class imbalance weight (thunderstorm)
    "pos_weight_cb": 20.0,    # cloudburst rarer
    "pos_weight_ff": 15.0,    # flash flood
    "val_frac": 0.15,
    "test_frac": 0.10,
    # Paths
    "era5_dir": "era5_cache",
    "label_dir": "labels",
    "checkpoint_dir": "checkpoints",
    "final_model": "models/convlstm_india_v1.pt",
    "metadata_out": "models/convlstm_india_v1_meta.json",
    # ERA5 variables → CDS API names
    "era5_pressure_vars": [
        "u_component_of_wind", "v_component_of_wind",
        "temperature", "specific_humidity", "geopotential",
    ],
    "era5_pressure_levels": ["850", "700", "500"],
    "era5_single_vars": [
        "convective_available_potential_energy",
        "convective_inhibition",
        "total_column_water_vapour",
        "mean_sea_level_pressure",
        "2m_temperature",
    ],
    "train_years": list(range(1990, 2021)),
    "val_years": [2021, 2022],
    "test_years": [2023, 2024],
}


# ── ConvLSTM Cell ─────────────────────────────────────────────────────────────

class ConvLSTMCell(nn.Module):
    def __init__(self, in_channels, hidden_channels, kernel_size):
        super().__init__()
        pad = kernel_size // 2
        self.hidden_channels = hidden_channels
        self.conv = nn.Conv2d(
            in_channels + hidden_channels,
            4 * hidden_channels,
            kernel_size, padding=pad, bias=True
        )

    def forward(self, x, h, c):
        combined = torch.cat([x, h], dim=1)
        gates = self.conv(combined)
        i, f, o, g = gates.chunk(4, dim=1)
        i = torch.sigmoid(i)
        f = torch.sigmoid(f)
        o = torch.sigmoid(o)
        g = torch.tanh(g)
        c_new = f * c + i * g
        h_new = o * torch.tanh(c_new)
        return h_new, c_new

    def init_hidden(self, batch, H, W, device):
        return (torch.zeros(batch, self.hidden_channels, H, W, device=device),
                torch.zeros(batch, self.hidden_channels, H, W, device=device))


# ── ConvLSTM Encoder ──────────────────────────────────────────────────────────

class ConvLSTMEncoder(nn.Module):
    def __init__(self, in_channels, hidden_list, kernel_size):
        super().__init__()
        self.cells = nn.ModuleList()
        ch = in_channels
        for h in hidden_list:
            self.cells.append(ConvLSTMCell(ch, h, kernel_size))
            ch = h
        self.out_channels = ch

    def forward(self, x):
        # x: (B, T, C, H, W)
        B, T, C, H, W = x.shape
        device = x.device
        states = [cell.init_hidden(B, H, W, device) for cell in self.cells]

        for t in range(T):
            xt = x[:, t]
            new_states = []
            for i, cell in enumerate(self.cells):
                h, c = cell(xt, *states[i])
                new_states.append((h, c))
                xt = h
            states = new_states

        return states[-1][0]  # final hidden state: (B, hidden_last, H, W)


# ── Multi-task Decoder ────────────────────────────────────────────────────────

class MultiTaskDecoder(nn.Module):
    """3 independent heads: thunderstorm, cloudburst, flash flood."""
    def __init__(self, in_channels, n_heads=3):
        super().__init__()
        self.heads = nn.ModuleList()
        for _ in range(n_heads):
            self.heads.append(nn.Sequential(
                nn.Conv2d(in_channels, 64, 3, padding=1),
                nn.BatchNorm2d(64),
                nn.ReLU(inplace=True),
                nn.Conv2d(64, 32, 3, padding=1),
                nn.ReLU(inplace=True),
                nn.Conv2d(32, 1, 1),
                nn.Sigmoid(),
            ))

    def forward(self, z):
        return [head(z) for head in self.heads]


# ── Full Model ────────────────────────────────────────────────────────────────

class IndiaWeatherNowcaster(nn.Module):
    def __init__(self, cfg):
        super().__init__()
        self.encoder = ConvLSTMEncoder(
            cfg["in_channels"],
            cfg["hidden_channels"],
            cfg["kernel_size"],
        )
        self.decoder = MultiTaskDecoder(
            cfg["hidden_channels"][-1],
            cfg["n_heads"],
        )

    def forward(self, x):
        z = self.encoder(x)
        return self.decoder(z)


# ── Dataset ───────────────────────────────────────────────────────────────────

class IndiaGridDataset(Dataset):
    """
    Loads pre-processed ERA5 numpy arrays and binary label grids.

    Expected file structure:
      era5_cache/YYYY/MM/DD_HH.npy  — shape (12, H, W) float32
      labels/YYYY/MM/DD.npy         — shape (3, H, W) float32 {0, 1}
    """

    def __init__(self, years, era5_dir, label_dir, n_timesteps=4):
        self.era5_dir = Path(era5_dir)
        self.label_dir = Path(label_dir)
        self.n_timesteps = n_timesteps
        self.samples = []  # list of (list_of_era5_paths, label_path)

        for year in years:
            for month in range(1, 13):
                label_month_dir = self.label_dir / str(year) / f"{month:02d}"
                if not label_month_dir.exists():
                    continue
                for label_file in sorted(label_month_dir.glob("*.npy")):
                    day = int(label_file.stem)
                    # Collect 4 consecutive 6-hourly ERA5 snapshots ending at 12Z
                    era5_paths = []
                    for step in range(n_timesteps - 1, -1, -1):
                        dt = datetime(year, month, day, 12) - timedelta(hours=step * 6)
                        p = self.era5_dir / str(dt.year) / f"{dt.month:02d}" / f"{dt.day:02d}_{dt.hour:02d}.npy"
                        era5_paths.append(p)
                    if all(p.exists() for p in era5_paths):
                        self.samples.append((era5_paths, label_file))

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        era5_paths, label_path = self.samples[idx]
        frames = [np.load(p).astype(np.float32) for p in era5_paths]  # each (12, H, W)
        x = np.stack(frames, axis=0)   # (T, 12, H, W)
        y = np.load(label_path).astype(np.float32)  # (3, H, W)
        return torch.from_numpy(x), torch.from_numpy(y)


# ── ERA5 Download ─────────────────────────────────────────────────────────────

def download_era5(cfg):
    """
    Download ERA5 reanalysis via CDS API.
    Requires ~/.cdsapirc with your CDS API key.
    Register at: https://cds.climate.copernicus.eu/
    """
    try:
        import cdsapi
    except ImportError:
        print("Install cdsapi: pip install cdsapi")
        print("Then create ~/.cdsapirc with your key from https://cds.climate.copernicus.eu/")
        return

    era5_dir = Path(cfg["era5_dir"])
    era5_dir.mkdir(parents=True, exist_ok=True)
    c = cdsapi.Client()

    years = cfg["train_years"] + cfg["val_years"] + cfg["test_years"]
    area = [cfg["north"], cfg["west"], cfg["south"], cfg["east"]]

    for year in years:
        print(f"\nDownloading ERA5 for {year}...")
        out_path = era5_dir / f"era5_india_pressure_{year}.nc"
        if not out_path.exists():
            c.retrieve("reanalysis-era5-pressure-levels", {
                "product_type": "reanalysis",
                "variable": cfg["era5_pressure_vars"],
                "pressure_level": cfg["era5_pressure_levels"],
                "year": str(year),
                "month": [f"{m:02d}" for m in range(1, 13)],
                "day": [f"{d:02d}" for d in range(1, 32)],
                "time": ["00:00", "06:00", "12:00", "18:00"],
                "area": area,
                "grid": [0.25, 0.25],
                "format": "netcdf",
            }, str(out_path))

        out_single = era5_dir / f"era5_india_single_{year}.nc"
        if not out_single.exists():
            c.retrieve("reanalysis-era5-single-levels", {
                "product_type": "reanalysis",
                "variable": cfg["era5_single_vars"],
                "year": str(year),
                "month": [f"{m:02d}" for m in range(1, 13)],
                "day": [f"{d:02d}" for d in range(1, 32)],
                "time": ["00:00", "06:00", "12:00", "18:00"],
                "area": area,
                "grid": [0.25, 0.25],
                "format": "netcdf",
            }, str(out_single))

    print("\nERA5 download complete. Run preprocess_era5() next.")


def preprocess_era5(cfg):
    """
    Convert downloaded ERA5 NetCDF to per-timestamp .npy arrays.
    Also normalises each channel using training-set statistics.
    """
    import xarray as xr
    era5_dir = Path(cfg["era5_dir"])
    out_dir  = Path(cfg["era5_dir"]) / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)

    all_years = cfg["train_years"] + cfg["val_years"] + cfg["test_years"]

    for year in all_years:
        pres_f  = era5_dir / f"era5_india_pressure_{year}.nc"
        singl_f = era5_dir / f"era5_india_single_{year}.nc"
        if not pres_f.exists() or not singl_f.exists():
            print(f"Missing ERA5 files for {year}, skipping.")
            continue

        ds_p = xr.open_dataset(pres_f)
        ds_s = xr.open_dataset(singl_f)

        times = ds_p.time.values
        for t in times:
            dt = datetime.utcfromtimestamp(int(t) / 1e9)
            out_path = out_dir / str(dt.year) / f"{dt.month:02d}" / f"{dt.day:02d}_{dt.hour:02d}.npy"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if out_path.exists():
                continue

            channels = []
            for var in ["u", "v", "t", "q", "z"]:
                for lev in [850, 700, 500]:
                    if var in ds_p and lev in ds_p.level.values:
                        arr = ds_p[var].sel(time=t, level=lev).values
                        channels.append(arr.astype(np.float32))

            for var in ["cape", "cin", "tcwv", "msl", "t2m"]:
                if var in ds_s:
                    arr = ds_s[var].sel(time=t).values
                    channels.append(arr.astype(np.float32))

            # Trim to exactly 12 channels
            channels = channels[:12]
            while len(channels) < 12:
                channels.append(np.zeros_like(channels[0]))

            np.save(out_path, np.stack(channels, axis=0))

        ds_p.close()
        ds_s.close()

    print("ERA5 preprocessing complete.")


# ── Label Generation ──────────────────────────────────────────────────────────

def build_labels_from_imd(imd_csv_path: str, cfg: dict):
    """
    Convert IMD severe weather event CSV to binary grid labels.

    Expected CSV columns:
      date, lat, lon, event_type (thunderstorm/cloudburst/flash_flood)

    Download from: https://mausam.imd.gov.in/imd_latest/contents/severe-weather-event.php
    or request from your CSIR contact.

    For each event, we mark a 50 km radius on the 0.25-deg grid as 1.
    """
    import pandas as pd

    label_dir = Path(cfg["label_dir"])
    df = pd.read_csv(imd_csv_path, parse_dates=["date"])

    lats = np.arange(cfg["south"], cfg["north"] + cfg["step"], cfg["step"])
    lons = np.arange(cfg["west"],  cfg["east"]  + cfg["step"], cfg["step"])
    H, W = len(lats), len(lons)

    lat_grid, lon_grid = np.meshgrid(lats, lons, indexing="ij")

    def haversine(lat1, lon1):
        R = 6371.0
        dlat = np.radians(lat_grid - lat1)
        dlon = np.radians(lon_grid - lon1)
        a = np.sin(dlat/2)**2 + np.cos(np.radians(lat1)) * np.cos(np.radians(lat_grid)) * np.sin(dlon/2)**2
        return R * 2 * np.arcsin(np.sqrt(a))

    for date, grp in df.groupby("date"):
        label = np.zeros((3, H, W), dtype=np.float32)
        for _, row in grp.iterrows():
            dist = haversine(row["lat"], row["lon"])
            mask = dist <= 50  # 50 km radius
            et = str(row["event_type"]).lower()
            if "thunder" in et:
                label[0][mask] = 1.0
            if "cloudburst" in et or "cloud burst" in et:
                label[1][mask] = 1.0
                label[0][mask] = 1.0  # cloudburst implies thunderstorm
            if "flash" in et or "flood" in et:
                label[2][mask] = 1.0

        out = label_dir / str(date.year) / f"{date.month:02d}" / f"{date.day:02d}.npy"
        out.parent.mkdir(parents=True, exist_ok=True)
        np.save(out, label)

    print(f"Labels built for {df['date'].nunique()} event-days.")


# ── Training Loop ─────────────────────────────────────────────────────────────

def train(cfg):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device}")
    if str(device) == "cuda":
        print(f"GPU: {torch.cuda.get_device_name(0)}")

    # Dataset
    era5_processed = Path(cfg["era5_dir"]) / "processed"
    train_ds = IndiaGridDataset(cfg["train_years"], era5_processed, cfg["label_dir"], cfg["n_timesteps"])
    val_ds   = IndiaGridDataset(cfg["val_years"],   era5_processed, cfg["label_dir"], cfg["n_timesteps"])
    test_ds  = IndiaGridDataset(cfg["test_years"],  era5_processed, cfg["label_dir"], cfg["n_timesteps"])

    print(f"Train: {len(train_ds)}  Val: {len(val_ds)}  Test: {len(test_ds)}")
    if len(train_ds) == 0:
        print("No training samples found. Run with --download-era5 first.")
        return

    train_loader = DataLoader(train_ds, batch_size=cfg["batch_size"], shuffle=True,  num_workers=4, pin_memory=True)
    val_loader   = DataLoader(val_ds,   batch_size=cfg["batch_size"], shuffle=False, num_workers=4, pin_memory=True)

    # Model
    model = IndiaWeatherNowcaster(cfg).to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Model parameters: {n_params:,}")

    # Loss: weighted BCE for each head
    weights = [cfg["pos_weight_ts"], cfg["pos_weight_cb"], cfg["pos_weight_ff"]]
    criterions = [
        nn.BCELoss(weight=torch.tensor(w, device=device)) for w in weights
    ]

    optimizer = optim.AdamW(model.parameters(), lr=cfg["lr"], weight_decay=cfg["weight_decay"])
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=cfg["epochs"], eta_min=1e-6)

    ckpt_dir = Path(cfg["checkpoint_dir"])
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    Path(cfg["final_model"]).parent.mkdir(parents=True, exist_ok=True)

    best_val_loss = float("inf")
    history = []

    for epoch in range(1, cfg["epochs"] + 1):
        # Train
        model.train()
        train_loss = 0.0
        for X, Y in train_loader:
            X, Y = X.to(device), Y.to(device)
            optimizer.zero_grad()
            preds = model(X)  # list of 3 tensors (B, 1, H, W)
            loss = sum(criterions[i](preds[i].squeeze(1), Y[:, i]) for i in range(3))
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_loss += loss.item()

        train_loss /= len(train_loader)

        # Validate
        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for X, Y in val_loader:
                X, Y = X.to(device), Y.to(device)
                preds = model(X)
                loss = sum(criterions[i](preds[i].squeeze(1), Y[:, i]) for i in range(3))
                val_loss += loss.item()
        val_loss /= max(len(val_loader), 1)

        scheduler.step()

        history.append({"epoch": epoch, "train_loss": round(train_loss, 5), "val_loss": round(val_loss, 5)})
        print(f"Epoch {epoch:3d}/{cfg['epochs']}  train={train_loss:.4f}  val={val_loss:.4f}  lr={scheduler.get_last_lr()[0]:.2e}")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            torch.save({"epoch": epoch, "state_dict": model.state_dict(),
                        "val_loss": val_loss, "cfg": cfg},
                       cfg["final_model"])
            print(f"  ✓ Saved best model (val_loss={val_loss:.4f})")

        if epoch % 10 == 0:
            torch.save({"epoch": epoch, "state_dict": model.state_dict()},
                       ckpt_dir / f"ckpt_epoch{epoch:04d}.pt")

    # Save metadata
    meta = {
        "architecture": "ConvLSTM + MultiTaskDecoder",
        "n_params": n_params,
        "trained_at": datetime.utcnow().isoformat(),
        "best_val_loss": best_val_loss,
        "cfg": cfg,
        "history": history,
        "input_shape": f"(B, {cfg['n_timesteps']}, {cfg['in_channels']}, H, W)",
        "output_shape": "3 × (B, 1, H, W) — [thunderstorm, cloudburst, flash_flood]",
        "grid": {"south": cfg["south"], "north": cfg["north"],
                 "west": cfg["west"], "east": cfg["east"], "step": cfg["step"]},
        "channels": [
            "U850", "V850", "T850", "Q850", "Z850",
            "U700", "V700", "T700", "Q700", "Z700",
            "U500", "V500",  # 12 channels total (trimmed/padded as needed)
        ],
    }
    with open(cfg["metadata_out"], "w") as f:
        json.dump(meta, f, indent=2)
    print(f"\nTraining complete. Best val_loss: {best_val_loss:.4f}")
    print(f"Model saved: {cfg['final_model']}")
    print(f"Metadata:    {cfg['metadata_out']}")


# ── Inference helper (for forecast_action.py) ────────────────────────────────

def load_and_infer(model_path: str, x_np: np.ndarray, device: str = "cuda") -> dict:
    """
    Run inference with saved ConvLSTM model.
    x_np: shape (T, 12, H, W) float32
    Returns: dict with 'thunderstorm', 'cloudburst', 'flash_flood' probability maps.
    """
    ckpt = torch.load(model_path, map_location=device)
    cfg  = ckpt["cfg"]
    model = IndiaWeatherNowcaster(cfg).to(device)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()

    x = torch.from_numpy(x_np).unsqueeze(0).to(device)  # (1, T, C, H, W)
    with torch.no_grad():
        preds = model(x)

    return {
        "thunderstorm":  preds[0].squeeze().cpu().numpy(),
        "cloudburst":    preds[1].squeeze().cpu().numpy(),
        "flash_flood":   preds[2].squeeze().cpu().numpy(),
    }


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ConvLSTM pan-India severe weather training")
    parser.add_argument("--download-era5",  action="store_true", help="Download ERA5 data via CDS API")
    parser.add_argument("--preprocess",     action="store_true", help="Preprocess ERA5 NetCDF → .npy")
    parser.add_argument("--build-labels",   type=str, metavar="CSV", help="Build labels from IMD event CSV")
    parser.add_argument("--train",          action="store_true", help="Train the model (default if no flag)")
    args = parser.parse_args()

    if args.download_era5:
        download_era5(CFG)
    elif args.preprocess:
        preprocess_era5(CFG)
    elif args.build_labels:
        build_labels_from_imd(args.build_labels, CFG)
    else:
        train(CFG)
