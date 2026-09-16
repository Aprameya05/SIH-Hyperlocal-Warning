"""
run_radar_scheduler.py
======================
Runs fetch_himawari_realtime.py every 10 minutes
and fetch_imerg_realtime.py every 30 minutes in the background.

Usage:
    python run_radar_scheduler.py

Or on Windows Task Scheduler / Linux cron:
    Windows: schtasks /create /tn "HimawariFetch" /tr "python fetch_himawari_realtime.py"
             /sc minute /mo 10 /st 00:00
    Linux:   */10 * * * * cd /path/to/csir-repo && python fetch_himawari_realtime.py
             */30 * * * * cd /path/to/csir-repo && python fetch_imerg_realtime.py
"""

import time, subprocess, sys, logging, datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("scheduler")

HIMAWARI_INTERVAL_S = 600   # 10 minutes
IMERG_INTERVAL_S    = 1800  # 30 minutes

python = sys.executable
last_himawari = 0
last_imerg    = 0


def run(script: str):
    log.info(f"→ {script}")
    result = subprocess.run(
        [python, script],
        capture_output=False,
        timeout=300,
    )
    if result.returncode != 0:
        log.warning(f"  {script} exited with code {result.returncode}")
    return result.returncode


if __name__ == "__main__":
    log.info("Radar scheduler started. Ctrl+C to stop.")
    log.info(f"Himawari: every {HIMAWARI_INTERVAL_S//60} min")
    log.info(f"IMERG   : every {IMERG_INTERVAL_S//60} min")

    # Run immediately on start
    run("fetch_himawari_realtime.py")
    last_himawari = time.time()
    run("fetch_imerg_realtime.py")
    last_imerg = time.time()

    while True:
        try:
            now = time.time()
            if now - last_himawari >= HIMAWARI_INTERVAL_S:
                run("fetch_himawari_realtime.py")
                last_himawari = time.time()
            if now - last_imerg >= IMERG_INTERVAL_S:
                run("fetch_imerg_realtime.py")
                last_imerg = time.time()
            time.sleep(30)
        except KeyboardInterrupt:
            log.info("Scheduler stopped.")
            break
        except Exception as e:
            log.error(f"Scheduler error: {e}")
            time.sleep(60)