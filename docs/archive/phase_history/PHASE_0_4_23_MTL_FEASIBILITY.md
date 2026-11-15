# Phase 0.4.23 — Three-Hazard MTL Feasibility (Design Only, No Training)

Evaluates four candidate MTL configurations against the actual data states established in Phase 0.4.22 and refined in this phase. No model was trained or configured to run; this is a design comparison only.

## Option A — True three-head MTL: TS + CB + observed FF

- **Label quality**: TS clean/observed; CB has an unresolved daily/6h resolution mismatch (Part 3 of the main remediation plan); observed FF has zero usable rows today (no negative label — Part 4/5 of this phase's work defines a contract but does not yet produce a dataset).
- **Dataset size**: TS ~20-300 event groups depending on acquisition phase; CB technically large (49,613 positive cell-days) but at the wrong resolution; observed FF currently 0 trainable rows.
- **Temporal alignment**: the three hazards' labels are not co-registered to the same (cell, 6h-slot) grid today — CB is daily, FF-observed has no slots at all (date-level flood events), TS is the only one natively 6h-sloted.
- **Spatial alignment**: TS is 1 cell (VOBL); CB spans 382-992 cells; FF-observed spans 61-75 cells. The three hazards do not share a common spatial footprint — a shared-backbone MTL model would need to handle wildly different per-task spatial coverage, with TS supervision available at exactly one of the ~992 cells.
- **Loss masking requirements**: heavy — most (cell, slot) training examples would have a label for at most one of the three heads, requiring per-task masking for every batch.
- **Scientific risk**: very high. Training a shared backbone where two of three heads (CB, FF) have unresolved label-quality problems risks the shared representation absorbing those heads' noise and degrading the one head (TS) that is actually clean.
- **What could legitimately be claimed**: nothing defensible today. **Not recommended now.**

## Option B — TS + CB, FF excluded until labels mature

- **Label quality**: TS clean; CB still has the unresolved resolution mismatch — excluding FF does not fix CB's own problem.
- **Dataset size**: same TS constraint as Option A; CB still large but mis-resolved.
- **Temporal/spatial alignment**: better than Option A (one fewer unresolved axis) but CB's daily label still cannot honestly supervise a 2-6h-slot output head without the Part 3 remediation being done first.
- **Loss masking**: still required (TS at 1 cell, CB pan-India).
- **Scientific risk**: moderate — avoids compounding FF's zero-negative problem, but still trains on a CB label known to be resolution-mismatched to the target.
- **What could legitimately be claimed**: only "TS+CB shared-backbone experiment, CB component scientifically limited to daily resolution" — and only once TS itself clears the XGBoost-baseline-scale gate from Phase 0.4.22. **Premature until Part 3's CB remediation is actually implemented (not just designed, as it is here).**

## Option C — TS + CB + FF-proxy, explicitly experimental

- **Label quality**: TS clean; CB mis-resolved; FF-proxy internally consistent but explicitly non-observed (every row self-labeled `PROXY_NOT_OBSERVED`).
- **Dataset size**: all three technically have non-trivial row counts (TS smallest, CB and FF-proxy both in the tens-of-thousands of cell-days), but only if resolution/proxy caveats are tolerated per-task.
- **Temporal/spatial alignment**: same CB mismatch as Option B; FF-proxy adds a third daily-resolution head, compounding rather than resolving the resolution problem across two of three heads.
- **Loss masking**: required, same as above.
- **Scientific risk**: moderate-to-high if ever presented without the proxy caveat traveling with every downstream claim (the exact failure pattern already flagged repeatedly in the Phase 0.4.21/A-to-Z audits for other components) — explicitly manageable if every FF output is labeled "proxy" everywhere it appears, never as "flash flood probability" unqualified.
- **What could legitimately be claimed**: "an experimental three-head shared backbone, where only TS is observed/correctly-resolution-matched; CB and FF are daily-resolution proxies included for representation-sharing research, not for independent per-hazard claims." This is the only option among A-D that uses all three hazards without requiring new label-engineering work first, provided the caveats are enforced everywhere in UI/docs. **Usable today only as a clearly-labeled research configuration, never as a production three-hazard model.**

## Option D — Shared representation with hazard-specific datasets and missing-task supervision

- This is the same underlying architecture question as Options A-C (a shared backbone with per-task heads and masked losses) — "missing-task supervision" describes the masking mechanism already implied by A-C, not a distinct data configuration. Evaluated as a *training strategy* rather than a *dataset choice*: it is the correct mechanism regardless of which hazards/labels are chosen, since none of the three hazards currently have full co-registered coverage across all cells and slots. It does not by itself resolve CB's resolution mismatch or FF-observed's missing negatives — it only lets the shared backbone train without requiring every example to have all three labels.

## Recommendation

**MTL_RECOMMENDED_OPTION = C**, implemented with Option D's masked-loss training mechanism, and only once TS alone has cleared the Phase 0.4.22 XGBoost-baseline-scale gate (Part 9 target: 300 event groups). This is chosen on evidence, not presentation value: Option A is blocked by observed-FF having zero usable rows; Option B still trains on a known-mismatched CB label with no corresponding gain over Option C; Option C is the only configuration usable today without first completing new label-engineering work (CB resolution fix, FF negative-label construction), provided every CB and FF output is labeled with its actual provenance (daily-resolution; rainfall-proxy, not observed) everywhere it is surfaced. This recommendation does not endorse training now — it answers "which configuration would be least scientifically compromised if and when training begins," consistent with the A100_GATE remaining NOT_READY (see Part 7 below / the main remediation plan).
