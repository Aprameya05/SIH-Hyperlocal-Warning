"""
train_v6_slot_models.py
=======================
CSIR Thunderstorm Nowcasting System — v6 Slot Model Training
Run on Google Colab A100 (or local GPU).

WHAT'S NEW IN v6 vs v5:
  1. Himawari-9 Band 13 BT features fused into XGBoost training
     (min_bt_50km, cold_pixels_count, bt_trend_1h, bt_vobl)
     → merged from data/himawari_backtest.csv if available
  2. October-specific class weighting to fix DOY_sin suppression bias
     → separate scale_pos_weight for October slot 2 (2x normal)
  3. New derived features:
       wet_bulb_potential_temp  — parcel buoyancy proxy
       convective_coupling      — CAPE * CIN (inhibition-release coupling)
       rf_std_7d                — rainfall variance (proxy for regime persistence)
       cape_trend               — CAPE lag1 delta (unstable growth signal)
       moisture_depth           — q850 * (1 - q500/q850) convective moisture depth
       low_level_convergence    — (u850^2 + v850^2)^0.5 / (u500^2+v500^2+0.01)^0.5
  4. A100 GPU acceleration: tree_method="hist", device="cuda"
  5. 100 Optuna trials (vs 50 in v5)
  6. Walk-forward CV: train on [2014..year-1], test on [year]
     separately per slot — more realistic for IMD operational evaluation
  7. Saves production artifact with BOTH key schemas for compatibility:
       artifact["model"] = artifact["calibrated"] = final_model
       artifact["feature_cols"] = artifact["features"] = feature_cols
  8. Isotonic calibration fitted on OOF predictions

USAGE (Colab):
  !python train_v6_slot_models.py              # all 4 slots
  !python train_v6_slot_models.py --slot 2     # one slot only
  !python train_v6_slot_models.py --no-gpu     # CPU fallback

OUTPUT:
  models/nowcast_slot{n}_xgb_v6_himawari.pkl  — if Himawari data present
  models/nowcast_slot{n}_xgb_v6_temporal.pkl  — if no Himawari data
  results/v6_evaluation.csv
  results/v6_summary.txt

Author: Aprameya, CSIR Thunderstorm Project, 2026-08
"""

import argparse
import json
import math
import warnings
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import optuna
from sklearn.calibration import CalibratedClassifierCV
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold
from xgboost import XGBClassifier

warnings.filterwarnings("ignore")
optuna.logging.set_verbosity(optuna.logging.WARNING)

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE    = Path(".")
DATA    = BASE / "data"
MODELS  = BASE / "models"
RESULTS = BASE / "results"
MODELS.mkdir(exist_ok=True)
RESULTS.mkdir(exist_ok=True)

TRAINING_CSV     = DATA / "bengaluru_6hr_training_dataset.csv"
HIMAWARI_BT_CSV  = DATA / "himawari_backtest.csv"   # optional — produced by backtest_himawari.py

# ── Config ─────────────────────────────────────────────────────────────────────
N_TRIALS      = 100           # Optuna trials — A100 can handle 100 in ~20 min
N_CV_FOLDS    = 5
RANDOM_STATE  = 42
TEST_YEAR_MIN = 2023          # hold-out test years

SLOT_NAMES = {0: "0001-0600 IST", 1: "0601-1200 IST",
              2: "1201-1800 IST", 3: "1801-2400 IST"}

# October-specific Slot 2 class weight multiplier (DOY_sin fix)
OCT_SLOT2_WEIGHT_MULT = 2.0

# ── Metrics ────────────────────────────────────────────────────────────────────
def compute_metrics(y_true, y_prob, threshold):
    y_pred = (y_prob >= threshold).astype(int)
    tp = int(((y_pred == 1) & (y_true == 1)).sum())
    fp = int(((y_pred == 1) & (y_true == 0)).sum())
    fn = int(((y_pred == 0) & (y_true == 1)).sum())
    tn = int(((y_pred == 0) & (y_true == 0)).sum())
    pod  = tp / (tp + fn)      if (tp + fn) > 0  else 0.0
    far  = fp / (tp + fp)      if (tp + fp) > 0  else 0.0
    csi  = tp / (tp + fp + fn) if (tp + fp + fn) > 0 else 0.0
    hss_num = 2 * (tp * tn - fp * fn)
    hss_den = (tp + fn) * (fn + tn) + (tp + fp) * (fp + tn)
    hss  = hss_num / hss_den  if hss_den > 0 else 0.0
    brier = float(np.mean((y_prob - y_true) ** 2))
    auroc = roc_auc_score(y_true, y_prob) if y_true.sum() > 0 else float("nan")
    return dict(TP=tp, FP=fp, FN=fn, TN=tn,
                AUROC=round(auroc, 4), POD=round(pod, 4),
                FAR=round(far, 4), CSI=round(csi, 4),
                HSS=round(hss, 4), BRIER=round(brier, 4))


def find_best_threshold(y_true, y_prob, beta=1.5, min_t=0.10):
    """Maximise F-beta score (beta>1 weights recall over precision).
    beta=1.5 prioritises POD for IMD operational use.
    """
    best_score, best_t = 0.0, 0.5
    for t in np.arange(min_t, 0.90, 0.01):
        y_pred = (y_prob >= t).astype(int)
        tp = ((y_pred == 1) & (y_true == 1)).sum()
        fp = ((y_pred == 1) & (y_true == 0)).sum()
        fn = ((y_pred == 0) & (y_true == 1)).sum()
        denom = (1 + beta**2) * tp + beta**2 * fn + fp
        score = (1 + beta**2) * tp / denom if denom > 0 else 0.0
        if score > best_score:
            best_score, best_t = score, t
    return round(float(best_t), 2)


# ── Feature engineering ─────────────────────────────────────────────────────────
def add_v6_features(df: pd.DataFrame) -> pd.DataFrame:
    """Adds derived features not present in the v5 training dataset."""
    df = df.copy()

    # --- Thermodynamic coupling ---
    if "CAPE" in df.columns and "CIN" in df.columns:
        df["convective_coupling"] = df["CAPE"] * df["CIN"].abs()
        # Lifted potential: high CAPE + low CIN → convection likely
        df["cape_cin_ratio"] = df["CAPE"] / (df["CIN"].abs() + 1.0)

    # --- CAPE trend (day-over-day change as a proxy for atmosphere destabilising) ---
    if "CAPE" in df.columns:
        df["cape_trend"] = df.groupby("slot")["CAPE"].diff().fillna(0.0)

    # --- Moisture depth (convective moisture column) ---
    if all(c in df.columns for c in ["ERA5_q_850hPa", "ERA5_q_500hPa"]):
        df["moisture_depth"] = df["ERA5_q_850hPa"] * (
            1 - df["ERA5_q_500hPa"] / (df["ERA5_q_850hPa"] + 1e-9)
        )

    # --- Low-level convergence proxy (850 vs 500 wind speed ratio) ---
    if all(c in df.columns for c in ["ERA5_u_850hPa", "ERA5_v_850hPa",
                                      "ERA5_u_500hPa", "ERA5_v_500hPa"]):
        ws850 = (df["ERA5_u_850hPa"]**2 + df["ERA5_v_850hPa"]**2)**0.5
        ws500 = (df["ERA5_u_500hPa"]**2 + df["ERA5_v_500hPa"]**2)**0.5
        df["low_level_convergence"] = ws850 / (ws500 + 0.01)

    # --- Wet-bulb potential temperature proxy (buoyancy) ---
    if all(c in df.columns for c in ["ERA5_t_850hPa", "ERA5_q_850hPa"]):
        # θw ≈ T_850 + 2491*q_850 (simplified, no pressure correction needed for relative comparison)
        df["wet_bulb_potential_temp"] = (
            df["ERA5_t_850hPa"] + 2491.0 * df["ERA5_q_850hPa"]
        )

    # --- Rainfall variance (regime persistence) ---
    if "RF" in df.columns:
        rf_roll = df.groupby("slot")["RF"].rolling(7, min_periods=1).std()
        df["rf_std_7d"] = rf_roll.reset_index(level=0, drop=True).fillna(0.0)

    # --- Lapse rate 850→500 ---
    if all(c in df.columns for c in ["ERA5_t_850hPa", "ERA5_t_500hPa"]):
        # Both in Kelvin; lapse rate in K/hPa
        df["lapse_rate_850_500"] = (df["ERA5_t_850hPa"] - df["ERA5_t_500hPa"]) / 350.0

    # --- October flag (for DOY_sin suppression correction) ---
    if "month" in df.columns:
        df["is_october_slot2"] = (
            (df["month"] == 10) & (df["slot"] == 2)
        ).astype(int)

    # --- Station-level convective suppression flag (K-Index based) ---
    # monsoon_break_flag = 1 when K-Index < 25, indicating suppressed convective
    # potential at the VOBL/43295 station level.
    #
    # NOTE: This is NOT the IMD active/break monsoon classification (Rajeevan et al. 2010),
    # which is defined for the monsoon core zone (central India, 18-28N) using GPCP/TRMM
    # rainfall anomalies — a definition physically inappropriate for the South Peninsula.
    # Zero break spells are observed at Bengaluru under that national-scale classification.
    # The Rajeevan active/break feature was explicitly removed from this model per guidance
    # from Dr. Geeta Agnihotri (Scientist F, IMD). This K-Index threshold is independently
    # derived from VOBL radiosonde climatology and is scientifically valid for local use.
    if "K_INDEX" in df.columns:
        df["monsoon_break_flag"] = (df["K_INDEX"] < 25).astype(int)

    return df


# ── Himawari BT features ────────────────────────────────────────────────────────
def load_himawari_bt(himawari_path: Path) -> pd.DataFrame | None:
    """Load Himawari backtest CSV, return indexed by (date, slot) for merging."""
    if not himawari_path.exists():
        print(f"  Himawari backtest not found at {himawari_path}")
        print("  → Training WITHOUT satellite features (still v6)")
        print("  → To add Himawari: run backtest_himawari.py first, then retrain")
        return None

    bt = pd.read_csv(himawari_path, parse_dates=["date"])
    print(f"  Himawari BT loaded: {len(bt)} rows, {bt['date'].min()} → {bt['date'].max()}")

    # Expected columns: date, slot, min_bt_50km, cold_pixels_count, vobl_bt_celsius, bt_trend_1h
    bt_cols = ["date", "slot", "min_bt_50km", "cold_pixels_count",
                "vobl_bt_celsius", "bt_trend_1h"]
    bt_cols_present = [c for c in bt_cols if c in bt.columns]
    print(f"  Columns found: {bt_cols_present}")
    return bt[bt_cols_present]


# ── Isotonic calibrator ─────────────────────────────────────────────────────────
def fit_isotonic_calibrator(oof_prob: np.ndarray, y_train: np.ndarray) -> IsotonicRegression:
    cal = IsotonicRegression(out_of_bounds="clip")
    cal.fit(oof_prob, y_train)
    return cal


def calibrate_prob(cal, raw_prob: float) -> float:
    return float(cal.predict([raw_prob])[0])


# ── XGBoost wrapper ─────────────────────────────────────────────────────────────
def make_xgb(params: dict, use_gpu: bool) -> XGBClassifier:
    p = {k: v for k, v in params.items() if k != "early_stopping_rounds"}
    if use_gpu:
        p["tree_method"] = "hist"
        p["device"]      = "cuda"
    else:
        p["tree_method"] = "hist"
        p["device"]      = "cpu"
    p["verbosity"] = 0
    return XGBClassifier(**p)


def make_xgb_es(params: dict, use_gpu: bool) -> XGBClassifier:
    p = dict(params)
    if use_gpu:
        p["tree_method"] = "hist"
        p["device"]      = "cuda"
    else:
        p["tree_method"] = "hist"
        p["device"]      = "cpu"
    p["verbosity"] = 0
    return XGBClassifier(**p)


# ── Slot class weight — October fix ────────────────────────────────────────────
def compute_scale_pos_weight(y: np.ndarray, month_col: np.ndarray | None,
                              slot_id: int) -> float | np.ndarray:
    """For Slot 2 in October, return per-sample weights doubling October positives."""
    n_pos = y.sum()
    n_neg = (y == 0).sum()
    base_spw = n_neg / n_pos if n_pos > 0 else 1.0

    if slot_id != 2 or month_col is None:
        return base_spw

    # Sample weight: 2x for October positive samples
    weights = np.ones(len(y), dtype=float)
    oct_pos = (month_col == 10) & (y == 1)
    weights[oct_pos] = 2.0
    print(f"  October positive samples in Slot 2: {oct_pos.sum()} (weight×2)")
    return weights


# ── Walk-forward validation ─────────────────────────────────────────────────────
def walk_forward_eval(df_slot: pd.DataFrame, feature_cols: list,
                      params: dict, use_gpu: bool, n_folds: int = 3) -> float:
    """Train on [2014..year-1], test on [year] for each year in test set.
    Returns mean AUROC across walk-forward steps.
    """
    test_years = sorted(df_slot[df_slot["year"] >= TEST_YEAR_MIN]["year"].unique())
    if len(test_years) == 0:
        return 0.0

    aurocs = []
    for yr in test_years:
        train = df_slot[df_slot["year"] < yr]
        test  = df_slot[df_slot["year"] == yr]
        if len(train) == 0 or test["ts_label"].sum() == 0:
            continue
        X_tr = train[feature_cols].fillna(0).values
        y_tr = train["ts_label"].values
        X_te = test[feature_cols].fillna(0).values
        y_te = test["ts_label"].values
        m = make_xgb(params, use_gpu)
        m.fit(X_tr, y_tr)
        prob = m.predict_proba(X_te)[:, 1]
        try:
            aurocs.append(roc_auc_score(y_te, prob))
        except Exception:
            pass

    return float(np.mean(aurocs)) if aurocs else 0.0


# ── Main training loop ──────────────────────────────────────────────────────────
def train_slot(slot_id: int, df: pd.DataFrame, feature_cols: list,
               use_gpu: bool, has_himawari: bool) -> dict:
    slot_df = df[df["slot"] == slot_id].copy().reset_index(drop=True)
    train_df = slot_df[slot_df["year"] < TEST_YEAR_MIN]
    test_df  = slot_df[slot_df["year"] >= TEST_YEAR_MIN]

    if len(train_df) == 0:
        print(f"  Slot {slot_id}: no training data!")
        return {}

    X_train = train_df[feature_cols].fillna(0)   # keep as DataFrame — preserves feature names in XGBoost booster
    y_train = train_df["ts_label"].values
    X_test  = test_df[feature_cols].fillna(0)
    y_test  = test_df["ts_label"].values

    n_pos = int(y_train.sum())
    n_neg = int((y_train == 0).sum())
    print(f"  Train: {len(y_train)} rows | {n_pos} pos ({n_pos/len(y_train)*100:.1f}%)")
    print(f"  Test : {len(y_test)} rows  | {int(y_test.sum())} pos ({y_test.mean()*100:.1f}%)")

    # Sample weights for October Slot 2 fix
    month_col = train_df["month"].values if "month" in train_df.columns else None
    sample_weight = compute_scale_pos_weight(y_train, month_col, slot_id)
    base_spw = (n_neg / n_pos) if n_pos > 0 else 1.0
    has_sample_weights = isinstance(sample_weight, np.ndarray)

    # ── Optuna ────────────────────────────────────────────────────────────────
    print(f"  Running Optuna ({N_TRIALS} trials)...")

    def objective(trial):
        params = {
            "n_estimators":          trial.suggest_int("n_estimators", 100, 800),
            "max_depth":             trial.suggest_int("max_depth", 3, 8),
            "learning_rate":         trial.suggest_float("learning_rate", 0.005, 0.2, log=True),
            "subsample":             trial.suggest_float("subsample", 0.5, 1.0),
            "colsample_bytree":      trial.suggest_float("colsample_bytree", 0.5, 1.0),
            "colsample_bylevel":     trial.suggest_float("colsample_bylevel", 0.5, 1.0),
            "min_child_weight":      trial.suggest_int("min_child_weight", 1, 15),
            "reg_alpha":             trial.suggest_float("reg_alpha", 1e-4, 20.0, log=True),
            "reg_lambda":            trial.suggest_float("reg_lambda", 1e-4, 20.0, log=True),
            "gamma":                 trial.suggest_float("gamma", 0.0, 5.0),
            "scale_pos_weight":      base_spw,
            "random_state":          RANDOM_STATE,
            "eval_metric":           "auc",
            "early_stopping_rounds": 30,
        }
        skf    = StratifiedKFold(n_splits=N_CV_FOLDS, shuffle=True, random_state=RANDOM_STATE)
        scores = []
        X_tr_arr = X_train.values  # numpy for indexing in CV
        for tr_idx, val_idx in skf.split(X_tr_arr, y_train):
            m = make_xgb_es(params, use_gpu)
            sw = sample_weight[tr_idx] if has_sample_weights else None
            m.fit(X_train.iloc[tr_idx], y_train[tr_idx],
                  eval_set=[(X_train.iloc[val_idx], y_train[val_idx])],
                  sample_weight=sw,
                  verbose=False)
            prob = m.predict_proba(X_train.iloc[val_idx])[:, 1]
            if y_train[val_idx].sum() > 0:
                scores.append(roc_auc_score(y_train[val_idx], prob))
        return float(np.mean(scores)) if scores else 0.5

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=RANDOM_STATE))
    study.optimize(objective, n_trials=N_TRIALS, show_progress_bar=True)
    cv_auroc    = round(study.best_value, 4)
    best_params = dict(study.best_params)
    best_params.update({
        "scale_pos_weight": base_spw,
        "random_state":     RANDOM_STATE,
        "eval_metric":      "auc",
    })
    print(f"  Best CV AUROC: {cv_auroc}")

    # ── Final model ───────────────────────────────────────────────────────────
    print("  Training final model...")
    model = make_xgb(best_params, use_gpu)
    sw = sample_weight if has_sample_weights else None
    model.fit(X_train, y_train, sample_weight=sw)

    # ── OOF for calibration + threshold tuning ────────────────────────────────
    print("  OOF calibration + threshold tuning...")
    skf_oof = StratifiedKFold(n_splits=N_CV_FOLDS, shuffle=True, random_state=RANDOM_STATE)
    oof_prob = np.zeros(len(y_train))
    X_tr_arr = X_train.values
    for tr_idx, val_idx in skf_oof.split(X_tr_arr, y_train):
        m = make_xgb(best_params, use_gpu)
        sw_oof = sample_weight[tr_idx] if has_sample_weights else None
        m.fit(X_train.iloc[tr_idx], y_train[tr_idx], sample_weight=sw_oof)
        oof_prob[val_idx] = m.predict_proba(X_train.iloc[val_idx])[:, 1]

    calibrator = fit_isotonic_calibrator(oof_prob, y_train)
    oof_cal    = np.array([calibrate_prob(calibrator, p) for p in oof_prob])
    threshold  = find_best_threshold(y_train, oof_cal, beta=1.5, min_t=0.10)
    print(f"  Threshold (F1.5): {threshold}")

    # ── Test evaluation ───────────────────────────────────────────────────────
    if len(y_test) > 0 and y_test.sum() > 0:
        raw_test_prob = model.predict_proba(X_test.values)[:, 1]
        cal_test_prob = np.array([calibrate_prob(calibrator, p) for p in raw_test_prob])
        metrics = compute_metrics(y_test, cal_test_prob, threshold)
        print(f"  AUROC={metrics['AUROC']} POD={metrics['POD']} FAR={metrics['FAR']} "
              f"CSI={metrics['CSI']} HSS={metrics['HSS']} BRIER={metrics['BRIER']}")
    else:
        metrics = {}
        print("  No test labels — skipping evaluation")

    # ── Walk-forward validation ───────────────────────────────────────────────
    wf_auroc = walk_forward_eval(slot_df, feature_cols, best_params, use_gpu)
    print(f"  Walk-forward AUROC: {wf_auroc:.4f}")

    # ── Feature importance ────────────────────────────────────────────────────
    booster = model.get_booster()
    fi = booster.get_score(importance_type="gain")
    top10 = sorted(fi.items(), key=lambda x: x[1], reverse=True)[:10]
    print("  Top 10 features by gain:")
    for feat, score in top10:
        print(f"    {feat}: {score:.1f}")

    return {
        "model":         model,
        "calibrator":    calibrator,
        "feature_cols":  feature_cols,
        "threshold":     threshold,
        "cv_auroc":      cv_auroc,
        "wf_auroc":      wf_auroc,
        "best_params":   best_params,
        "metrics":       metrics,
        "top_features":  top10,
        "has_himawari":  has_himawari,
        "n_train":       len(y_train),
        "n_pos_train":   n_pos,
    }


# ── Save artifact ───────────────────────────────────────────────────────────────
def save_artifact(result: dict, slot_id: int, has_himawari: bool) -> Path:
    suffix = "himawari" if has_himawari else "temporal"
    out    = MODELS / f"nowcast_slot{slot_id}_xgb_v6_{suffix}.pkl"

    model      = result["model"]
    cal        = result["calibrator"]
    feat_cols  = result["feature_cols"]
    threshold  = result["threshold"]
    cv_auroc   = result["cv_auroc"]

    # Save Booster in UBJ format for cross-version stability
    booster_dir = MODELS / "boosters"
    booster_dir.mkdir(exist_ok=True)
    ubj_path = booster_dir / f"{out.stem}.ubj"
    model.get_booster().save_model(str(ubj_path))
    print(f"  Booster saved → {ubj_path.name} ({ubj_path.stat().st_size / 1024:.0f} KB)")

    artifact = {
        # Production keys (both schemas — forecast_action.py handles either)
        "model":           model,          # primary key for forecast_action.py
        "calibrated":      model,          # alternate key (v4/v5 schema)
        "calibrator":      cal,            # isotonic calibrator
        "feature_cols":    feat_cols,      # primary feature key
        "features":        feat_cols,      # alternate feature key (v4/v5 schema)
        "threshold":       threshold,
        "auroc":           cv_auroc,
        "wf_auroc":        result["wf_auroc"],
        "metrics":         result["metrics"],
        "top_features":    result["top_features"],
        "slot":            slot_id,
        "has_himawari":    has_himawari,
        "model_version":   f"v6_{'himawari' if has_himawari else 'temporal'}",
        "sklearn_version": __import__("sklearn").__version__,
        "xgb_version":     __import__("xgboost").__version__,
        "booster_ubj_path": str(ubj_path),
        "trained_at":      datetime.now().isoformat(),
    }
    joblib.dump(artifact, out)
    print(f"  Artifact saved → {out.name} ({out.stat().st_size / 1024:.0f} KB)")
    return out


# ── Entry point ─────────────────────────────────────────────────────────────────
def main():
    global N_TRIALS  # must be first before any reference to N_TRIALS in this scope
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, choices=[0, 1, 2, 3], default=None)
    ap.add_argument("--no-gpu", action="store_true", help="Force CPU mode")
    ap.add_argument("--n-trials", type=int, default=100,
                    help="Optuna trials per slot (default 100)")
    args = ap.parse_args()
    N_TRIALS = args.n_trials

    # GPU detection
    use_gpu = not args.no_gpu
    if use_gpu:
        try:
            import subprocess
            r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                               capture_output=True, text=True, timeout=5)
            gpu_name = r.stdout.strip()
            print(f"  GPU detected: {gpu_name}")
        except Exception:
            print("  No GPU detected — falling back to CPU")
            use_gpu = False

    print("=" * 70)
    print("  CSIR Thunderstorm — v6 Slot Model Training")
    print("=" * 70)
    print(f"  GPU: {'ENABLED' if use_gpu else 'CPU only'}")
    print(f"  Optuna trials: {N_TRIALS} per slot")
    print(f"  Test years: {TEST_YEAR_MIN}+")

    # ── Load training data ───────────────────────────────────────────────────
    print(f"\n  Loading: {TRAINING_CSV}")
    if not TRAINING_CSV.exists():
        print(f"  ERROR: {TRAINING_CSV} not found!")
        print("  Make sure bengaluru_6hr_training_dataset.csv is in data/")
        return

    df = pd.read_csv(TRAINING_CSV, parse_dates=["date"])
    df["year"]  = df["date"].dt.year
    df["month"] = df["date"].dt.month
    df["doy"]   = df["date"].dt.dayofyear

    print(f"  Loaded: {len(df)} rows, years {df['year'].min()}–{df['year'].max()}")
    print(f"  Columns: {len(df.columns)}")
    print(f"  TS rate: {df['ts_label'].mean()*100:.1f}%")

    # ── Merge Himawari BT features ────────────────────────────────────────────
    bt_df      = load_himawari_bt(HIMAWARI_BT_CSV)
    has_himawari = bt_df is not None

    if has_himawari:
        # Merge on (date, slot)
        bt_df["date"] = pd.to_datetime(bt_df["date"])
        df = df.merge(bt_df, on=["date", "slot"], how="left")
        bt_feats = [c for c in bt_df.columns if c not in ("date", "slot")]
        print(f"  Himawari features merged: {bt_feats}")
        print(f"  Coverage: {df[bt_feats[0]].notna().mean()*100:.1f}% non-NaN")
        # Fill NaN BT with conservative "no storm" defaults
        for c in bt_feats:
            if "bt" in c.lower() or "min" in c.lower():
                df[c] = df[c].fillna(5.0)    # 5°C = warm, no deep convection
            elif "cold_pixels" in c.lower():
                df[c] = df[c].fillna(0)
            else:
                df[c] = df[c].fillna(0.0)

    # ── Add v6 derived features ───────────────────────────────────────────────
    print("\n  Adding v6 derived features...")
    df = add_v6_features(df)

    # ── Build feature columns ─────────────────────────────────────────────────
    EXCLUDE = {"date", "year", "slot", "slot_label", "ts_label",
               "CAPE_QC_FLAG", "CAPE_PYGRIB_CROSSCHECK"}
    all_feat_cols = [c for c in df.columns if c not in EXCLUDE
                     and df[c].dtype in (np.float64, np.int64, np.float32, np.int32, int, float)]

    # Drop near-zero-variance columns
    stds = df[all_feat_cols].std()
    low_var = stds[stds < 1e-6].index.tolist()
    if low_var:
        print(f"  Dropping {len(low_var)} low-variance cols: {low_var[:5]}...")
    feature_cols = [c for c in all_feat_cols if c not in low_var]

    print(f"  Total feature columns: {len(feature_cols)}")
    if has_himawari:
        hi_feats = [c for c in feature_cols if any(h in c for h in ("bt", "pixel", "himawari"))]
        print(f"  Himawari features active: {hi_feats}")

    # ── Train ─────────────────────────────────────────────────────────────────
    slots_to_train = [args.slot] if args.slot is not None else [0, 1, 2, 3]
    all_results = []
    saved_paths = []

    for slot_id in slots_to_train:
        print(f"\n{'='*70}")
        print(f"  SLOT {slot_id} — {SLOT_NAMES[slot_id]}")
        print(f"{'='*70}")
        result = train_slot(slot_id, df, feature_cols, use_gpu, has_himawari)
        if not result:
            continue
        path = save_artifact(result, slot_id, has_himawari)
        saved_paths.append(path)
        row = {
            "slot": slot_id, "window": SLOT_NAMES[slot_id],
            "cv_auroc": result["cv_auroc"], "wf_auroc": result["wf_auroc"],
            "threshold": result["threshold"],
            "has_himawari": has_himawari,
            "model_file": path.name,
        }
        if result.get("metrics"):
            row.update(result["metrics"])
        all_results.append(row)

    # ── Summary ────────────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  v6 TRAINING COMPLETE")
    print(f"{'='*70}")
    if all_results:
        results_df = pd.DataFrame(all_results)
        results_df.to_csv(RESULTS / "v6_evaluation.csv", index=False)
        print("\n  Per-slot results:")
        print(results_df[["slot", "window", "cv_auroc", "wf_auroc",
                           "AUROC", "POD", "FAR", "CSI", "HSS", "threshold"]].to_string(index=False))

    print("\n  Files saved:")
    for p in saved_paths:
        print(f"    {p}")

    print("\n" + "="*70)
    print("  NEXT STEP: Run resave_models.py to ensure cross-version compatibility")
    print("  Then update SLOT_MODEL_PRIORITY in forecast_action.py and")
    print("  compute_realtime_shap.py to include v6 models.")
    print("="*70)

    # ── Colab helper: update model priority ───────────────────────────────────
    print("\n  Update SLOT_MODEL_PRIORITY to include v6 (add to top of each slot list):")
    suffix = "himawari" if has_himawari else "temporal"
    for slot_id in slots_to_train:
        print(f"    {slot_id}: [\"nowcast_slot{slot_id}_xgb_v6_{suffix}.pkl\", ...]")


if __name__ == "__main__":
    main()
