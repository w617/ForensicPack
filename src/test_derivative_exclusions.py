from pathlib import Path

import engine
from models import JobConfig


def config_for(source: Path, **overrides) -> JobConfig:
    values = {
        "source_dir": source,
        "output_dir": source,
        "archive_fmt": "ZIP",
        "compress_level_label": "Normal (5)",
        "split_enabled": False,
        "split_size_str": "",
        "hash_algorithms": ["SHA256"],
        "password": None,
        "delete_source": False,
        "skip_existing": False,
        "preflight_space_check": False,
    }
    values.update(overrides)
    return JobConfig(**values)


def test_derivative_manifests_audits_and_checksums_are_always_excluded(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "caseA").mkdir()
    (evidence / "caseA" / "evidence.bin").write_bytes(b"evidence")

    derivative_names = {
        "caseA.manifest.json",
        "caseA.manifest.txt",
        "caseA.audit.json",
        "caseA.audit.jsonl",
        "caseA.sha256",
        "caseA.manifest.json.sig",
        "caseA.certificate.pem",
        "manifest.json",
        "manifest.txt",
        "audit.json",
        "audit.jsonl",
        ".sha256",
    }
    for name in derivative_names:
        (evidence / name).write_text("generated", encoding="utf-8")

    # Unrelated evidence with similar cryptographic extensions remains eligible.
    (evidence / "submitted-certificate.pem").write_text("evidence", encoding="utf-8")
    (evidence / "detached-signature.sig").write_text("evidence", encoding="utf-8")
    (evidence / "notes.json").write_text("evidence", encoding="utf-8")

    processable, excluded = engine.classify_source_items(evidence, config_for(evidence))

    processable_names = {path.name for path in processable}
    excluded_names = {path.name for path in excluded}
    assert derivative_names <= excluded_names
    assert derivative_names.isdisjoint(processable_names)
    assert {"caseA", "submitted-certificate.pem", "detached-signature.sig", "notes.json"} <= processable_names


def test_include_generated_outputs_override_remains_available(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    generated = {
        "caseA.manifest.json",
        "caseA.audit.json",
        "caseA.audit.jsonl",
        "caseA.sha256",
    }
    for name in generated:
        (evidence / name).write_text("generated", encoding="utf-8")

    processable, excluded = engine.classify_source_items(
        evidence,
        config_for(evidence, exclude_generated_outputs=False),
    )

    assert {path.name for path in processable} == generated
    assert excluded == []
