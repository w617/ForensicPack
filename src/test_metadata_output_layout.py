from pathlib import Path

import pytest

import engine
from models import CancellationToken, JobCallbacks, JobConfig
from sidecars import _checksum_name, verify_checksum_file
from utils import METADATA_DIR_NAME, application_data_dir, metadata_output_dir, resolve_state_db_path


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


def test_destination_root_contains_archives_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("FORENSICPACK_APPDATA", str(appdata))
    source = tmp_path / "source"
    source.mkdir()
    (source / "Case Files").mkdir()
    (source / "Case Files" / "evidence.bin").write_bytes(b"evidence")
    output = tmp_path / "destination"
    config = config_for(source, output)

    results = engine.run_session(config, callbacks(), CancellationToken())

    assert results[0].verify == "PASS"
    assert {path.name for path in output.iterdir()} == {"Case Files.zip"}
    assert not list(output.glob("*.audit.jsonl"))
    assert not list(output.glob("*.manifest.json"))
    assert not list(output.glob("*.manifest.txt"))
    assert not list(output.glob("*.sha256"))
    assert not list(output.glob("ForensicPack_Report_*.csv"))
    assert not list(output.glob("ForensicPack_Report_*.txt"))
    assert not (output / METADATA_DIR_NAME).exists()

    metadata = metadata_output_dir(output)
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
    assert results[0].manifest_path.startswith(str(appdata))
    assert resolve_state_db_path(config) == application_data_dir() / "forensicpack_state.db"
    assert resolve_state_db_path(config).is_file()


def test_metadata_workspace_is_stable_for_same_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORENSICPACK_APPDATA", str(tmp_path / "appdata"))
    first = metadata_output_dir(tmp_path / "destination")
    second = metadata_output_dir(tmp_path / "." / "destination")
    other = metadata_output_dir(tmp_path / "other-destination")

    assert first == second
    assert first != other
    assert first.parent == application_data_dir() / "Cases"


def test_legacy_metadata_folder_is_excluded_on_same_folder_rerun(tmp_path: Path) -> None:
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


def test_checksum_name_falls_back_to_absolute_path_across_windows_drives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "destination" / "Case Files.zip"
    checksum_dir = tmp_path / "appdata" / "Cases" / "destination-abc123"

    def fake_relpath(_target, _start):
        raise ValueError("path is on mount 'G:', start on mount 'C:'")

    monkeypatch.setattr("sidecars.os.path.relpath", fake_relpath)

    assert _checksum_name(target, checksum_dir) == target.as_posix()
