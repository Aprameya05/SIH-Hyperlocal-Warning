#!/usr/bin/env python3
"""Phase 0.4.19.1 focused tests -- relocating the large historical-GFS raw
file storage to a custom --raw-root (e.g. a D: drive) without breaking
acquisition, provenance, or verification. These tests never hit the
network and never delete anything; they use tmp_path dirs standing in for
"C:" and "D:" equivalents."""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import acquire_phase_0_4_19_batch as acq  # noqa: E402
import verify_phase_0_4_19_batch as ver  # noqa: E402


def _skip_if_manifest_missing():
    if not acq.MANIFEST_CSV.exists():
        pytest.skip("candidate manifest CSV not present in this environment")


def test_load_batch_targets_honors_a_custom_raw_root(tmp_path):
    _skip_if_manifest_missing()
    custom_root = tmp_path / "D_drive_equivalent" / "raw"
    targets = acq.load_batch_targets(raw_root=custom_root)
    assert len(targets) > 0
    for t in targets:
        assert t.dest.parent == custom_root
        assert t.dest == custom_root / t.filename


def test_default_raw_root_is_unchanged_when_not_overridden():
    _skip_if_manifest_missing()
    targets = acq.load_batch_targets()
    for t in targets:
        assert t.dest.parent == acq.RAW_DIR


def test_cli_raw_root_flag_is_threaded_through_to_targets(monkeypatch, tmp_path):
    _skip_if_manifest_missing()
    custom_root = tmp_path / "relocated_raw"
    captured = {}

    real_load = acq.load_batch_targets

    def _spy(manifest_csv=acq.MANIFEST_CSV, raw_root=acq.RAW_DIR):
        captured["raw_root"] = Path(raw_root)
        return real_load(manifest_csv, raw_root)

    monkeypatch.setattr(acq, "load_batch_targets", _spy)
    monkeypatch.setattr(sys, "argv", [
        "acquire_phase_0_4_19_batch.py", "--dry-run", "--raw-root", str(custom_root),
    ])
    acq.main()
    assert captured["raw_root"] == custom_root


def test_provenance_manifest_records_only_bare_filenames_not_absolute_paths(tmp_path):
    # This is exactly what makes relocation safe: the provenance manifest
    # must never embed a C:\ or D:\ absolute path, so it stays correct
    # regardless of where raw_root points at read time.
    target = acq.DownloadTarget(
        event_group_key="X|2020-07-15|1", label="POSITIVE", partition="train",
        season="monsoon", target_ist_date="2020-07-15", cycle="2020071500",
        lead="f003", filename="gfs.0p25.2020071500.f003.grib2",
        url="https://example.invalid/x", dest=tmp_path / "raw" / "gfs.0p25.2020071500.f003.grib2",
    )
    result = acq.DownloadResult(
        target=target, status="DOWNLOADED", final_size_bytes=12345, sha256="abc123",
    )
    provenance_path = tmp_path / "manifest.json"
    acq.write_provenance([result], provenance_path=provenance_path)
    entries = json.loads(provenance_path.read_text())
    assert len(entries) == 1
    assert entries[0]["file_name"] == "gfs.0p25.2020071500.f003.grib2"
    assert "dest" not in entries[0]
    assert "path" not in entries[0]
    for v in entries[0].values():
        if isinstance(v, str):
            assert "D:\\" not in v and "C:\\" not in v and str(tmp_path) not in v


def test_custom_provenance_manifest_path_round_trips(tmp_path):
    target = acq.DownloadTarget(
        event_group_key="X|2020-07-15|1", label="POSITIVE", partition="train",
        season="monsoon", target_ist_date="2020-07-15", cycle="2020071500",
        lead="f003", filename="gfs.0p25.2020071500.f003.grib2",
        url="https://example.invalid/x", dest=tmp_path / "raw" / "gfs.0p25.2020071500.f003.grib2",
    )
    result = acq.DownloadResult(target=target, status="DOWNLOADED", final_size_bytes=1, sha256="deadbeef")
    custom_manifest = tmp_path / "custom_location" / "manifest.json"
    acq.write_provenance([result], provenance_path=custom_manifest)
    assert custom_manifest.exists()
    loaded = acq.load_existing_provenance(custom_manifest)
    assert "gfs.0p25.2020071500.f003.grib2" in loaded


def test_verifier_reads_from_a_custom_raw_root(tmp_path, monkeypatch):
    _skip_if_manifest_missing()
    custom_root = tmp_path / "relocated_raw"
    custom_root.mkdir(parents=True)
    captured = {}

    def _spy_verify_one_file(path, expected, provenance):
        captured.setdefault("paths_checked", []).append(path)
        return {"filename": path.name, "failures": ["file does not exist (not yet acquired)"],
                "warnings": [], "status": "FILE_NOT_FOUND"}

    monkeypatch.setattr(ver, "verify_one_file", _spy_verify_one_file)
    monkeypatch.setattr(sys, "argv", [
        "verify_phase_0_4_19_batch.py", "--raw-root", str(custom_root),
    ])
    with pytest.raises(SystemExit):
        ver.main()
    assert captured["paths_checked"], "verifier should have attempted to check at least one file"
    for p in captured["paths_checked"]:
        assert p.parent == custom_root


def test_verifier_raw_dir_alias_still_works(tmp_path, monkeypatch):
    # --raw-dir was the original Phase 0.4.19 flag name; Phase 0.4.19.1
    # renames the canonical flag to --raw-root but must not break anyone
    # who already has --raw-dir in a saved command.
    _skip_if_manifest_missing()
    custom_root = tmp_path / "relocated_via_alias"
    custom_root.mkdir(parents=True)
    captured = {}

    def _spy_verify_one_file(path, expected, provenance):
        captured.setdefault("paths_checked", []).append(path)
        return {"filename": path.name, "failures": ["file does not exist (not yet acquired)"],
                "warnings": [], "status": "FILE_NOT_FOUND"}

    monkeypatch.setattr(ver, "verify_one_file", _spy_verify_one_file)
    monkeypatch.setattr(sys, "argv", [
        "verify_phase_0_4_19_batch.py", "--raw-dir", str(custom_root),
    ])
    with pytest.raises(SystemExit):
        ver.main()
    for p in captured["paths_checked"]:
        assert p.parent == custom_root


# -------------------- .part file resumability --------------------

def test_part_filename_convention_matches_what_was_observed_on_disk():
    # The real failure report showed gfs.0p25.2019100206.f003.grib2.part
    # sitting next to the final-name convention. Confirm the downloader's
    # own tmp-path naming produces exactly that.
    target = acq.DownloadTarget(
        event_group_key="X|2019-10-02|2", label="POSITIVE", partition="train",
        season="post-monsoon", target_ist_date="2019-10-02", cycle="2019100206",
        lead="f003", filename="gfs.0p25.2019100206.f003.grib2",
        url="https://data.gdex.ucar.edu/d084001/2019/20191002/gfs.0p25.2019100206.f003.grib2",
        dest=Path("/some/raw/dir/gfs.0p25.2019100206.f003.grib2"),
    )
    tmp_path = target.dest.with_suffix(target.dest.suffix + ".part")
    assert tmp_path.name == "gfs.0p25.2019100206.f003.grib2.part"


def test_resume_is_only_attempted_when_server_advertises_accept_ranges(tmp_path, monkeypatch):
    # A non-empty .part file must NOT be blindly Range-resumed -- the
    # stream_download() logic must check head_info["accept_ranges_bytes"]
    # first, and fall back to a clean restart (mode="wb", resume_from
    # reset to 0) when the server doesn't advertise range support. This
    # is exactly the "do not assume resumable" check the user asked for.
    dest = tmp_path / "gfs.0p25.2019100206.f003.grib2"
    tmp_file = dest.with_suffix(dest.suffix + ".part")
    tmp_file.write_bytes(b"x" * 1000)  # simulates the real 136MB partial file, scaled down

    captured_modes = {}

    class _FakeResponse:
        status = 200
        def read(self, n):
            if captured_modes.get("served"):
                return b""
            captured_modes["served"] = True
            return b"y" * 10
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=60):
        captured_modes["range_header_sent"] = req.headers.get("Range")
        return _FakeResponse()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    # Case 1: server does NOT advertise Accept-Ranges -- must restart clean
    head_info_no_ranges = {"content_length": 1010, "accept_ranges_bytes": False}
    result = acq.stream_download(
        "https://example.invalid/f", tmp_file, resume_from=1000,
        size_ceiling=acq.DEFAULT_SIZE_CEILING_BYTES, allow_large=False,
        head_info=head_info_no_ranges,
    )
    assert result["resumed"] is False
    assert captured_modes.get("range_header_sent") is None, (
        "must not send a Range header when the server hasn't confirmed Accept-Ranges support"
    )

    # Case 2: server DOES advertise Accept-Ranges -- resume is attempted
    captured_modes.clear()
    tmp_file.write_bytes(b"x" * 1000)
    head_info_ranges = {"content_length": 1010, "accept_ranges_bytes": True}
    result2 = acq.stream_download(
        "https://example.invalid/f", tmp_file, resume_from=1000,
        size_ceiling=acq.DEFAULT_SIZE_CEILING_BYTES, allow_large=False,
        head_info=head_info_ranges,
    )
    assert result2["resumed"] is True
    assert captured_modes.get("range_header_sent") == "bytes=1000-"


def test_already_verified_is_false_for_a_dot_part_file_itself(tmp_path):
    # already_verified() must only ever consider the FINAL filename, never
    # a .part file -- a .part is never "already verified," regardless of
    # its size.
    dest = tmp_path / "gfs.0p25.2019100206.f003.grib2"
    part = dest.with_suffix(dest.suffix + ".part")
    part.write_bytes(b"partial data only")
    target = acq.DownloadTarget(
        event_group_key="X|2019-10-02|2", label="POSITIVE", partition="train",
        season="post-monsoon", target_ist_date="2019-10-02", cycle="2019100206",
        lead="f003", filename=dest.name, url="https://example.invalid/x", dest=dest,
    )
    # dest itself does not exist yet (only the .part does)
    assert acq.already_verified(target, {}) is False
