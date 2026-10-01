"""Re-runnable check: both acquisition scripts fail safely without
credentials/real input, rather than fabricating data. Run from repo root."""
import subprocess, sys

r1 = subprocess.run([sys.executable, "scripts/acquire_imdaa.py", "--start", "2024-01-01", "--end", "2024-01-07"],
                     capture_output=True, text=True)
assert r1.returncode == 1, f"expected exit 1, got {r1.returncode}"
assert "NCMRWF_USER" in r1.stdout
print("OK: acquire_imdaa.py fails safely without credentials")

r2 = subprocess.run([sys.executable, "scripts/parse_imdaa.py", "--input", "raw/imdaa/nonexistent.nc", "--output", "processed/imdaa/out.json"],
                     capture_output=True, text=True)
assert r2.returncode == 1, f"expected exit 1, got {r2.returncode}"
assert "does not exist" in r2.stdout
print("OK: parse_imdaa.py fails safely without a real input file")
