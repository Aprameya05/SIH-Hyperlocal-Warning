# Contributing

- Branch from `main`; open a PR; do not push to `main` or force-push.
- Run `make test` and `make gate` before opening a PR; add a test for every behaviour change.
- Never fabricate data, probabilities, lead times or coverage. Missing is `null`/`unavailable`, never zero.
- Label provenance: observation / NWP input / derived / trained prediction / calibrated probability / heuristic / official / simulation.
- Do not weaken fail-closed alert policy or security checks to make CI pass.
- Never commit secrets or subscriber data; use `.env.example` placeholders.
- Keep Python and JS twin logic in parity (`tests/test_location_parity.py`).
- Layout: see `docs/architecture/REPOSITORY_STRUCTURE.md`.
