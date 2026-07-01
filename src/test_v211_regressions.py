from pathlib import Path

import pytest

import engine
from models import CancellationToken, JobCallbacks, JobConfig


def callbacks() -> JobCallbacks:
    return JobCallbacks(
        log_cb=lambda _message, _colour=None: None,
        progress_overall_cb=lambda _fraction: None,
        progress_case_cb=lambda _fraction: None,
        status_cb=lambda _message: None,
    )


def config_for(source: Path, output: Path, **overrides) -> JobConfig:
    values = {
        "source_dir": source,
        "output_dir": output,
        "archive_fmt": "ZIP",
        "compress_level_label": "Normal (5)",
        "split_enabled": False,
        "split_size_str": "",
        "hash_algorithms": ["SHA256"],
        "password": None,
        "delete_source": False,
        "skip_existing": False,
        "threads": 1,
        "preflight_space_check": False,
    }
    values.update(overrides)
    return JobConfig(**values)


def test_unrelated_sidecar_collision_fails_before_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "case.txt").write_text("evidence", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    protected = output / "case.txt.manifest.json"
    protected.write_text("unrelated-existing-content", encoding="utf-8")

    with pytest.raises(ValueError, match="case.txt.manifest.json"):
        engine.run_session(config_for(source, output), callbacks(), CancellationToken())

    assert protected.read_text(encoding="utf-8") == "unrelated-existing-content"
    assert not (output / "case.txt.zip").exists()


def test_same_folder_split_rerun_excludes_every_volume(tmp_path: Path) -> None:
    source = tmp_path / "evidence"
    source.mkdir()
    case = source / "caseA"
    case.mkdir()
    (case / "evidence.bin").write_bytes(b"evidence")
    (source / "caseA.7z.001").write_bytes(b"part-one")
    (source / "caseA.7z.002").write_bytes(b"part-two")
    (source / "caseA.manifest.json").write_text("{}", encoding="utf-8")
    (source / "caseA.audit.jsonl").write_text("", encoding="utf-8")

    config = config_for(
        source,
        source,
        archive_fmt="7z",
        split_enabled=True,
        split_size_str="1",
    )
    processable, excluded = engine.classify_source_items(source, config)

    assert [path.name for path in processable] == ["caseA"]
    excluded_names = {path.name for path in excluded}
    assert {"caseA.7z.001", "caseA.7z.002", "caseA.manifest.json", "caseA.audit.jsonl"} <= excluded_names
