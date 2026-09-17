#!/usr/bin/env python3
"""
Multi-Task Learning Backbone
Shared transformer encoder with three task-specific output heads:
  - Thunderstorm probability (TS)
  - Cloudburst probability (CB)
  - Flash flood probability (FF)

Architecture:
  Input: 12 atmospheric features per grid cell (CAPE, CIN, PWAT, K-Index,
         Total Totals, wind shear 850-200, T850, T700, T500, Td850, Td700, CTT)
  Encoder: 4-layer transformer (d_model=256, nhead=8, dropout=0.1)
  Heads: 2-layer MLP per hazard type -> sigmoid output

Status: Architecture ready. Training requires labeled pan-India grid dataset
        that does not yet exist in clean form. Physics-based pipeline.py
        serves as production proxy until labeled data is collected.

Training:
  python backend/mtl_backbone.py --train --data data/labeled_grid.csv

Inference (once trained):
  python backend/mtl_backbone.py --infer --input data/pan_india_grid.json
"""

import argparse
import json
import math
import sys
from pathlib import Path

try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import DataLoader, Dataset
except ImportError:
    print("PyTorch not installed -- run: pip install torch")
    sys.exit(1)

# ---------------------------------------------------------------------------
# FEATURE NAMES (must match extract_features() order)
# ---------------------------------------------------------------------------

FEATURE_NAMES = [
    "cape",          # J/kg
    "cin",           # J/kg (stored as negative; absolute value used)
    "pwat_mm",       # mm
    "k_index",       # K (Celsius-equivalent differences)
    "totals_totals", # TT index
    "wind_shear_ms", # m/s
    "t850",          # K
    "t700",          # K
    "t500",          # K
    "td850",         # K
    "td700",         # K
    "ctt_c",         # deg C (cloud top temperature proxy)
]

N_FEATURES = len(FEATURE_NAMES)
FILL_VALUE = 0.0  # replace missing with 0 (normalized later)


# ---------------------------------------------------------------------------
# FEATURE NORMALIZATION CONSTANTS (approximate climatological ranges)
# ---------------------------------------------------------------------------

FEATURE_MEAN = [
    800.0,   # cape
    -50.0,   # cin
    35.0,    # pwat
    25.0,    # k_index
    48.0,    # totals_totals
    12.0,    # wind_shear_ms
    295.0,   # t850
    280.0,   # t700
    258.0,   # t500
    285.0,   # td850
    268.0,   # td700
    -15.0,   # ctt_c
]

FEATURE_STD = [
    1200.0,  # cape
    80.0,    # cin
    15.0,    # pwat
    12.0,    # k_index
    10.0,    # totals_totals
    10.0,    # wind_shear_ms
    8.0,     # t850
    8.0,     # t700
    8.0,     # t500
    8.0,     # td850
    8.0,     # td700
    20.0,    # ctt_c
]


# ---------------------------------------------------------------------------
# MODEL
# ---------------------------------------------------------------------------

class PositionalEncoding(nn.Module):
    """Sinusoidal lat/lon encoding appended to feature vector."""

    def __init__(self, d_model: int):
        super().__init__()
        self.d_model = d_model

    def forward(self, x: torch.Tensor, lat: torch.Tensor, lon: torch.Tensor) -> torch.Tensor:
        # lat, lon: (B,) in degrees; encode as sin/cos pair
        lat_rad = lat * math.pi / 180.0
        lon_rad = lon * math.pi / 180.0
        pos = torch.stack([
            torch.sin(lat_rad), torch.cos(lat_rad),
            torch.sin(lon_rad), torch.cos(lon_rad),
        ], dim=-1)  # (B, 4)
        # pad to d_model if needed (project with linear layer in encoder instead)
        return pos


class HazardHead(nn.Module):
    """Two-layer MLP head for a single hazard type."""

    def __init__(self, d_model: int, hidden: int = 64):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(d_model, hidden),
            nn.ReLU(),
            nn.Dropout(0.1),
            nn.Linear(hidden, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)


class MTLHazardModel(nn.Module):
    """
    Shared transformer encoder + three task-specific heads.

    Input:  (B, N_FEATURES) normalized atmospheric features
    Output: dict with keys 'ts', 'cb', 'ff' each of shape (B,)
    """

    def __init__(
        self,
        n_features: int = N_FEATURES,
        d_model: int = 256,
        nhead: int = 8,
        num_layers: int = 4,
        dropout: float = 0.1,
        head_hidden: int = 64,
    ):
        super().__init__()

        # Project features + 4-dim positional encoding to d_model
        self.input_proj = nn.Linear(n_features + 4, d_model)

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=nhead,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            batch_first=True,
            norm_first=True,  # pre-norm for training stability
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.pos_enc = PositionalEncoding(d_model)

        # Task heads
        self.head_ts = HazardHead(d_model, head_hidden)
        self.head_cb = HazardHead(d_model, head_hidden)
        self.head_ff = HazardHead(d_model, head_hidden)

        self._init_weights()

    def _init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)

    def forward(
        self,
        features: torch.Tensor,
        lat: torch.Tensor,
        lon: torch.Tensor,
    ) -> dict:
        pos = self.pos_enc(features, lat, lon)            # (B, 4)
        x = torch.cat([features, pos], dim=-1)            # (B, N_FEATURES+4)
        x = self.input_proj(x).unsqueeze(1)               # (B, 1, d_model)
        x = self.encoder(x).squeeze(1)                    # (B, d_model)
        return {
            "ts": self.head_ts(x),
            "cb": self.head_cb(x),
            "ff": self.head_ff(x),
        }


# ---------------------------------------------------------------------------
# DATASET
# ---------------------------------------------------------------------------

class GridDataset(Dataset):
    """
    Labeled pan-India grid dataset.
    Expected CSV columns: lat, lon, cape, cin, pwat_mm, k_index, totals_totals,
                          wind_shear_ms, t850, t700, t500, td850, td700, ctt_c,
                          ts_label, cb_label, ff_label  (0/1 binary)
    """

    def __init__(self, csv_path: Path):
        import csv
        rows = list(csv.DictReader(open(csv_path)))
        self.lat   = torch.tensor([float(r["lat"]) for r in rows])
        self.lon   = torch.tensor([float(r["lon"]) for r in rows])
        self.feats = torch.zeros(len(rows), N_FEATURES)
        for i, row in enumerate(rows):
            for j, name in enumerate(FEATURE_NAMES):
                val = row.get(name) or ""
                self.feats[i, j] = float(val) if val else FILL_VALUE

        # Normalize
        mean = torch.tensor(FEATURE_MEAN)
        std  = torch.tensor(FEATURE_STD).clamp(min=1e-6)
        self.feats = (self.feats - mean) / std

        self.labels = torch.zeros(len(rows), 3)
        for i, row in enumerate(rows):
            self.labels[i, 0] = float(row.get("ts_label", 0))
            self.labels[i, 1] = float(row.get("cb_label", 0))
            self.labels[i, 2] = float(row.get("ff_label", 0))

    def __len__(self):
        return len(self.lat)

    def __getitem__(self, idx):
        return self.feats[idx], self.lat[idx], self.lon[idx], self.labels[idx]


# ---------------------------------------------------------------------------
# TRAINING
# ---------------------------------------------------------------------------

def train(data_path: Path, epochs: int = 50, batch_size: int = 256, lr: float = 1e-3):
    dataset = GridDataset(data_path)
    loader  = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model  = MTLHazardModel().to(device)
    opt    = optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    loss_fn = nn.BCELoss()

    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0
        for feats, lat, lon, labels in loader:
            feats, lat, lon, labels = (
                feats.to(device), lat.to(device), lon.to(device), labels.to(device)
            )
            out = model(feats, lat, lon)
            loss = (
                loss_fn(out["ts"], labels[:, 0]) +
                loss_fn(out["cb"], labels[:, 1]) +
                loss_fn(out["ff"], labels[:, 2])
            )
            opt.zero_grad()
            loss.backward()
            opt.step()
            total_loss += loss.item()
        print(f"Epoch {epoch:3d}/{epochs}  loss={total_loss/len(loader):.4f}")

    save_path = Path("backend/mtl_model.pt")
    torch.save(model.state_dict(), save_path)
    print(f"Model saved to {save_path}")


# ---------------------------------------------------------------------------
# INFERENCE
# ---------------------------------------------------------------------------

def infer(grid_json: Path, model_path: Path = Path("backend/mtl_model.pt")):
    if not model_path.exists():
        print(f"ERROR: model not found at {model_path} -- train first")
        sys.exit(1)

    grid = json.loads(grid_json.read_text())
    cells = grid["grid_cells"]

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = MTLHazardModel().to(device)
    model.load_state_dict(torch.load(model_path, map_location=device))
    model.eval()

    mean = torch.tensor(FEATURE_MEAN)
    std  = torch.tensor(FEATURE_STD).clamp(min=1e-6)

    results = []
    with torch.no_grad():
        for c in cells:
            feats = torch.tensor(
                [c.get(n) or FILL_VALUE for n in FEATURE_NAMES], dtype=torch.float32
            )
            feats = ((feats - mean) / std).unsqueeze(0).to(device)
            lat   = torch.tensor([c["lat"]], dtype=torch.float32).to(device)
            lon   = torch.tensor([c["lon"]], dtype=torch.float32).to(device)
            out   = model(feats, lat, lon)
            results.append({
                "lat": c["lat"], "lon": c["lon"],
                "ts_mtl": round(out["ts"].item(), 4),
                "cb_mtl": round(out["cb"].item(), 4),
                "ff_mtl": round(out["ff"].item(), 4),
            })

    out_path = Path("data/mtl_predictions.json")
    out_path.write_text(json.dumps({"predictions": results}, separators=(",", ":")))
    print(f"Predictions written to {out_path} ({len(results)} cells)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MTL hazard model")
    parser.add_argument("--train",  action="store_true")
    parser.add_argument("--infer",  action="store_true")
    parser.add_argument("--data",   type=Path, default=Path("data/labeled_grid.csv"))
    parser.add_argument("--input",  type=Path, default=Path("data/pan_india_grid.json"))
    parser.add_argument("--epochs", type=int, default=50)
    args = parser.parse_args()

    if args.train:
        train(args.data, epochs=args.epochs)
    elif args.infer:
        infer(args.input)
    else:
        parser.print_help()
