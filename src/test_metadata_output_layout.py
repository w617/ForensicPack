from pathlib import Path

import engine
from models import CancellationToken, JobCallbacks, JobConfig
from sidecars import verify_checksum_file
from utils import METADATA_DIR_NAME


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


def test_destination_root_contains_archive_and_metadata_folder_only(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "Case Files").mkdir()
    (source / "Case Files" / "evidence.bin").write_bytes(b"evidence")
    output = tmp_path / "destination"

    results = engine.run_session(config_for(source, output), callbacks(), CancellationToken())

    assert results[0].verify == "PASS WITH WARNINGS"
    root_names = {path.name for path in output.iterdir()}
    assert root_names == {"Case Files.zip", METADATA_DIR_NAME}
    assert not list(output.glob("*.audit.jsonl"))
    assert not list(output.glob("*.manifest.json"))
    assert not list(output.glob("*.manifest.txt"))
    assert not list(output.glob("*.sha256"))
    assert not list(output.glob("ForensicPack_Report_*.csv"))
    assert not list(output.glob("ForensicPack_Report_*.txt"))

    metadata = output / METADATA_DIR_NAME
    expected_metadata = {
        "Case Files.audit.jsonl",
        "Case Files.manifest.json",
        "Case Files.manifest.txt",
        "Case Files.sha256",
    }
    metadata_names = {path.name for path in metadata.iterdir()}
    assert expected_metadata <= metadata_names
    assert any(name.startswith("ForensicPack_Report_") and name.endswith(".csv") for name in metadata_names)
    assert any(name.startswith("ForensicPack_Report_") and name.endswith(".txt") for name in metadata_names)

    checksum_ok, checksum_issues = verify_checksum_file(metadata / "Case Files.sha256")
    assert checksum_ok is True
    assert checksum_issues == []


def test_metadata_folder_is_excluded_on_same_folder_rerun(tmp_path: Path) -> None:
    source = tmp_path / "evidence"
    source.mkdir()
    (source / "Case Files").mkdir()
    (source / "Case Files" / "evidence.bin").write_bytes(b"evidence")
    metadata = source / METADATA_DIR_NAME
    metadata.mkdir()
    (metadata / "Case Files.audit.jsonl").write_text("generated", encoding="utf-8")
    (metadata / "Case Files.manifest.json").write_text("{}", encoding="utf-8")
    (metadata / "Case Files.sha256").write_text("generated", encoding="utf-8")

    processable, excluded = engine.classify_source_items(source, config_for(source, source))

    assert [path.name for path in processable] == ["Case Files"]
    assert METADATA_DIR_NAME in {path.name for path in excluded}
