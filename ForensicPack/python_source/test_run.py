import csv
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import cli
import engine
from engine import CancellationToken, JobCallbacks, JobConfig, JobResult, process_cases, run_session, run_verify_session, verify_archive
from gui_assets import TITLE_BANNER_CANDIDATES, resolve_first_existing_gui_asset, resolve_gui_asset_path
import gui_state


def _callbacks():
    return JobCallbacks(
        log_cb=lambda _msg, _colour=None: None,
        progress_overall_cb=lambda _fraction: None,
        progress_case_cb=lambda _fraction: None,
        status_cb=lambda _text: None,
    )


@pytest.fixture
def sample_source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    file_item = source / "document.txt"
    file_item.write_text("evidence", encoding="utf-8")
    case_dir = source / "caseA"
    case_dir.mkdir()
    (case_dir / "notes.txt").write_text("notes", encoding="utf-8")
    return source


def test_zip_naming_for_file_and_directory(sample_source: Path, tmp_path: Path):
    output = tmp_path / "output"
    config = JobConfig(
        source_dir=sample_source,
        output_dir=output,
        archive_fmt="ZIP",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=["SHA256"],
        password=None,
        delete_source=False,
        skip_existing=False,
        threads=2,
    )
    results = run_session(config, _callbacks(), CancellationToken())
    archive_names = sorted(Path(result.archive_path).name for result in results)
    assert "document.txt.zip" in archive_names
    assert "caseA.zip" in archive_names


def test_zip_handles_pre_1980_file_timestamps(sample_source: Path, tmp_path: Path):
    output = tmp_path / "output"
    legacy_file = sample_source / "document.txt"
    os.utime(legacy_file, (1, 1))
    config = JobConfig(
        source_dir=sample_source,
        output_dir=output,
        archive_fmt="ZIP",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=["SHA256"],
        password=None,
        delete_source=False,
        skip_existing=False,
        threads=1,
    )
    results = run_session(config, _callbacks(), CancellationToken())
    assert all(result.verify == "PASS" for result in results)


def test_selected_item_names_filters_session_items(sample_source: Path, tmp_path: Path):
    output = tmp_path / "output"
    config = JobConfig(
        source_dir=sample_source,
        output_dir=output,
        archive_fmt="ZIP",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=["SHA256"],
        password=None,
        delete_source=False,
        skip_existing=False,
        threads=1,
        selected_item_names=["caseA"],
    )
    results = run_session(config, _callbacks(), CancellationToken())
    assert [result.case_name for result in results] == ["caseA"]


def test_selected_item_names_missing_returns_no_results(sample_source: Path, tmp_path: Path):
    output = tmp_path / "output"
    config = JobConfig(
        source_dir=sample_source,
        output_dir=output,
        archive_fmt="ZIP",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=["SHA256"],
        password=None,
        delete_source=False,
        skip_existing=False,
        threads=1,
        selected_item_names=["missing-item"],
    )
    results = run_session(config, _callbacks(), CancellationToken())
    assert results == []


def test_password_rejected_for_zip(sample_source: Path, tmp_path: Path):
    output = tmp_path / "output"
    config = JobConfig(
        source_dir=sample_source,
        output_dir=output,
        archive_fmt="ZIP",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=["SHA256"],
        password="secret",
        delete_source=False,
        skip_existing=False,
        threads=1,
    )
    with pytest.raises(ValueError, match="Password protection is only supported for 7z"):
        run_session(config, _callbacks(), CancellationToken())


def test_skip_existing_requires_verified_archive(sample_source: Path, tmp_path: Path):
    output = tmp_path / "output"
    config = JobConfig(
        source_dir=sample_source,
        output_dir=output,
        archive_fmt="ZIP",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=["SHA256"],
        password=None,
        delete_source=False,
        skip_existing=False,
        threads=1,
    )
    run_session(config, _callbacks(), CancellationToken())
    config.skip_existing = True
    results = run_session(config, _callbacks(), CancellationToken())
    assert all(result.verify == "SKIPPED (Verified Existing)" for result in results)


def test_cancel_during_manifest_leaves_no_final_archive(sample_source: Path, tmp_path: Path):
    output = tmp_path / "output"
    token = CancellationToken()

    def progress_case(_fraction: float):
        token.request_cancel()

    callbacks = JobCallbacks(
        log_cb=lambda _msg, _colour=None: None,
        progress_overall_cb=lambda _fraction: None,
        progress_case_cb=progress_case,
        status_cb=lambda _text: None,
    )
    config = JobConfig(
        source_dir=sample_source,
        output_dir=output,
        archive_fmt="ZIP",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=["SHA256"],
        password=None,
        delete_source=False,
        skip_existing=False,
        threads=1,
    )
    results = run_session(config, callbacks, token)
    assert any(result.verify == "CANCELLED" for result in results)
    assert not any(path.suffix == ".zip" for path in output.glob("*.zip"))


def test_tar_verification_detects_truncation(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    item = source / "caseA"
    item.mkdir()
    (item / "evidence.txt").write_text("abc" * 2000, encoding="utf-8")
    output = tmp_path / "output"
    config = JobConfig(
        source_dir=source,
        output_dir=output,
        archive_fmt="TAR.GZ",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=["SHA256"],
        password=None,
        delete_source=False,
        skip_existing=False,
        threads=1,
    )
    results = run_session(config, _callbacks(), CancellationToken())
    archive_path = Path(results[0].archive_path)
    truncated = output / "truncated.tar.gz"
    truncated.write_bytes(archive_path.read_bytes()[: max(1, archive_path.stat().st_size // 2)])
    assert not verify_archive(truncated, "TAR.GZ", _callbacks())


def test_embed_manifest_can_be_disabled_for_zip(sample_source: Path, tmp_path: Path):
    output = tmp_path / "output"
    config = JobConfig(
        source_dir=sample_source,
        output_dir=output,
        archive_fmt="ZIP",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=["SHA256"],
        password=None,
        delete_source=False,
        skip_existing=False,
        threads=1,
        embed_manifest_in_archive=False,
    )
    results = run_session(config, _callbacks(), CancellationToken())
    archive_path = Path(results[0].archive_path)
    import zipfile
    with zipfile.ZipFile(archive_path, "r") as zf:
        names = set(zf.namelist())
    assert f"{results[0].case_name}_manifest.txt" not in names


def test_run_verify_session_handles_duplicate_archive_names(tmp_path: Path):
    verify_root = tmp_path / "verify"
    first_dir = verify_root / "a"
    second_dir = verify_root / "b"
    first_dir.mkdir(parents=True)
    second_dir.mkdir(parents=True)

    import zipfile
    first = first_dir / "case.zip"
    second = second_dir / "case.zip"
    with zipfile.ZipFile(first, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("one.txt", "one")
    with zipfile.ZipFile(second, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("two.txt", "two")

    output = tmp_path / "output"
    results = run_verify_session(
        verify_input=verify_root,
        output_dir=output,
        callbacks=_callbacks(),
        hash_algorithms=["SHA256"],
        report_json=False,
    )
    assert len(results) == 2
    assert Path(results[0].archive_path) == first
    assert Path(results[1].archive_path) == second
    assert all(result.verify == "PASS" for result in results)


def _result_with_status(status: str) -> JobResult:
    return JobResult(
        case_name="case",
        format="ZIP",
        start_time="",
        end_time="",
        file_count=0,
        source_size=0,
        archive_path="",
        archive_size=0,
        verify=status,
    )


def test_split_7z_uses_volume_entrypoint_for_verify_and_skip_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "source"
    source.mkdir()
    case_dir = source / "caseA"
    case_dir.mkdir()
    (case_dir / "evidence.bin").write_bytes(b"x" * 4096)
    output = tmp_path / "output"
    tested_paths: list[Path] = []

    def fake_run_7zip(_job_id, args, _token, _runtime, _callbacks):
        command = args[0]
        if command == "a":
            targets = [Path(arg) for arg in args[1:] if not str(arg).startswith("-")]
            target = targets[0]
            split_mode = any(str(arg).startswith("-v") for arg in args) or (target.parent / f"{target.name}.001").exists()
            if split_mode:
                (target.parent / f"{target.name}.001").write_bytes(b"part1")
                (target.parent / f"{target.name}.002").write_bytes(b"part2")
            else:
                target.write_bytes(b"archive")
            return True
        if command == "t":
            path = Path(args[1])
            tested_paths.append(path)
            return path.exists()
        return False

    monkeypatch.setattr(engine, "_run_7zip", fake_run_7zip)

    config = JobConfig(
        source_dir=source,
        output_dir=output,
        archive_fmt="7z",
        compress_level_label="Normal (5)",
        split_enabled=True,
        split_size_str="1",
        hash_algorithms=["SHA256"],
        password="secret",
        delete_source=False,
        skip_existing=False,
        threads=1,
    )
    first_results = run_session(config, _callbacks(), CancellationToken())
    assert first_results[0].verify == "PASS"
    assert first_results[0].archive_path.endswith(".7z.001")
    assert (output / "caseA.7z.001").exists()
    assert first_results[0].archive_size == 10
    assert any(path.name.endswith(".partial.001") for path in tested_paths)

    config.skip_existing = True
    second_results = run_session(config, _callbacks(), CancellationToken())
    assert second_results[0].verify == "SKIPPED (Verified Existing)"
    assert second_results[0].archive_path.endswith(".7z.001")
    assert any(path.name == "caseA.7z.001" for path in tested_paths)


def test_password_is_redacted_in_logged_commands():
    redacted_inline = engine._redact_command(["7z", "a", "-psecret", "archive.7z", "input"])
    redacted_split = engine._redact_command(["7z", "a", "-p", "secret", "archive.7z", "input"])
    assert "secret" not in redacted_inline
    assert "secret" not in redacted_split
    assert "-p***" in redacted_inline
    assert "-p ***" in redacted_split


def test_cleanup_cancel_artifacts_removes_partial_and_temp_manifest(tmp_path: Path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "case.zip.partial").write_text("x", encoding="utf-8")
    (output / "case.zip.partial.001").write_text("x", encoding="utf-8")
    (output / "tmp_2_case_manifest.txt").write_text("x", encoding="utf-8")
    removed = engine._cleanup_cancel_artifacts(output)
    assert removed == 3
    assert not (output / "case.zip.partial").exists()
    assert not (output / "case.zip.partial.001").exists()
    assert not (output / "tmp_2_case_manifest.txt").exists()


@pytest.mark.parametrize(
    ("source_dir", "output_dir", "message"),
    [
        ("same", "same", "must be different"),
        ("source", "source/output", "cannot be inside source"),
        ("output/source", "output", "cannot be inside output"),
    ],
)
def test_validate_config_rejects_unsafe_source_output_relationships(
    tmp_path: Path,
    source_dir: str,
    output_dir: str,
    message: str,
):
    source = tmp_path / source_dir
    output = tmp_path / output_dir
    source.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    config = JobConfig(
        source_dir=source,
        output_dir=output,
        archive_fmt="ZIP",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=["SHA256"],
        password=None,
        delete_source=False,
        skip_existing=False,
        threads=1,
    )
    with pytest.raises(ValueError, match=message):
        run_session(config, _callbacks(), CancellationToken())


def test_cli_normalizes_hashes_and_returns_nonzero_on_failed_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    captured_hashes: list[str] = []

    def fake_run_session(config, _callbacks, _token):
        captured_hashes[:] = config.hash_algorithms
        return [_result_with_status("FAILED")]

    monkeypatch.setattr(cli, "run_session", fake_run_session)
    exit_code = cli.run_cli(
        [
            "pack",
            "--source",
            str(tmp_path / "source"),
            "--output",
            str(tmp_path / "output"),
            "--format",
            "zip",
            "--hash",
            "sha-256",
            "--hash",
            "SHA256",
            "--hash",
            "sha512",
        ]
    )
    assert captured_hashes == ["SHA256", "SHA512"]
    assert exit_code == 1


def test_cli_returns_zero_when_all_jobs_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cli, "run_session", lambda *_args, **_kwargs: [_result_with_status("PASS")])
    exit_code = cli.run_cli(
        [
            "pack",
            "--source",
            str(tmp_path / "source"),
            "--output",
            str(tmp_path / "output"),
            "--format",
            "zip",
            "--hash",
            "SHA256",
        ]
    )
    assert exit_code == 0


def test_hash_normalization_populates_hash_columns(sample_source: Path, tmp_path: Path):
    output = tmp_path / "output"
    config = JobConfig(
        source_dir=sample_source,
        output_dir=output,
        archive_fmt="ZIP",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=["sha-256", "SHA256", "sha512"],
        password=None,
        delete_source=False,
        skip_existing=False,
        threads=1,
    )
    results = run_session(config, _callbacks(), CancellationToken())
    assert config.hash_algorithms == ["SHA256", "SHA512"]
    assert all(set(result.hashes) == {"SHA256", "SHA512"} for result in results)
    report_csv = next(output.glob("ForensicPack_Report_*.csv"))
    with report_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert all(row["SHA256"] for row in rows)
    assert all(row["SHA512"] for row in rows)


def test_session_allows_hashing_disabled(sample_source: Path, tmp_path: Path):
    output = tmp_path / "output"
    config = JobConfig(
        source_dir=sample_source,
        output_dir=output,
        archive_fmt="ZIP",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=[],
        password=None,
        delete_source=False,
        skip_existing=False,
        threads=1,
    )
    results = run_session(config, _callbacks(), CancellationToken())
    assert all(result.verify == "PASS" for result in results)
    assert all(result.hashes == {} for result in results)
    report_csv = next(output.glob("ForensicPack_Report_*.csv"))
    with report_csv.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert all(not row["SHA256"] for row in rows)
    assert all(not row["SHA512"] for row in rows)


def test_process_cases_skip_current_requests_single_job(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    skip_flag = threading.Event()
    observed = {"target": None, "other": None}

    def fake_run_session(_config, callbacks, token):
        callbacks.item_status_cb(3, "running")
        skip_flag.set()
        time.sleep(0.2)
        observed["target"] = token.state_for(3)
        observed["other"] = token.state_for(2)
        callbacks.item_status_cb(3, "skipped")
        return []

    monkeypatch.setattr(engine, "run_session", fake_run_session)

    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir()
    output.mkdir()
    process_cases(
        source_dir=str(source),
        output_dir=str(output),
        archive_fmt="ZIP",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=["SHA256"],
        password="",
        delete_source=False,
        skip_existing=False,
        skip_current_flag=skip_flag,
    )
    assert observed["target"] == "skip"
    assert observed["other"] is None


def test_progress_throttling_preserves_final_event():
    events: list[tuple[float, str]] = []
    callbacks = JobCallbacks(
        log_cb=lambda _msg, _colour=None: None,
        progress_overall_cb=lambda _fraction: None,
        progress_case_cb=lambda fraction: events.append((fraction, "case")),
        status_cb=lambda _text: None,
        progress_interval_ms=200,
    )
    callbacks.emit_progress(engine.ProgressEvent(0, "manifest", 1, 100, "a"))
    callbacks.emit_progress(engine.ProgressEvent(0, "manifest", 2, 100, "b"))
    callbacks.emit_progress(engine.ProgressEvent(0, "manifest", 100, 100, "done"))
    assert len(events) == 2
    assert events[-1][0] == 1.0


def test_item_failure_callback_receives_error_reason(sample_source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    output = tmp_path / "output"

    def fail_archive(*_args, **_kwargs):
        raise RuntimeError("forced archive failure")

    monkeypatch.setattr(engine, "create_archive", fail_archive)
    failures: list[tuple[int, str]] = []
    callbacks = JobCallbacks(
        log_cb=lambda _msg, _colour=None: None,
        progress_overall_cb=lambda _fraction: None,
        progress_case_cb=lambda _fraction: None,
        status_cb=lambda _text: None,
        item_failure_cb=lambda idx, reason: failures.append((idx, reason)),
    )
    config = JobConfig(
        source_dir=sample_source,
        output_dir=output,
        archive_fmt="ZIP",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=["SHA256"],
        password=None,
        delete_source=False,
        skip_existing=False,
        threads=1,
    )
    results = run_session(config, callbacks, CancellationToken())
    assert any(result.verify == "FAILED" for result in results)
    assert failures
    assert any("forced archive failure" in reason for _, reason in failures)


def test_cancellation_token_cancel_callbacks_fire_immediately():
    token = CancellationToken()
    called = threading.Event()
    token.on_cancel(lambda: called.set())
    token.request_cancel()
    assert called.is_set()


def test_cancellation_token_late_callback_fires_when_already_cancelled():
    token = CancellationToken()
    token.request_cancel()
    called = threading.Event()
    token.on_cancel(lambda: called.set())
    assert called.is_set()


def test_fast_scan_matches_deterministic_inventory(tmp_path: Path):
    source = tmp_path / "case"
    source.mkdir()
    (source / "b.txt").write_text("b", encoding="utf-8")
    (source / "a.txt").write_text("a", encoding="utf-8")
    token = CancellationToken()
    deterministic, _ = engine.build_inventory(source, 0, token, _callbacks(), scan_mode="deterministic")
    fast, _ = engine.build_inventory(source, 0, token, _callbacks(), scan_mode="fast")
    left = {(record.manifest_rel, record.size) for record in deterministic}
    right = {(record.manifest_rel, record.size) for record in fast}
    assert left == right


def test_dry_run_creates_no_archives(sample_source: Path, tmp_path: Path):
    output = tmp_path / "output"
    config = JobConfig(
        source_dir=sample_source,
        output_dir=output,
        archive_fmt="ZIP",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=["SHA256"],
        password=None,
        delete_source=False,
        skip_existing=False,
        threads=1,
        dry_run=True,
    )
    results = run_session(config, _callbacks(), CancellationToken())
    assert all(result.verify == "DRY-RUN" for result in results)
    assert not list(output.glob("*.zip"))


def test_state_store_tracks_and_resume_skips_completed(sample_source: Path, tmp_path: Path):
    output = tmp_path / "output"
    state_db = output / "state.db"
    config = JobConfig(
        source_dir=sample_source,
        output_dir=output,
        archive_fmt="ZIP",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=["SHA256"],
        password=None,
        delete_source=False,
        skip_existing=False,
        threads=1,
        state_db_path=state_db,
    )
    first = run_session(config, _callbacks(), CancellationToken())
    assert all(result.verify == "PASS" for result in first)
    conn = sqlite3.connect(state_db)
    rows = conn.execute("SELECT state, COUNT(*) FROM jobs GROUP BY state").fetchall()
    conn.close()
    assert ("completed", 2) in rows

    config.resume_enabled = True
    second = run_session(config, _callbacks(), CancellationToken())
    assert all("Resume Preserved" in result.verify for result in second)
    assert config.resume_used is True


def test_split_parts_persisted_in_state_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "source"
    source.mkdir()
    case_dir = source / "caseA"
    case_dir.mkdir()
    (case_dir / "evidence.bin").write_bytes(b"x" * 4096)
    output = tmp_path / "output"
    state_db = output / "state.db"

    def fake_run_7zip(_job_id, args, _token, _runtime, _callbacks):
        command = args[0]
        if command == "a":
            targets = [Path(arg) for arg in args[1:] if not str(arg).startswith("-")]
            target = targets[0]
            split_mode = any(str(arg).startswith("-v") for arg in args) or (target.parent / f"{target.name}.001").exists()
            if split_mode:
                (target.parent / f"{target.name}.001").write_bytes(b"part1")
                (target.parent / f"{target.name}.002").write_bytes(b"part2")
            else:
                target.write_bytes(b"archive")
            return True
        if command == "t":
            return Path(args[1]).exists()
        return False

    monkeypatch.setattr(engine, "_run_7zip", fake_run_7zip)
    config = JobConfig(
        source_dir=source,
        output_dir=output,
        archive_fmt="7z",
        compress_level_label="Normal (5)",
        split_enabled=True,
        split_size_str="1",
        hash_algorithms=["SHA256"],
        password=None,
        delete_source=False,
        skip_existing=False,
        threads=1,
        state_db_path=state_db,
    )
    run_session(config, _callbacks(), CancellationToken())
    conn = sqlite3.connect(state_db)
    parts = conn.execute("SELECT part_path FROM parts ORDER BY part_path").fetchall()
    conn.close()
    assert any(part[0].endswith(".7z.001") for part in parts)
    assert any(part[0].endswith(".7z.002") for part in parts)


def test_cli_verify_command_returns_nonzero_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cli, "run_verify_session", lambda **_kwargs: [_result_with_status("FAILED")])
    exit_code = cli.run_cli(["verify", "--input", str(tmp_path)])
    assert exit_code == 1


def test_cli_pack_exposes_new_policy_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    def fake_run_session(config, _callbacks, _token):
        captured["scan_mode"] = config.scan_mode
        captured["archive_hash_mode"] = config.archive_hash_mode
        captured["thread_strategy"] = config.thread_strategy
        captured["resume_enabled"] = config.resume_enabled
        captured["dry_run"] = config.dry_run
        captured["progress_interval_ms"] = config.progress_interval_ms
        captured["report_json"] = config.report_json
        captured["embed_manifest_in_archive"] = config.embed_manifest_in_archive
        return [_result_with_status("PASS")]

    monkeypatch.setattr(cli, "run_session", fake_run_session)
    exit_code = cli.run_cli(
        [
            "pack",
            "--source",
            str(tmp_path / "source"),
            "--output",
            str(tmp_path / "output"),
            "--format",
            "zip",
            "--hash",
            "SHA256",
            "--scan-mode",
            "fast",
            "--archive-hash-mode",
            "skip",
            "--thread-strategy",
            "auto",
            "--resume",
            "--dry-run",
            "--progress-interval-ms",
            "50",
            "--report-json",
        ]
    )
    assert exit_code == 0
    assert captured == {
        "scan_mode": "fast",
        "archive_hash_mode": "skip",
        "thread_strategy": "auto",
        "resume_enabled": True,
        "dry_run": True,
        "progress_interval_ms": 50,
        "report_json": True,
        "embed_manifest_in_archive": True,
    }


def test_cli_pack_can_disable_embed_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    def fake_run_session(config, _callbacks, _token):
        captured["embed_manifest_in_archive"] = config.embed_manifest_in_archive
        return [_result_with_status("PASS")]

    monkeypatch.setattr(cli, "run_session", fake_run_session)
    exit_code = cli.run_cli(
        [
            "pack",
            "--source",
            str(tmp_path / "source"),
            "--output",
            str(tmp_path / "output"),
            "--format",
            "zip",
            "--no-embed-manifest",
        ]
    )
    assert exit_code == 0
    assert captured["embed_manifest_in_archive"] is False


def test_cli_pack_accepts_missing_hash_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    captured_hashes: list[str] = []

    def fake_run_session(config, _callbacks, _token):
        captured_hashes[:] = config.hash_algorithms
        return [_result_with_status("PASS")]

    monkeypatch.setattr(cli, "run_session", fake_run_session)
    exit_code = cli.run_cli(
        [
            "pack",
            "--source",
            str(tmp_path / "source"),
            "--output",
            str(tmp_path / "output"),
            "--format",
            "zip",
        ]
    )
    assert exit_code == 0
    assert captured_hashes == []


def test_report_json_emitted_when_enabled(sample_source: Path, tmp_path: Path):
    output = tmp_path / "output"
    config = JobConfig(
        source_dir=sample_source,
        output_dir=output,
        archive_fmt="ZIP",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=["SHA256"],
        password=None,
        delete_source=False,
        skip_existing=False,
        threads=1,
        report_json=True,
    )
    run_session(config, _callbacks(), CancellationToken())
    report_json = next(output.glob("ForensicPack_Report_*.json"))
    assert report_json.exists()


def test_icon_assets_exist_at_expected_paths():
    png_path = resolve_gui_asset_path("forensicpack_icon.png")
    ico_path = resolve_gui_asset_path("forensicpack_icon.ico")
    assert png_path.is_file()
    assert ico_path.is_file()


def test_resolve_first_existing_gui_asset():
    resolved_icon = resolve_first_existing_gui_asset(("forensicpack_icon.png",))
    missing = resolve_first_existing_gui_asset(("missing_banner_file.png",))
    assert resolved_icon is not None
    assert resolved_icon.name == "forensicpack_icon.png"
    assert missing is None
    assert isinstance(TITLE_BANNER_CANDIDATES, tuple)
    assert "forensicpack_icon.png" in TITLE_BANNER_CANDIDATES


def test_build_windows_elevation_command_script_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    launcher = tmp_path / "forensicpack.py"
    launcher.write_text("print('x')", encoding="utf-8")
    monkeypatch.setattr(gui_state.sys, "frozen", False, raising=False)
    monkeypatch.setattr(gui_state.sys, "executable", r"C:\Python\python.exe")
    monkeypatch.setattr(gui_state.sys, "argv", [str(launcher)])
    executable, params = gui_state.build_windows_elevation_command(str(tmp_path / "unused" / "app.py"))
    assert executable == r"C:\Python\python.exe"
    assert "forensicpack.py" in params
    assert params.endswith(" gui")


def test_build_windows_elevation_command_frozen_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gui_state.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gui_state.sys, "executable", r"C:\Program Files\ForensicPack\ForensicPack.exe")
    executable, params = gui_state.build_windows_elevation_command(str(Path.cwd() / "x.py"))
    assert executable.endswith("ForensicPack.exe")
    assert params == ""


def test_is_permission_error_detection():
    assert gui_state.is_permission_error(PermissionError("access denied")) is True
    assert gui_state.is_permission_error(OSError(13, "Permission denied")) is True
    assert gui_state.is_permission_error(RuntimeError("other failure")) is False
