"""
resave_models.py
================
Version-agnostic model resave script for CSIR Thunderstorm Nowcasting System.

PROBLEM: Models saved with XGBoost 2.x / sklearn 1.7.x break when loaded on
machines with different library versions (Atul's machine runs XGBoost 2.1.3 /
sklearn 1.5.2, training was done with 1.7.2).

SOLUTION: Extract the XGBoost Booster from each artifact, save it in the
universal JSON/UBJ format (Booster.save_model), then repack the full artifact
dict using joblib. The Booster JSON format is stable across XGBoost versions.

Run this on Aprameya's machine or Colab (where the current sklearn/xgboost is)
then push the updated .pkl files. Atul can then load them without version issues.

Usage:
    python resave_models.py               # resave all known production models
    python resave_models.py --check-only  # just verify each model loads cleanly
    python resave_models.py --slot 2      # resave only one slot

Models targeted (production set per handoff doc, 2026-08-08):
    Slot 0  : nowcast_slot0_xgb_v4_ensemble.pkl     (CV AUROC 0.8484)
    Slot 1  : nowcast_slot1_xgb_v5_temporal.pkl      (v5 temporal, 30 lag features)
    Slot 2  : nowcast_slot2_xgb_v5_temporal.pkl      (v5 temporal, 30 lag features)
    Slot 3  : nowcast_slot3_xgb_v5_temporal.pkl      (v5 temporal, 30 lag features)
    Himawari: himawari_correction_model.pkl           (BT correction, CV AUROC 0.9141)

Fallback chain (if production model absent):
    v3_calibrated → v2_calibrated → v4_calibrated
"""

import argparse
import json
import os
import sys
from pathlib import Path

import joblib
import numpy as np

MODELS = Path("models")
BOOSTER_DIR = MODELS / "boosters"
BOOSTER_DIR.mkdir(parents=True, exist_ok=True)

# Production model priority per slot
SLOT_CANDIDATES = {
    0: [
        "nowcast_slot0_xgb_v4_ensemble.pkl",
        "nowcast_slot0_xgb_v4_calibrated.pkl",
        "nowcast_slot0_xgb_v3_calibrated.pkl",
        "nowcast_slot0_xgb_v2_calibrated.pkl",
    ],
    1: [
        "nowcast_slot1_xgb_v5_temporal.pkl",
        "nowcast_slot1_xgb_v4_calibrated.pkl",
        "nowcast_slot1_xgb_v3_calibrated.pkl",
        "nowcast_slot1_xgb_v2_calibrated.pkl",
    ],
    2: [
        "nowcast_slot2_xgb_v5_temporal.pkl",
        "nowcast_slot2_xgb_v4_calibrated.pkl",
        "nowcast_slot2_xgb_v3_calibrated.pkl",
        "nowcast_slot2_xgb_v2_calibrated.pkl",
    ],
    3: [
        "nowcast_slot3_xgb_v5_temporal.pkl",
        "nowcast_slot3_xgb_v4_calibrated.pkl",
        "nowcast_slot3_xgb_v3_calibrated.pkl",
        "nowcast_slot3_xgb_v2_calibrated.pkl",
    ],
}

EXTRA_MODELS = ["himawari_correction_model.pkl"]


def get_xgb_booster(model_obj):
    """Extract XGBoost Booster from various wrapper types."""
    # Direct Booster
    try:
        import xgboost as xgb
        if isinstance(model_obj, xgb.Booster):
            return model_obj
        # XGBClassifier / XGBRegressor
        if hasattr(model_obj, "get_booster"):
            return model_obj.get_booster()
    except ImportError:
        pass

    # sklearn Pipeline (last step)
    if hasattr(model_obj, "steps"):
        last = model_obj.steps[-1][1]
        if hasattr(last, "get_booster"):
            return last.get_booster()
        return None

    return None


def resave_artifact(pkl_path: Path, check_only: bool = False) -> bool:
    """Load artifact, resave Booster in UBJ format, repack with joblib."""
    print(f"\n  {'Checking' if check_only else 'Resaving'}: {pkl_path.name}")

    try:
        artifact = joblib.load(pkl_path)
    except Exception as e:
        print(f"    ✗ LOAD FAILED: {e}")
        return False

    # Unpack model object from dict or use directly
    # Artifact schemas seen in this repo:
    #   v3/v4 calibrated : {'model': ..., 'feature_cols': ..., 'threshold': ..., 'calibrator': ...}
    #   v4 ensemble / v5 : {'calibrated': ..., 'features': ..., 'slot': ..., 'auroc': ...}
    if isinstance(artifact, dict):
        model_obj = artifact.get("model") or artifact.get("calibrated")
        if model_obj is None:
            print(f"    ✗ No model key in artifact. Keys: {list(artifact.keys())}")
            return False
        # Normalise to unified schema so forecast_action.py can load either format
        if "model" not in artifact and "calibrated" in artifact:
            artifact["model"]        = artifact["calibrated"]
        if "feature_cols" not in artifact and "features" in artifact:
            artifact["feature_cols"] = artifact["features"]
        if "threshold" not in artifact:
            artifact["threshold"] = None   # will be overridden by THRESHOLDS dict at runtime
    else:
        model_obj = artifact
        artifact = {"model": model_obj, "feature_cols": [], "threshold": None}

    booster = get_xgb_booster(model_obj)
    if booster is None:
        print(f"    ✓ No XGBoost Booster found (may be sklearn-only model) — skipping UBJ resave")
        if not check_only:
            joblib.dump(artifact, pkl_path)
            print(f"    ✓ Repacked with joblib (no Booster resave needed)")
        return True

    # Get XGBoost version
    try:
        import xgboost as xgb
        xgb_ver = xgb.__version__
    except Exception:
        xgb_ver = "unknown"

    # Save Booster in UBJ format (version-stable binary JSON)
    ubj_path = BOOSTER_DIR / (pkl_path.stem + ".ubj")
    if not check_only:
        try:
            booster.save_model(str(ubj_path))
            print(f"    ✓ Booster saved → {ubj_path.name} ({ubj_path.stat().st_size / 1024:.0f} KB)")
        except Exception as e:
            print(f"    ✗ Booster.save_model failed: {e}")
            return False

        # Stamp the artifact with version info
        artifact["xgb_version_trained"] = xgb_ver
        artifact["booster_ubj_path"] = str(ubj_path)
        artifact["resaved_at"] = __import__("datetime").datetime.now().isoformat()

        joblib.dump(artifact, pkl_path)
        print(f"    ✓ Artifact repacked with joblib → {pkl_path.name}")

    # Verify round-trip
    try:
        verify = joblib.load(pkl_path)
        v_model = verify.get("model") if isinstance(verify, dict) else verify
        v_booster = get_xgb_booster(v_model)
        if v_booster is None:
            raise RuntimeError("Could not re-extract Booster after resave")

        feature_names = v_booster.feature_names
        n_trees = v_booster.num_boosted_rounds()
        feature_cols = (artifact.get("feature_cols") or
                        (verify.get("feature_cols") if isinstance(verify, dict) else None))
        thresh = artifact.get("threshold", verify.get("threshold") if isinstance(verify, dict) else None)

        print(f"    ✓ Verified: {n_trees} trees, {len(feature_cols or [])} features, threshold={thresh}")
    except Exception as e:
        print(f"    ✗ Verification failed: {e}")
        return False

    return True


def find_production_model(slot: int) -> Path | None:
    for candidate in SLOT_CANDIDATES[slot]:
        p = MODELS / candidate
        if p.exists():
            return p
    return None


def main():
    ap = argparse.ArgumentParser(description="Resave XGBoost models in version-stable format")
    ap.add_argument("--check-only", action="store_true", help="Only verify loads, do not resave")
    ap.add_argument("--slot", type=int, choices=[0, 1, 2, 3], default=None, help="Resave only this slot")
    ap.add_argument("--all", action="store_true", help="Resave ALL pkl files in models/ (including non-production)")
    args = ap.parse_args()

    print("=" * 65)
    print("  CSIR Thunderstorm — Model Resave Utility")
    print("=" * 65)

    # Report library versions
    try:
        import xgboost as xgb
        print(f"  XGBoost   : {xgb.__version__}")
    except ImportError:
        print("  XGBoost   : NOT INSTALLED — install before running")
        sys.exit(1)
    try:
        import sklearn
        print(f"  sklearn   : {sklearn.__version__}")
    except ImportError:
        print("  sklearn   : NOT INSTALLED")
    print(f"  joblib    : {joblib.__version__}")
    print()

    targets = []

    if args.all:
        targets = list(MODELS.glob("*.pkl"))
    elif args.slot is not None:
        p = find_production_model(args.slot)
        if p:
            targets = [p]
        else:
            print(f"  ✗ No model found for Slot {args.slot}")
            sys.exit(1)
    else:
        for slot in range(4):
            p = find_production_model(slot)
            if p:
                targets.append(p)
                print(f"  Slot {slot}: using {p.name}")
            else:
                print(f"  Slot {slot}: ✗ NO MODEL FOUND — tried: {SLOT_CANDIDATES[slot]}")
        for name in EXTRA_MODELS:
            p = MODELS / name
            if p.exists():
                targets.append(p)
                print(f"  Extra   : {name}")

    print(f"\n  Total targets: {len(targets)}")
    print("=" * 65)

    ok = 0
    fail = 0
    for p in targets:
        success = resave_artifact(p, check_only=args.check_only)
        if success:
            ok += 1
        else:
            fail += 1

    print("\n" + "=" * 65)
    print(f"  Done: {ok} OK, {fail} FAILED")
    if fail > 0:
        print("  ✗ Some models failed — check errors above before pushing")
        sys.exit(1)
    else:
        print("  ✓ All models resaved and verified — safe to push")
    print("=" * 65)


if __name__ == "__main__":
    main()
