"""Test configuration: make the repository root importable so tests can import the operational modules that live at the
repository root (forecast_action, gfs_row_select, ...) and the `backend` / `pipelines` packages, wherever pytest is started."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
