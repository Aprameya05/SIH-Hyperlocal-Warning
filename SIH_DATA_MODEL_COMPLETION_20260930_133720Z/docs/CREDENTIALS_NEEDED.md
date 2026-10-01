# Credentials / Access Needed to Proceed Further

1. NCMRWF (IMDAA reanalysis)
   - Register at the NCMRWF data service (outside this sandbox; this sandbox cannot reach ncmrwf.gov.in or related hosts — proxy returns 403).
   - Once registered, confirm the actual current product name, file format, variable list and resolution offered, and share that spec so the fetch/parse script can be written against real details rather than assumed ones.

2. MOSDAC (INSAT-3D/3DR)
   - Register at https://mosdac.gov.in
   - Provide MOSDAC_USER / MOSDAC_PASS (env vars or GitHub Secrets)
   - backend/fetch_insat3d.py already exists and is ready to use these, but its endpoint (MOSDAC_BASE) should be reconfirmed against MOSDAC's current API before first use -- it has changed before.

3. A100 GPU access
   - This cloud sandbox has no GPU, no nvidia-smi, and no PyTorch installed. Whatever A100 you referenced is not reachable from this session.
   - Tell me where it actually runs (your own machine / a cloud instance you control) and I will write and hand over a training script tested against the real label file (data/bengaluru_6hr_training_dataset_cb_ff.csv) for you to run there.

4. Optional: data/catchment_characteristics_indofloods.csv
   - If available, moves the flash-flood label off its current RF-based fallback and onto the catchment-refined path dev/Fetch cb ff labels.py already supports.
