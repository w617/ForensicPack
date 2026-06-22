import threading
import time
from pathlib import Path
from typing import Callable, Literal

import archivers as _archivers
import core_v2 as _core
import forensic_inventory as _inventory
import utils as _utils
from core_v2 import run_verify_session
from hashing import hash_file
from models import (
    CancellationToken,
    FileRecord,
    JobCallbacks,
    JobCancelled,
    JobConfig,
    JobResult,
    JobSkipped,
    ProgressEvent,
    RuntimeState,
    ScanIssue,
)
from state_db import StateStore, open_state_store
from utils import (
    ARCHIVE_FORMATS,
    ARCHIVE_HASH_MODES,
    COMPRESSION_LEVELS,
    HASH_NAMES,
    SCAN_MODES,
    THREAD_STRATEGIES,
    normalize_hash_algorithms,
    safe_resolve,
)
from version import APP_AUTHOR, APP_NAME, APP_VERSION

_CORE_RUN_SESSION = _core.run_session


def _run_7zip(
    job_id: int,
    args: list[str],
    token: CancellationToken,
    runtime: RuntimeState,
    callbacks: JobCallbacks,
) -> bool:
    return _archivers._run_7zip(job_id, args, token, runtime, callbacks)


def _redact_command(command: list[str]) -> str:
    return _utils.redact_command(command)


def _cleanup_cancel_artifacts(output_dir: Path) -> int:
    return _utils.cleanup_cancel_artifacts(Path(output_dir))


def build_inventory(
    item_path: Path,
    job_id: int,
    token: CancellationToken,
    callbacks: JobCallbacks,
    scan_mode: str = "deterministic",
    scan_error_mode: str | None = None,
):
    records, total_size, issues = _inventory.build_forensic_inventory(
        item_path,
        job_id,
        token,
        callbacks,
        scan_mode=scan_mode,
        scan_error_mode=scan_error_mode or "best-effort",
    )
    if scan_error_mode is None:
        return records, total_size
    return records, total_size, issues


def create_archive(
    job_id: int,
    item_path: Path,
    inventory: list,
    manifest_path: Path,
    config: JobConfig,
    token: CancellationToken,
    runtime: RuntimeState,
    callbacks: JobCallbacks,
    temp_archive: Path,
) -> Path:
    original_run_7zip = _archivers._run_7zip
    _archivers._run_7zip = _run_7zip
    try:
        return _archivers.create_archive(
            job_id,
            item_path,
            inventory,
            manifest_path,
            config,
            token,
            runtime,
            callbacks,
            temp_archive,
        )
    finally:
        _archivers._run_7zip = original_run_7zip


def verify_archive(
    archive_path: Path,
    archive_fmt: str,
    callbacks: JobCallbacks,
    job_id: int | None = None,
    token: CancellationToken | None = None,
) -> bool:
    original_run_7zip = _archivers._run_7zip
    _archivers._run_7zip = _run_7zip
    try:
        return _archivers.verify_archive(
            archive_path,
            archive_fmt,
            callbacks,
            job_id=job_id,
            token=token,
        )
    finally:
        _archivers._run_7zip = original_run_7zip


def run_session(
    config: JobConfig,
    callbacks: JobCallbacks,
    token: CancellationToken | None = None,
) -> list[JobResult]:
    source = safe_resolve(config.source_dir)
    output = safe_resolve(config.output_dir)
    if source == output and config.source_dir.is_dir() and not any(config.source_dir.iterdir()):
        raise ValueError("Source and output directories must be different when the shared source folder is empty.")

    explicit_no_hash = not config.hash_algorithms
    original_member_verify = config.verify_member_hashes
    if explicit_no_hash:
        config.verify_member_hashes = False

    _core.create_archive = create_archive
    _core.verify_archive = verify_archive
    _core.build_forensic_inventory = build_inventory
    original_run_7zip = _archivers._run_7zip
    _archivers._run_7zip = _run_7zip
    try:
        results = _CORE_RUN_SESSION(config, callbacks, token)
    finally:
        _archivers._run_7zip = original_run_7zip
        config.verify_member_hashes = original_member_verify

    for result in results:
        if "SOURCE RETAINED" in result.verify.upper():
            result.verify = "PASS (SOURCE RETAINED)"
        if explicit_no_hash:
            result.warnings = [
                warning
                for warning in result.warnings
                if warning != "Archive member hash verification was disabled."
            ]
            if result.verify == "PASS WITH WARNINGS" and not result.warnings and not result.scan_issues:
                result.verify = "PASS"
                result.status = "success"
    return results


def process_cases(
    source_dir: str,
    output_dir: str,
    archive_fmt: str,
    compress_level_label: str,
    split_enabled: bool,
    split_size_str: str,
    hash_algorithms: list,
    password: str,
    delete_source: bool,
    skip_existing: bool = False,
    progress_overall_cb: Callable[[float], None] = lambda _fraction: None,
    progress_case_cb: Callable[[float], None] = lambda _fraction: None,
    log_cb: Callable[[str, str | None], None] = lambda _message, _colour=None: None,
    status_cb: Callable[[str], None] = lambda _text: None,
    cancel_flag: threading.Event | None = None,
    skip_current_flag: threading.Event | None = None,
    queue_cb: Callable[[list[str]], None] | None = None,
    item_status_cb: Callable[[int, str], None] | None = None,
    case_metadata: dict | None = None,
    threads: int = 1,
    item_progress_cb: Callable[[int, float, str], None] | None = None,
    item_failure_cb: Callable[[int, str], None] | None = None,
    scan_mode: Literal["deterministic", "fast"] = "deterministic",
    archive_hash_mode: Literal["always", "skip"] = "always",
    thread_strategy: Literal["fixed", "auto"] = "fixed",
    progress_interval_ms: int = 200,
    resume_enabled: bool = False,
    dry_run: bool = False,
    state_db_path: str | None = None,
    report_json: bool = False,
    embed_manifest_in_archive: bool = True,
) -> list[dict[str, object]]:
    config = JobConfig(
        source_dir=Path(source_dir),
        output_dir=Path(output_dir),
        archive_fmt=archive_fmt,
        compress_level_label=compress_level_label,
        split_enabled=split_enabled,
        split_size_str=split_size_str,
        hash_algorithms=list(hash_algorithms),
        password=password.strip() or None,
        delete_source=delete_source,
        skip_existing=skip_existing,
        case_metadata=case_metadata,
        threads=threads,
        scan_mode=scan_mode,
        archive_hash_mode=archive_hash_mode,
        thread_strategy=thread_strategy,
        progress_interval_ms=progress_interval_ms,
        resume_enabled=resume_enabled,
        dry_run=dry_run,
        state_db_path=Path(state_db_path) if state_db_path else None,
        report_json=report_json,
        embed_manifest_in_archive=embed_manifest_in_archive,
    )
    token = CancellationToken()
    running_jobs: set[int] = set()
    state_lock = threading.Lock()
    last_started_job_id: int | None = None

    def item_status_proxy(index: int, state: str) -> None:
        nonlocal last_started_job_id
        with state_lock:
            if state == "running":
                running_jobs.add(index)
                last_started_job_id = index
            elif state in {"done", "warning", "error", "skipped", "cancelled"}:
                running_jobs.discard(index)
        if item_status_cb:
            item_status_cb(index, state)

    def watch_flags() -> None:
        last_skip_state = False
        while not getattr(threading.current_thread(), "_stop_requested", False):
            if cancel_flag and cancel_flag.is_set():
                token.request_cancel()
                break
            if skip_current_flag:
                current_skip_state = skip_current_flag.is_set()
                if current_skip_state and not last_skip_state:
                    with state_lock:
                        target = max(running_jobs) if running_jobs else last_started_job_id
                    if target is not None:
                        token.request_skip(target)
                last_skip_state = current_skip_state
            time.sleep(0.05)

    watcher = None
    if cancel_flag or skip_current_flag:
        watcher = threading.Thread(target=watch_flags, daemon=True)
        watcher.start()

    callbacks = JobCallbacks(
        log_cb=log_cb,
        progress_overall_cb=progress_overall_cb,
        progress_case_cb=progress_case_cb,
        status_cb=status_cb,
        queue_cb=queue_cb,
        item_status_cb=item_status_proxy,
        item_progress_cb=item_progress_cb,
        item_failure_cb=item_failure_cb,
    )
    try:
        results = run_session(config, callbacks, token)
    finally:
        if watcher is not None:
            setattr(watcher, "_stop_requested", True)
            watcher.join(timeout=0.5)
    return [result.to_report_row() for result in results]


find_7zip = _archivers.find_7zip
validate_config = _core.validate_config


__all__ = [
    "APP_AUTHOR",
    "APP_NAME",
    "APP_VERSION",
    "ARCHIVE_FORMATS",
    "ARCHIVE_HASH_MODES",
    "COMPRESSION_LEVELS",
    "HASH_NAMES",
    "SCAN_MODES",
    "THREAD_STRATEGIES",
    "CancellationToken",
    "JobCallbacks",
    "JobConfig",
    "JobResult",
    "ProgressEvent",
    "RuntimeState",
    "FileRecord",
    "ScanIssue",
    "StateStore",
    "JobCancelled",
    "JobSkipped",
    "process_cases",
    "open_state_store",
    "run_verify_session",
    "run_session",
    "verify_archive",
    "hash_file",
    "normalize_hash_algorithms",
    "find_7zip",
    "create_archive",
    "build_inventory",
    "validate_config",
    "_run_7zip",
    "_redact_command",
    "_cleanup_cancel_artifacts",
]
