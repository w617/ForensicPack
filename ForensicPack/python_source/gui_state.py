import json
import os
import sys
import errno
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine import JobConfig


GUI_SETTINGS_SCHEMA_VERSION = 1

GUI_SETTINGS_DEFAULTS: dict[str, object] = {
    "schema_version": GUI_SETTINGS_SCHEMA_VERSION,
    "source_dir": "",
    "output_dir": "",
    "archive_fmt": "7z",
    "compress_level_label": "Normal (5)",
    "hash_algorithms": ["SHA256"],
    "threads": 4,
    "auto_threads": False,
    "split_enabled": False,
    "split_size_str": "4",
    "password": "",
    "delete_source": False,
    "skip_existing": False,
    "resume_enabled": False,
    "dry_run": False,
    "scan_mode": "deterministic",
    "archive_hash_mode": "always",
    "thread_strategy": "fixed",
    "progress_interval_ms": 200,
    "state_db_path": "",
    "report_json": False,
    "embed_manifest_in_archive": True,
    "use_metadata": False,
    "metadata_examiner": "",
    "metadata_case_id": "",
    "metadata_evidence_id": "",
    "metadata_notes": "",
    "hash_threads": 4,
}


@dataclass
class WidgetStateBinding:
    widget: Any
    enabled_state: str = "normal"
    disabled_state: str = "disabled"


@dataclass
class GuiSettings:
    schema_version: int = GUI_SETTINGS_SCHEMA_VERSION
    source_dir: str = ""
    output_dir: str = ""
    archive_fmt: str = "7z"
    compress_level_label: str = "Normal (5)"
    hash_algorithms: list[str] | None = None
    threads: int = 4
    auto_threads: bool = False
    split_enabled: bool = False
    split_size_str: str = "4"
    resume_enabled: bool = False
    dry_run: bool = False
    scan_mode: str = "deterministic"
    archive_hash_mode: str = "always"
    thread_strategy: str = "fixed"
    progress_interval_ms: int = 200
    state_db_path: str = ""
    report_json: bool = False
    embed_manifest_in_archive: bool = True
    delete_source: bool = False
    skip_existing: bool = False
    use_metadata: bool = False
    metadata_examiner: str = ""
    metadata_case_id: str = ""
    metadata_evidence_id: str = ""
    metadata_notes: str = ""
    hash_threads: int = 4


def settings_path() -> Path:
    if os.name == "nt":
        appdata = os.getenv("APPDATA")
        base = Path(appdata) if appdata else (Path.home() / "AppData" / "Roaming")
        return base / "ForensicPack" / "gui_settings.json"
    return Path.home() / ".config" / "forensicpack" / "gui_settings.json"


def _upgrade_settings(raw: dict[str, object]) -> dict[str, object]:
    merged = dict(GUI_SETTINGS_DEFAULTS)
    for key, value in raw.items():
        if key in merged:
            merged[key] = value
    merged["schema_version"] = GUI_SETTINGS_SCHEMA_VERSION
    # Password is never restored from saved settings.
    merged["password"] = ""
    return merged


def load_gui_settings(path: Path | None = None) -> dict[str, object]:
    target = path or settings_path()
    if not target.exists():
        return dict(GUI_SETTINGS_DEFAULTS)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return dict(GUI_SETTINGS_DEFAULTS)
    except (OSError, json.JSONDecodeError):
        return dict(GUI_SETTINGS_DEFAULTS)
    return _upgrade_settings(raw)


def save_gui_settings(settings: dict[str, object], path: Path | None = None) -> Path:
    target = path or settings_path()
    merged = _upgrade_settings(settings)
    merged.pop("password", None)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return target


def requires_destructive_confirmation(config: JobConfig) -> bool:
    return bool(config.delete_source)


def validate_destructive_confirmation(text: str | None) -> bool:
    return (text or "").strip().upper() == "DELETE"


def build_run_summary(config: JobConfig) -> str:
    selected_line = "All direct children"
    if config.selected_item_names is not None:
        if not config.selected_item_names:
            selected_line = "None"
        elif len(config.selected_item_names) <= 3:
            selected_line = ", ".join(config.selected_item_names)
        else:
            preview = ", ".join(config.selected_item_names[:3])
            selected_line = f"{preview}, +{len(config.selected_item_names) - 3} more"
    lines = [
        "Session Summary",
        "-" * 40,
        f"Source          : {config.source_dir.resolve(strict=False)}",
        f"Output          : {config.output_dir.resolve(strict=False)}",
        f"Selected Items  : {selected_line}",
        f"Format          : {config.archive_fmt}",
        f"Compression     : {config.compress_level_label}",
        f"Hashes          : {', '.join(config.hash_algorithms) if config.hash_algorithms else 'None (disabled)'}",
        f"Hash Threads    : {config.hash_threads}",
        f"Threads         : {config.threads}",
        f"Thread Strategy : {config.thread_strategy}",
        f"Split Enabled   : {'Yes' if config.split_enabled else 'No'}",
        f"Split Size      : {config.split_size_str or 'N/A'}",
        f"Skip Existing   : {'Yes' if config.skip_existing else 'No'}",
        f"Delete Source   : {'Yes' if config.delete_source else 'No'}",
        f"Resume Enabled  : {'Yes' if config.resume_enabled else 'No'}",
        f"Dry Run         : {'Yes' if config.dry_run else 'No'}",
        f"Scan Mode       : {config.scan_mode}",
        f"Archive Hash    : {config.archive_hash_mode if config.hash_algorithms else 'N/A (no hashes selected)'}",
        f"Progress Every  : {config.progress_interval_ms} ms",
        f"JSON Report     : {'Yes' if config.report_json else 'No'}",
        f"Embed Manifest  : {'Yes' if config.embed_manifest_in_archive else 'No'}",
        f"State DB        : {config.state_db_path or '(default)'}",
    ]
    if config.case_metadata and any(config.case_metadata.values()):
        lines.append("-" * 40)
        lines.append("Metadata")
        for key, value in config.case_metadata.items():
            if value:
                lines.append(f"{key:<15}: {value}")
    return "\n".join(lines)


def matches_queue_filter(state: str, selected_filter: str) -> bool:
    normalized = state.lower()
    if selected_filter == "All":
        return True
    if selected_filter == "Running":
        return normalized == "running"
    if selected_filter == "Done":
        return normalized == "done"
    if selected_filter == "Failed":
        return normalized in {"error", "failed"}
    if selected_filter == "Skipped":
        return normalized in {"skipped", "cancelled"}
    return True


def queue_filter_counts(states: list[str]) -> dict[str, int]:
    lowered = [state.lower() for state in states]
    return {
        "All": len(states),
        "Running": sum(1 for state in lowered if state == "running"),
        "Done": sum(1 for state in lowered if state == "done"),
        "Failed": sum(1 for state in lowered if state in {"error", "failed"}),
        "Skipped": sum(1 for state in lowered if state in {"skipped", "cancelled"}),
    }


def estimate_eta_seconds(elapsed_seconds: float, completed: int, total: int) -> float | None:
    if completed <= 0 or total <= 0 or completed >= total:
        return None
    remaining = total - completed
    rate = elapsed_seconds / completed
    return max(0.0, remaining * rate)


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, sec = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def apply_widget_bindings(bindings: list[WidgetStateBinding], enabled: bool) -> None:
    for binding in bindings:
        state = binding.enabled_state if enabled else binding.disabled_state
        widget = binding.widget
        try:
            widget.configure(state=state)
        except Exception:
            try:
                widget.config(state=state)
            except Exception:
                pass


def quote_windows_arg(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def build_windows_elevation_command(module_file: str) -> tuple[str, str]:
    if getattr(sys, "frozen", False):
        return sys.executable, ""

    script_path: Path | None = None
    if sys.argv and sys.argv[0]:
        argv0 = Path(sys.argv[0]).expanduser()
        if argv0.exists():
            script_path = argv0.resolve()
    if script_path is None:
        script_path = (Path(module_file).resolve().parents[1] / "gui.py").resolve()
    params = f"{quote_windows_arg(str(script_path))} gui"
    return sys.executable, params


def is_permission_error(exc: Exception) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError) and exc.errno in {errno.EACCES, errno.EPERM}:
        return True
    text = str(exc).lower()
    return "permission denied" in text or "access is denied" in text
