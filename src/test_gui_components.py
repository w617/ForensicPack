"""GUI component smoke tests.

These tests verify widget logic and state management without relying on a real
display.  A real Tk root is created and immediately withdrawn so no window
appears; tests are skipped when no display is available (headless CI).
"""
import sys
import pytest


def _require_display():
    try:
        import tkinter as tk
        root = tk.Tk()
        root.destroy()
    except Exception as exc:
        pytest.skip(f"No display available for GUI tests: {exc}")


@pytest.fixture(scope="module")
def app():
    _require_display()
    from gui_components.app import ForensicPackApp
    instance = ForensicPackApp()
    instance.withdraw()
    yield instance
    instance.destroy()


# ---------------------------------------------------------------------------
# Startup / initialisation
# ---------------------------------------------------------------------------

def test_app_creates_without_error(app):
    """ForensicPackApp must initialise without raising."""
    assert app is not None


def test_app_title_contains_version(app):
    from version import APP_NAME, APP_VERSION
    assert APP_VERSION in app.title()
    assert APP_NAME in app.title()


# ---------------------------------------------------------------------------
# Settings round-trip
# ---------------------------------------------------------------------------

def test_collect_settings_payload_has_required_keys(app):
    payload = app._collect_settings_payload()
    required = {
        "source_dir", "output_dir", "archive_fmt", "hash_algorithms",
        "threads", "split_enabled", "delete_source", "skip_existing",
    }
    assert required.issubset(payload.keys())


def test_apply_settings_to_controls_does_not_raise(app):
    settings = app._collect_settings_payload()
    app._apply_settings_to_controls(settings)


# ---------------------------------------------------------------------------
# Mode switching
# ---------------------------------------------------------------------------

def test_mode_switch_pack_to_verify(app):
    app._run_mode_var.set("verify")
    app._apply_mode_state()
    assert app._start_btn.cget("text") == "Start Verification"


def test_mode_switch_verify_to_pack(app):
    app._run_mode_var.set("pack")
    app._apply_mode_state()
    assert app._start_btn.cget("text") == "Start Processing"


# ---------------------------------------------------------------------------
# Queue panel
# ---------------------------------------------------------------------------

def test_build_queue_rows_populates_rows(app):
    app._build_queue_rows(["case_001", "case_002", "case_003"])
    assert len(app._queue_rows) == 3
    assert app._queue_rows[0]["name"] == "case_001"
    assert app._queue_rows[2]["name"] == "case_003"


def test_build_queue_rows_clears_previous(app):
    app._build_queue_rows(["alpha", "beta"])
    app._build_queue_rows(["only_one"])
    assert len(app._queue_rows) == 1
    assert app._queue_rows[0]["name"] == "only_one"


def test_queue_rows_initial_state_is_queued(app):
    app._build_queue_rows(["item_a", "item_b"])
    for row in app._queue_rows:
        assert row["state"] == "queued"
        assert row["start_time"] is None


def test_update_item_status_running_sets_start_time(app):
    import time
    app._build_queue_rows(["task"])
    before = time.monotonic()
    app._update_item_status(0, "running")
    after = time.monotonic()
    st = app._queue_rows[0]["start_time"]
    assert st is not None
    assert before <= st <= after


def test_update_item_status_done_clears_from_running_jobs(app):
    app._build_queue_rows(["task"])
    app._update_item_status(0, "running")
    assert 0 in app._running_jobs
    app._update_item_status(0, "done")
    assert 0 not in app._running_jobs


def test_queue_filter_all_shows_all_rows(app):
    app._build_queue_rows(["a", "b", "c"])
    app._set_queue_filter("All")
    visible = [r for r in app._queue_rows if r["frame"].winfo_ismapped()]
    assert len(visible) == 3


# ---------------------------------------------------------------------------
# Log panel
# ---------------------------------------------------------------------------

def test_log_write_and_clear(app):
    app._log_write("test message", "#ffffff")
    content = app._log.get("1.0", "end-1c")
    assert "test message" in content
    app._clear_log()
    assert app._log.get("1.0", "end-1c").strip() == ""


def test_save_log_reports_empty_on_blank_log(app, monkeypatch):
    app._clear_log()
    shown = []
    monkeypatch.setattr("tkinter.messagebox.showinfo", lambda *a, **kw: shown.append(a))
    app._save_log()
    assert any("empty" in str(m).lower() or "nothing" in str(m).lower() for m in shown)


# ---------------------------------------------------------------------------
# Diagnostic snapshot
# ---------------------------------------------------------------------------

def test_build_diagnostic_snapshot_contains_version(app):
    from version import APP_VERSION
    snapshot = app._build_diagnostic_snapshot()
    assert APP_VERSION in snapshot
