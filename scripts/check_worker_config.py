#!/usr/bin/env python3
"""Pre-deployment check for the Cloudflare Worker (worker/).

Static checks always run. With --remote it also lists the Worker's deployed secrets (needs CLOUDFLARE_API_TOKEN and
network) and refuses unless TOKEN_SECRET and ADMIN_KEY exist: the Worker fails closed (503) without TOKEN_SECRET.
Secret VALUES are never read; this script only sees secret names.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

WORKER = Path(__file__).resolve().parents[1] / "worker"
REQUIRED_SECRETS = ("TOKEN_SECRET", "ADMIN_KEY")
SECRET_LIKE = re.compile(r"(secret|token|key|password)", re.I)


def static_problems() -> list[str]:
    problems = []
    toml = (WORKER / "wrangler.toml").read_text(encoding="utf-8")
    if 'binding = "SUBSCRIBERS"' not in toml:
        problems.append("wrangler.toml: KV binding SUBSCRIBERS missing")
    vars_block = re.search(r"\[vars\](.*?)(?:\n\[|\Z)", toml, re.S)
    for line in (vars_block.group(1).splitlines() if vars_block else []):
        line = line.split("#")[0].strip()
        if "=" in line and SECRET_LIKE.search(line.split("=")[0]):
            problems.append(f"wrangler.toml [vars] must not hold secrets: {line.split('=')[0].strip()}")
    src = (WORKER / "index.js").read_text(encoding="utf-8")
    if re.search(r"TOKEN_SECRET\s*\|\|", src):
        problems.append("index.js: TOKEN_SECRET must not have a default value (predictable unsubscribe tokens)")
    for name in REQUIRED_SECRETS:
        if f"env.{name}" not in src:
            problems.append(f"index.js does not use env.{name}")
    return problems


def remote_problems() -> list[str]:
    try:
        out = subprocess.run(["npx", "--yes", "wrangler@3.87.0", "secret", "list", "--format", "json"], cwd=WORKER,
                             capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as e:
        return [f"could not run wrangler: {e}"]
    if out.returncode != 0:
        return [f"wrangler secret list failed (is CLOUDFLARE_API_TOKEN set?): {out.stderr.strip()[:300]}"]
    have = {x.get("name") for x in json.loads(out.stdout or "[]")}
    return [f"deployed Worker is missing secret {n}" for n in REQUIRED_SECRETS if n not in have]


def main(argv) -> int:
    problems = static_problems() + (remote_problems() if "--remote" in argv else [])
    if problems:
        print("WORKER NOT READY:", *problems, sep="\n  - ")
        return 1
    print("OK: worker config passes" + (" (including deployed secrets)" if "--remote" in argv else " (static checks only; run with --remote before deploying)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
