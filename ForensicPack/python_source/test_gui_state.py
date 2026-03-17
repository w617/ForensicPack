import json

from engine import JobConfig
from gui_state import (
    GUI_SETTINGS_DEFAULTS,
    GUI_SETTINGS_SCHEMA_VERSION,
    WidgetStateBinding,
    apply_widget_bindings,
    build_run_summary,
    estimate_eta_seconds,
    load_gui_settings,
    matches_queue_filter,
    queue_filter_counts,
    requires_destructive_confirmation,
    save_gui_settings,
    validate_destructive_confirmation,
)


def _base_config(tmp_path, *, delete_source=False):
    source = tmp_path / "source"
    output = tmp_path / "output"
    source.mkdir(parents=True)
    output.mkdir(parents=True)
    return JobConfig(
        source_dir=source,
        output_dir=output,
        archive_fmt="ZIP",
        compress_level_label="Normal (5)",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=["SHA256"],
        password=None,
        delete_source=delete_source,
        skip_existing=False,
        threads=2,
        scan_mode="deterministic",
        archive_hash_mode="always",
        thread_strategy="fixed",
        progress_interval_ms=200,
    )


def test_load_gui_settings_missing_file_fallback(tmp_path):
    settings = load_gui_settings(tmp_path / "does_not_exist.json")
    assert settings == GUI_SETTINGS_DEFAULTS


def test_save_and_load_gui_settings_roundtrip_and_password_exclusion(tmp_path):
    target = tmp_path / "gui_settings.json"
    payload = {
        "source_dir": "/tmp/src",
        "output_dir": "/tmp/out",
        "archive_fmt": "ZIP",
        "hash_algorithms": ["SHA256", "SHA512"],
        "password": "secret",
        "resume_enabled": True,
    }
    save_gui_settings(payload, target)
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert "password" not in raw
    loaded = load_gui_settings(target)
    assert loaded["source_dir"] == "/tmp/src"
    assert loaded["resume_enabled"] is True
    assert loaded["password"] == ""


def test_schema_upgrade_fallback(tmp_path):
    target = tmp_path / "legacy.json"
    target.write_text(
        json.dumps(
            {
                "schema_version": 0,
                "source_dir": "/legacy/source",
                "unknown_field": "ignored",
                "password": "legacy-secret",
            }
        ),
        encoding="utf-8",
    )
    loaded = load_gui_settings(target)
    assert loaded["schema_version"] == GUI_SETTINGS_SCHEMA_VERSION
    assert loaded["source_dir"] == "/legacy/source"
    assert "unknown_field" not in loaded
    assert loaded["password"] == ""


def test_destructive_confirmation_helpers(tmp_path):
    safe_config = _base_config(tmp_path / "safe", delete_source=False)
    destructive_config = _base_config(tmp_path / "destructive", delete_source=True)
    assert requires_destructive_confirmation(safe_config) is False
    assert requires_destructive_confirmation(destructive_config) is True
    assert validate_destructive_confirmation("DELETE") is True
    assert validate_destructive_confirmation(" delete ") is True
    assert validate_destructive_confirmation("nope") is False


def test_build_run_summary_reflects_config(tmp_path):
    config = _base_config(tmp_path)
    config.scan_mode = "fast"
    config.archive_hash_mode = "skip"
    config.thread_strategy = "auto"
    config.resume_enabled = True
    config.report_json = True
    config.embed_manifest_in_archive = False
    summary = build_run_summary(config)
    assert "Scan Mode       : fast" in summary
    assert "Archive Hash    : skip" in summary
    assert "Thread Strategy : auto" in summary
    assert "Resume Enabled  : Yes" in summary
    assert "JSON Report     : Yes" in summary
    assert "Embed Manifest  : No" in summary


def test_build_run_summary_marks_archive_hash_na_when_hashes_disabled(tmp_path):
    config = _base_config(tmp_path)
    config.hash_algorithms = []
    config.archive_hash_mode = "skip"
    summary = build_run_summary(config)
    assert "Hashes          : None (disabled)" in summary
    assert "Archive Hash    : N/A (no hashes selected)" in summary


class _DummyWidget:
    def __init__(self):
        self.state = "normal"

    def configure(self, *, state):
        self.state = state


def test_apply_widget_bindings_disable_enable_roundtrip():
    normal = _DummyWidget()
    readonly = _DummyWidget()
    bindings = [
        WidgetStateBinding(widget=normal, enabled_state="normal", disabled_state="disabled"),
        WidgetStateBinding(widget=readonly, enabled_state="readonly", disabled_state="disabled"),
    ]
    apply_widget_bindings(bindings, enabled=False)
    assert normal.state == "disabled"
    assert readonly.state == "disabled"
    apply_widget_bindings(bindings, enabled=True)
    assert normal.state == "normal"
    assert readonly.state == "readonly"


def test_queue_filter_counts_and_matching():
    states = ["queued", "running", "done", "error", "skipped", "cancelled"]
    counts = queue_filter_counts(states)
    assert counts == {"All": 6, "Running": 1, "Done": 1, "Failed": 1, "Skipped": 2}
    assert matches_queue_filter("running", "Running") is True
    assert matches_queue_filter("done", "Failed") is False
    assert matches_queue_filter("cancelled", "Skipped") is True
    assert matches_queue_filter("queued", "All") is True


def test_eta_estimation_monotonic_and_zero_handling():
    eta_1 = estimate_eta_seconds(10.0, completed=1, total=5)
    eta_2 = estimate_eta_seconds(10.0, completed=2, total=5)
    assert eta_1 is not None and eta_2 is not None
    assert eta_2 <= eta_1
    assert estimate_eta_seconds(0.0, completed=0, total=5) is None
    assert estimate_eta_seconds(10.0, completed=5, total=5) is None
