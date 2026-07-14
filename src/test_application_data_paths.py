from pathlib import Path

import pytest

from models import JobConfig
from utils import application_data_dir, metadata_output_dir, resolve_state_db_path


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
        "preflight_space_check": False,
    }
    values.update(overrides)
    return JobConfig(**values)


def test_application_data_override_controls_metadata_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    appdata = tmp_path / "private-appdata"
    monkeypatch.setenv("FORENSICPACK_APPDATA", str(appdata))
    source = tmp_path / "source"
    output = tmp_path / "destination"
    config = config_for(source, output)

    assert application_data_dir() == appdata
    assert metadata_output_dir(output).parent == appdata / "Cases"
    assert resolve_state_db_path(config) == appdata / "forensicpack_state.db"


def test_explicit_state_database_path_still_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORENSICPACK_APPDATA", str(tmp_path / "appdata"))
    custom = tmp_path / "custom" / "resume.sqlite"
    config = config_for(tmp_path / "source", tmp_path / "output", state_db_path=custom)

    assert resolve_state_db_path(config) == custom


def test_metadata_workspace_name_does_not_expose_full_destination_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORENSICPACK_APPDATA", str(tmp_path / "appdata"))
    output = tmp_path / "Sensitive Case Name" / "Delivery Folder"
    workspace = metadata_output_dir(output)

    assert workspace.parent == application_data_dir() / "Cases"
    assert workspace.name.startswith("Delivery-Folder-")
    assert str(tmp_path) not in workspace.name
