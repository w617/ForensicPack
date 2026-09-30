import errno
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

import archivers
import cli
import engine
import verification_space
from history import HistoryStore
from models import CancellationToken, JobCancelled, JobSkipped
from process_runner import run_process
from test_hardening_core import callbacks, config_for
from verification_center import export_records, reverify_run, verify_record_bundle


@pytest.fixture
def evidence(tmp_path):
    source = tmp_path / "source"
    case = source / "case"
    case.mkdir(parents=True)
    (case / "evidence.txt").write_text("original evidence", encoding="utf-8")
    return source, case, tmp_path / "output"


def package(evidence, **options):
    source, _, output = evidence
    return engine.run_session(config_for(source, output, **options), callbacks(), CancellationToken())[0]


def require_7zip():
    executable = archivers.find_7zip()
    if not executable:
        pytest.skip("Native 7-Zip is not installed")
    return Path(executable)


def test_7z_uses_only_inventory_with_literal_names(evidence, monkeypatch):
    executable = require_7zip()
    source, case, output = evidence
    (case / "excluded.txt").write_text("must not enter archive")
    (case / "brackets[1].txt").write_text("literal filename")
    (case / "-option.txt").write_text("literal dash")
    (case / "@literal.txt").write_text("literal at sign")
    original = engine.build_inventory

    def selection(*args, **kwargs):
        records, _, issues = original(*args, **kwargs)
        records = [r for r in records if r.path.name != "excluded.txt"]
        return records, sum(r.size for r in records), issues

    monkeypatch.setattr(engine, "build_inventory", selection)
    result = package(evidence, archive_fmt="7z", seven_zip_path=executable)
    assert result.status == "success", result.warnings
    assert result.archive_member_count == 4
    # The content verifier also rejects any unexpected archived member.
    assert result.content_verify == "PASS"


def test_7z_does_not_include_files_added_after_inventory(evidence, monkeypatch):
    executable = require_7zip()
    _, case, _ = evidence
    original = engine.create_archive

    def late_file(*args, **kwargs):
        (case / "late.txt").write_text("late evidence")
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, "create_archive", late_file)
    result = package(evidence, archive_fmt="7z", seven_zip_path=executable, delete_source=True)
    assert result.content_verify == "PASS"
    assert result.archive_member_count == 1
    assert result.status == "warning"
    assert case.exists()
    assert "SOURCE RETAINED" in result.verify


@pytest.mark.skipif(os.name == "nt", reason="Win32 rejects newline filenames")
def test_7z_rejects_unrepresentable_list_filename(evidence):
    executable = require_7zip()
    _, case, _ = evidence
    (case / "line\nbreak.txt").write_text("evidence")
    result = package(evidence, archive_fmt="7z", seven_zip_path=executable)
    assert result.status == "failed"
    assert "cannot represent" in " ".join(result.warnings)


def test_selected_scratch_drive_used_and_cleaned(evidence, tmp_path, monkeypatch):
    executable = require_7zip()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    observed = []
    from content_verification import run_process as real

    def capture(command, *args, **kwargs):
        observed.extend(arg[2:] for arg in command if arg.startswith("-o"))
        return real(command, *args, **kwargs)

    monkeypatch.setattr("content_verification.run_process", capture)
    result = package(evidence, archive_fmt="7z", seven_zip_path=executable, verify_temp_dir=scratch)
    assert result.status == "success", result.warnings
    assert observed and Path(observed[0]).parent == scratch
    assert not list(scratch.iterdir())


def test_insufficient_scratch_fails_before_archive_creation(evidence, tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    usage = shutil.disk_usage(scratch)
    monkeypatch.setattr(verification_space.shutil, "disk_usage", lambda _: type(usage)(usage.total, usage.total, 0))
    with pytest.raises(ValueError, match="verification space"):
        package(evidence, archive_fmt="7z", verify_temp_dir=scratch)
    assert not list(evidence[2].glob("*.7z*"))
    assert evidence[1].exists()


def test_scratch_reservations_prevent_parallel_overcommit(tmp_path, monkeypatch):
    usage = shutil.disk_usage(tmp_path)
    required = verification_space.required_scratch_bytes(1024)
    monkeypatch.setattr(verification_space.shutil, "disk_usage", lambda _: type(usage)(required+10, 0, required+10))
    with verification_space.reserve_scratch(tmp_path, 1024):
        with pytest.raises(ValueError, match="verification space"):
            with verification_space.reserve_scratch(tmp_path, 1024):
                pytest.fail("overcommitted scratch")
    assert not verification_space._reserved


@pytest.mark.parametrize("action,error", [("cancel", JobCancelled), ("skip", JobSkipped)])
def test_quiet_process_is_interruptible_and_reaped(action, error, monkeypatch):
    token = CancellationToken()
    processes = []
    real = subprocess.Popen

    def capture(*args, **kwargs):
        process = real(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("process_runner.subprocess.Popen", capture)
    timer = threading.Timer(0.2, token.request_cancel if action == "cancel" else lambda: token.request_skip(0))
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(error):
            run_process([sys.executable, "-c", "import time; time.sleep(30)"], token, 0, callbacks())
    finally:
        timer.cancel()
    assert time.monotonic() - started < 5
    assert processes and processes[0].poll() is not None


def test_cancellation_cleans_verification_scratch_and_retains_source(evidence, tmp_path, monkeypatch):
    executable = require_7zip()
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def cancel(command, token, job_id, *_):
        destination = Path(next(arg[2:] for arg in command if arg.startswith("-o")))
        (destination / "partial-extraction").write_text("partial")
        token.request_cancel()
        token.raise_if_requested(job_id)

    monkeypatch.setattr("content_verification.run_process", cancel)
    result = package(evidence, archive_fmt="7z", seven_zip_path=executable, verify_temp_dir=scratch, delete_source=True)
    assert result.status == "cancelled"
    assert evidence[1].exists()
    assert not list(scratch.iterdir())
    assert not list(evidence[2].glob("*.partial*"))
    assert HistoryStore().get(result.run_id)["status"] == "cancelled"


@pytest.mark.parametrize("error", [errno.ENOSPC, errno.ENODEV, errno.EIO])
def test_write_failure_retains_source_and_records_failure(evidence, monkeypatch, error):
    original = engine.create_archive

    def failure(*args, **kwargs):
        args[8].write_bytes(b"partial archive")
        raise OSError(error, "simulated destination failure")

    monkeypatch.setattr(engine, "create_archive", failure)
    result = package(evidence, delete_source=True)
    assert result.status == "failed"
    assert evidence[1].exists()
    assert not list(evidence[2].glob("*.partial*"))
    row = HistoryStore().get(result.run_id)
    assert row["status"] == "failed"
    assert "simulated destination failure" in row["warnings_json"]


def test_history_search_preserves_each_attempt_and_case_ids(evidence):
    options = {"case_metadata": {"Case ID": "2026-123", "Evidence ID": "Item-2"}}
    first = package(evidence, **options)
    second = package(evidence, **options)
    store = HistoryStore()
    rows, total = store.search("Item-2", "success")
    assert total == 2
    assert {r["run_id"] for r in rows} == {first.run_id, second.run_id}
    assert all(r["case_id"] == "2026-123" for r in rows)
    assert all(json.loads(r["reports_json"]) for r in rows)
    assert store.search("%", "All")[1] == 0
    assert len(store.search(limit=1)[0]) == 1


def test_reverify_after_archive_move_without_source(evidence, tmp_path):
    result = package(evidence)
    moved = tmp_path / "moved"
    moved.mkdir()
    shutil.move(result.archive_path, moved / Path(result.archive_path).name)
    shutil.rmtree(evidence[0])
    checked = reverify_run(result.run_id, archive_dir=moved)
    assert checked["status"] == "pass", checked
    assert "source not re-examined" in checked["scope"]
    row = HistoryStore().get(result.run_id)
    assert row["status"] == "success"
    assert HistoryStore().search()[0][0]["last_check"] == "pass"
    assert len(HistoryStore().events(result.run_id)) == 1


def test_reverify_corrupt_archive_records_failure_without_changing_original(evidence):
    result = package(evidence)
    original_audit = Path(result.audit_log_path).read_bytes()
    Path(result.archive_path).write_bytes(b"corrupt")
    checked = reverify_run(result.run_id)
    assert checked["status"] == "failed"
    assert "SHA256 mismatch" in checked["message"]
    assert HistoryStore().get(result.run_id)["verification"] == "PASS"
    assert Path(result.audit_log_path).read_bytes() == original_audit


def test_reverify_cancelled_is_recorded(evidence):
    result = package(evidence)
    token = CancellationToken()
    token.request_cancel()
    assert reverify_run(result.run_id, token=token)["status"] == "cancelled"


def test_export_bundle_checks_records_and_relocated_archive(evidence, tmp_path):
    result = package(evidence)
    original = Path(result.external_manifest_json).read_bytes()
    bundle = export_records([result.run_id], tmp_path / "records.zip")
    checked = verify_record_bundle(bundle, archive_dir=evidence[2])
    assert checked["status"] == "pass"
    assert checked["archive_parts_checked"] == 1
    with zipfile.ZipFile(bundle) as archive:
        assert not any(name.endswith("case.zip") for name in archive.namelist())
        assert archive.read(f"records/{result.run_id}/case.manifest.json") == original
        assert any("/reports/" in name for name in archive.namelist())
    assert Path(result.external_manifest_json).read_bytes() == original


def test_export_refuses_tampered_manifest(evidence, tmp_path):
    result = package(evidence)
    Path(result.external_manifest_json).write_text("{}")
    with pytest.raises(ValueError, match="changed"):
        export_records([result.run_id], tmp_path / "records.zip")


def test_export_cancel_does_not_publish_or_leave_partial(evidence, tmp_path):
    result = package(evidence)
    token = CancellationToken()
    token.request_cancel()
    with pytest.raises(JobCancelled):
        export_records([result.run_id], tmp_path / "records.zip", token=token)
    assert not (tmp_path / "records.zip").exists()
    assert not list(tmp_path.glob("records.zip.*.partial"))


def test_export_refuses_overwrite_and_bundle_detects_tampering(evidence, tmp_path):
    result = package(evidence)
    bundle = export_records([result.run_id], tmp_path / "records.zip")
    with pytest.raises(ValueError, match="already exists"):
        export_records([result.run_id], bundle)
    corrupted = tmp_path / "corrupted.zip"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(corrupted, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            target.writestr(name, b"changed" if name.endswith(".audit.jsonl") else data)
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_record_bundle(corrupted)


def test_import_previous_runs_does_not_rewrite_evidence_records(evidence):
    result = package(evidence)
    store = HistoryStore()
    original = Path(result.audit_log_path).read_bytes()
    with store.connect() as conn:
        conn.execute("DELETE FROM runs")
    assert store.import_previous_runs() == 1
    assert store.import_previous_runs() == 0
    assert store.get(result.run_id)["verification"] == "IMPORTED — NOT REVERIFIED"
    assert Path(result.audit_log_path).read_bytes() == original


def test_cli_history_export_and_verify(evidence, tmp_path, capsys):
    result = package(evidence)
    assert cli.run_cli(["history", "--query", "case"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 1
    bundle = tmp_path / "cli.zip"
    assert cli.run_cli(["export-records", "--run", result.run_id, "--output", str(bundle)]) == 0
    capsys.readouterr()
    assert cli.run_cli(["verify-records", "--input", str(bundle), "--archive-dir", str(evidence[2])]) == 0
    assert json.loads(capsys.readouterr().out)["archive_parts_checked"] == 1
    assert cli.run_cli(["reverify", "--run", result.run_id]) == 0


def test_long_path_zip_is_verified_and_indexed(evidence):
    _, case, _ = evidence
    long_dir = case.joinpath(*(["long-directory-name-0123456789"] * 9))
    try:
        long_dir.mkdir(parents=True)
        (long_dir / "long-file.txt").write_text("evidence")
    except OSError as exc:
        pytest.skip(f"Host filesystem long paths unavailable: {exc}")
    result = package(evidence)
    assert result.status == "success"
    assert result.archive_member_count == 2
    assert HistoryStore().get(result.run_id)["status"] == "success"


@pytest.mark.skipif(os.getenv("FORENSICPACK_LARGE_TESTS") != "1", reason="Opt-in large split-archive validation")
def test_large_native_split_archive_roundtrip(evidence):
    executable = require_7zip()
    _, case, _ = evidence
    with (case / "large.bin").open("wb") as handle:
        for _ in range(128):
            handle.write(os.urandom(1 << 20))
    result = package(evidence, archive_fmt="7z", seven_zip_path=executable, split_enabled=True,
                     split_size_str="0.1", compress_level_label="Store (0)")
    assert result.status == "success", result.warnings
    assert len(list(evidence[2].glob("*.7z.*"))) >= 2
    assert reverify_run(result.run_id)["status"] == "pass"
