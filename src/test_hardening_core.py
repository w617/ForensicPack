from pathlib import Path

import pytest

import engine
from models import CancellationToken, JobCallbacks, JobConfig
from sidecars import verify_checksum_file
from utils import metadata_output_dir


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


def test_same_folder_rerun_excludes_prior_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORENSICPACK_APPDATA", str(tmp_path / "appdata"))
    source = tmp_path / "evidence"
    source.mkdir()
    (source / "document.txt").write_text("evidence", encoding="utf-8")
    config = config_for(source, source)

    first = engine.run_session(config, callbacks(), CancellationToken())
    assert [result.case_name for result in first] == ["document.txt"]
    assert first[0].content_verify == "PASS"
    assert Path(first[0].external_manifest_json).is_file()
    assert Path(first[0].checksum_path).is_file()

    second = engine.run_session(config, callbacks(), CancellationToken())
    assert [result.case_name for result in second] == ["document.txt"]
    assert "document.txt.zip" in config.excluded_generated_items


def test_package_checksum_sidecar_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORENSICPACK_APPDATA", str(tmp_path / "appdata"))
    source = tmp_path / "source"
    source.mkdir()
    (source / "one.bin").write_bytes(b"one" * 4096)
    output = tmp_path / "output"
    result = engine.run_session(config_for(source, output), callbacks(), CancellationToken())[0]

    passed, issues = verify_checksum_file(Path(result.checksum_path))
    assert passed is True
    assert issues == []


def test_pdf_report_is_created_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORENSICPACK_APPDATA", str(tmp_path / "appdata"))
    source = tmp_path / "source"
    source.mkdir()
    (source / "evidence.txt").write_text("evidence", encoding="utf-8")
    output = tmp_path / "output"
    results = engine.run_session(
        config_for(source, output, report_pdf=True), callbacks(), CancellationToken()
    )
    assert results[0].verify == "PASS"
    assert not list(output.glob("ForensicPack_Report_*.pdf"))
    assert list(metadata_output_dir(output).glob("ForensicPack_Report_*.pdf"))
