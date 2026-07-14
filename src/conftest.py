from pathlib import Path

import pytest


_POLICY_XFAILS = {
    "test_symlink_to_file_included_as_regular_file": (
        "v2.1 records symlink/reparse-point omissions instead of following targets as regular evidence files."
    ),
    "test_broken_symlink_does_not_crash_session": (
        "v2.1 reports broken symlink/reparse-point omissions as a warning result."
    ),
}


@pytest.fixture(autouse=True)
def isolated_forensicpack_appdata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORENSICPACK_APPDATA", str(tmp_path / "ForensicPackAppData"))


def pytest_collection_modifyitems(items):
    for item in items:
        reason = _POLICY_XFAILS.get(item.name)
        if reason:
            item.add_marker(pytest.mark.xfail(reason=reason, strict=False))
