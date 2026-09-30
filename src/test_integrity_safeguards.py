"""Regression coverage for the five evidence-integrity fixes."""
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

import archivers
import core_v2
import engine
from audit import AuditLogger, verify_audit_log
from content_verification import verify_archive_member_hashes
from forensic_inventory import build_forensic_inventory
from models import CancellationToken, ScanIssue
from package_validation import resume_validation_error
from sidecars import verify_checksum_file
from test_hardening_core import callbacks, config_for
from utils import metadata_output_dir, resolve_state_db_path


@pytest.fixture
def evidence(tmp_path):
    source = tmp_path / "source"
    case = source / "case"
    case.mkdir(parents=True)
    (case / "evidence.txt").write_bytes(b"original evidence")
    return source, case, tmp_path / "output"


def run(source, output, **options):
    return engine.run_session(config_for(source, output, **options), callbacks(), CancellationToken())[0]


def test_delete_blocked_for_incomplete_scan(evidence, monkeypatch):
    source, case, output = evidence
    original = engine.build_inventory

    def incomplete(*args, **kwargs):
        records, size, issues = original(*args, **kwargs)
        issues.append(ScanIssue(str(case / "unreadable"), "read metadata", "PermissionError", "denied"))
        return records, size, issues

    monkeypatch.setattr(engine, "build_inventory", incomplete)
    result = run(source, output, delete_source=True)
    assert result.status == "warning"
    assert "SOURCE RETAINED" in result.verify
    assert case.is_dir()
    assert result.content_verify == "PASS"
    events = [json.loads(line) for line in Path(result.audit_log_path).read_text().splitlines()]
    assert any(e["event"] == "source_deletion_check" and not e["details"]["allowed"] for e in events)


@pytest.mark.parametrize("options", [
    {"verify_member_hashes": False}, {"hash_algorithms": []}, {"archive_hash_mode": "skip"},
])
def test_delete_blocked_when_required_verification_disabled(evidence, options):
    source, case, output = evidence
    result = run(source, output, delete_source=True, **options)
    assert case.is_dir()
    assert result.status == "warning"
    assert "SOURCE RETAINED" in result.verify


def test_delete_allowed_only_after_complete_verification(evidence):
    source, case, output = evidence
    result = run(source, output, delete_source=True)
    assert result.status == "success"
    assert result.content_verify == "PASS"
    assert not case.exists()
    assert verify_checksum_file(Path(result.checksum_path))[0]


@pytest.mark.parametrize("change", ["add", "modify"])
def test_delete_retains_source_changed_after_archive_verification(evidence, monkeypatch, change):
    source, case, output = evidence
    original = core_v2.verify_archive_member_hashes

    def verify_then_change(*args, **kwargs):
        result = original(*args, **kwargs)
        target = case / ("new.txt" if change == "add" else "evidence.txt")
        target.write_bytes(b"new evidence")
        return result

    monkeypatch.setattr(core_v2, "verify_archive_member_hashes", verify_then_change)
    result = run(source, output, delete_source=True)
    assert case.exists()
    assert result.status == "warning"
    assert "SOURCE RETAINED" in result.verify


@pytest.mark.parametrize("change", ["missing", "corrupt", "source_same_stat", "add", "remove", "manifest", "legacy_state"])
def test_resume_rebuilds_changed_or_missing_evidence(evidence, change):
    source, case, output = evidence
    first = run(source, output)
    original_audit = Path(first.audit_log_path).read_bytes()
    archive = Path(first.archive_path)
    if change == "missing":
        archive.unlink()
    elif change == "corrupt":
        archive.write_bytes(b"corrupt")
    elif change == "source_same_stat":
        target = case / "evidence.txt"
        stat = target.stat()
        target.write_bytes(b"modified evidence")
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    elif change == "add":
        (case / "new.txt").write_text("new")
    elif change == "remove":
        (case / "evidence.txt").unlink()
        (case / "replacement.txt").write_text("replacement")
    elif change == "manifest":
        Path(first.external_manifest_json).write_text("{}")
    else:
        db = resolve_state_db_path(config_for(source, output))
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE jobs SET manifest_sha256='' ")
    result = run(source, output, resume_enabled=True, skip_existing=True)
    assert result.status == "success"
    assert result.content_verify == "PASS"
    assert Path(result.archive_path).is_file()
    assert result.audit_log_path != first.audit_log_path
    assert Path(first.audit_log_path).read_bytes() == original_audit


def test_resume_validates_unchanged_package_without_text_manifest(evidence):
    source, _, output = evidence
    first = run(source, output, retain_manifests=False)
    result = run(source, output, retain_manifests=False, resume_enabled=True)
    assert result.status == "skipped"
    assert result.content_verify == "PASS"
    assert result.external_manifest_json == first.external_manifest_json


def test_resume_does_not_overwrite_unrelated_archive(evidence):
    source, _, output = evidence
    output.mkdir()
    archive = output / "case.zip"
    archive.write_bytes(b"unrelated")
    with pytest.raises(ValueError, match="collision"):
        run(source, output, resume_enabled=True)
    assert archive.read_bytes() == b"unrelated"


@pytest.mark.parametrize("change", ["missing", "corrupt", "extra"])
def test_resume_checks_every_split_volume(evidence, change):
    source, case, output = evidence
    output.mkdir()
    config = config_for(source, output, archive_fmt="7z", split_enabled=True, split_size_str="1")
    parts = [output / f"case.7z.{index:03d}" for index in (1, 2)]
    for part in parts:
        part.write_bytes(part.name.encode())
    records, _, _ = build_forensic_inventory(case, 0, CancellationToken(), callbacks())
    files = [r.to_manifest_dict({"SHA256": hashlib.sha256(r.path.read_bytes()).hexdigest()}) for r in records]
    manifest = output / "saved.json"
    manifest.write_text(json.dumps({
        "schema": "org.forensicpack.package-manifest/v1", "source_item": str(case),
        "archive_format": "7z", "content_verification": "PASS", "scan_issues": [], "files": files,
        "archive_parts": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in parts},
    }))
    row = {"state": "completed", "verify": "PASS", "manifest_json_path": str(manifest),
           "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(), "archive_path": str(parts[0])}
    assert resume_validation_error(row, case, config, CancellationToken(), callbacks(), 0) is None
    if change == "missing":
        parts[1].unlink()
    elif change == "corrupt":
        parts[1].write_bytes(b"corrupt")
    else:
        (output / "case.7z.003").write_bytes(b"extra")
    assert resume_validation_error(row, case, config, CancellationToken(), callbacks(), 0)


def test_reruns_preserve_each_manifest_and_audit(evidence):
    source, _, output = evidence
    first = run(source, output)
    paths = [Path(first.manifest_path), Path(first.external_manifest_json), Path(first.audit_log_path), Path(first.checksum_path)]
    before = {p: p.read_bytes() for p in paths}
    second = run(source, output)
    assert first.audit_log_path != second.audit_log_path
    assert all(path.read_bytes() == content for path, content in before.items())
    assert verify_audit_log(Path(first.audit_log_path))[0]
    assert verify_audit_log(Path(second.audit_log_path))[0]
    assert verify_checksum_file(Path(second.checksum_path))[0]
    assert verify_checksum_file(metadata_output_dir(output) / "case.sha256")[0]


def test_audit_logger_rejects_reused_path_without_truncating(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLogger(path).record("original")
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        AuditLogger(path)
    assert path.read_bytes() == original


def test_legacy_audit_and_manifest_preserved_on_upgrade(evidence):
    source, _, output = evidence
    root = metadata_output_dir(output)
    root.mkdir(parents=True)
    (root / "case.audit.jsonl").write_bytes(b"legacy audit")
    (root / "case.manifest.json").write_bytes(b"legacy manifest")
    run(source, output)
    histories = list((root / "History").iterdir())
    assert len(histories) == 1
    assert (histories[0] / "case.audit.jsonl").read_bytes() == b"legacy audit"
    assert (histories[0] / "case.manifest.json").read_bytes() == b"legacy manifest"


def test_failed_rerun_preserves_previous_audit(evidence, monkeypatch):
    source, _, output = evidence
    first = run(source, output)
    original = Path(first.audit_log_path).read_bytes()

    def fail(*_args, **_kwargs):
        raise RuntimeError("simulated archive failure")

    monkeypatch.setattr(engine, "create_archive", fail)
    second = run(source, output)
    assert second.status == "failed"
    assert Path(first.audit_log_path).read_bytes() == original
    assert verify_audit_log(Path(second.audit_log_path))[0]


def test_invalid_7z_never_passes_member_verification(evidence, tmp_path):
    source, case, output = evidence
    records, _, _ = build_forensic_inventory(case, 0, CancellationToken(), callbacks())
    hashes = {r.path: {"SHA256": hashlib.sha256(r.path.read_bytes()).hexdigest()} for r in records}
    invalid = tmp_path / "invalid.7z"
    invalid.write_bytes(b"not a 7z archive")
    ok, detail, count = verify_archive_member_hashes(
        invalid, records, hashes, config_for(source, output, archive_fmt="7z"), CancellationToken(), callbacks(), 0
    )
    assert not ok
    assert count == 0
    assert "signature" in detail


def test_zero_member_success_is_rejected_by_packaging(evidence, monkeypatch):
    source, case, output = evidence
    monkeypatch.setattr(core_v2, "verify_archive_member_hashes", lambda *_args, **_kwargs: (True, "skipped", 0))
    result = run(source, output, delete_source=True)
    assert result.status == "failed"
    assert case.exists()
    assert result.content_verify.startswith("FAILED")


def test_7z_engine_calls_real_runner_without_recursion(evidence, monkeypatch):
    source, _, output = evidence
    commands = []

    class Process:
        returncode = 0
        stdout = []

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(archivers, "find_7zip", lambda *_args: "7z")

    def popen(command, **_kwargs):
        commands.append(command)
        if command[1] == "a":
            target = next(Path(arg) for arg in command[2:] if not arg.startswith("-"))
            target.write_bytes(b"synthetic archive")
        return Process()

    monkeypatch.setattr(archivers.subprocess, "Popen", popen)
    original_runner = archivers._run_7zip
    result = run(source, output, archive_fmt="7z", password="secret", verify_member_hashes=False)
    assert result.status == "warning"
    assert [command[1] for command in commands] == ["a", "t"]
    assert all("-psecret" in command for command in commands)
    assert archivers._run_7zip is original_runner


@pytest.mark.parametrize("password,split", [(None, False), ("test-secret", False), ("test-secret", True)])
def test_native_7z_roundtrip(evidence, password, split):
    executable = archivers.find_7zip()
    if not executable:
        pytest.skip("Native 7-Zip is not available")
    source, _, output = evidence
    result = run(source, output, archive_fmt="7z", seven_zip_path=Path(executable), password=password,
                 split_enabled=split, split_size_str="0.1" if split else "")
    assert result.status == "success", result.warnings
    assert result.content_verify == "PASS"
    assert result.archive_member_count == 1
    assert verify_checksum_file(Path(result.checksum_path))[0]
