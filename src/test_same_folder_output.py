from pathlib import Path

import pytest

from engine import CancellationToken, JobCallbacks, JobConfig, run_session


def _callbacks() -> JobCallbacks:
    return JobCallbacks(
        log_cb=lambda _msg, _colour=None: None,
        progress_overall_cb=lambda _fraction: None,
        progress_case_cb=lambda _fraction: None,
        status_cb=lambda _text: None,
    )


def test_source_and_output_can_use_same_folder(tmp_path: Path) -> None:
    source = tmp_path / "evidence"
    source.mkdir()
    (source / "document.txt").write_text("evidence", encoding="utf-8")
    case_dir = source / "caseA"
    case_dir.mkdir()
    (case_dir / "notes.txt").write_text("notes", encoding="utf-8")

    config = JobConfig(
        source_dir=source,
        output_dir=source,
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
    assert (source / "document.txt.zip").is_file()
    assert (source / "caseA.zip").is_file()
    assert not any(path.name.endswith(".partial") for path in source.iterdir())


def test_nested_output_folder_remains_blocked(tmp_path: Path) -> None:
    source = tmp_path / "evidence"
    source.mkdir()
    (source / "document.txt").write_text("evidence", encoding="utf-8")

    config = JobConfig(
        source_dir=source,
        output_dir=source / "output",
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

    with pytest.raises(ValueError, match="Output directory cannot be inside source directory"):
        run_session(config, _callbacks(), CancellationToken())
