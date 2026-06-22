from pathlib import Path

import archivers as _archivers
import core_v2 as _core
import forensic_inventory as _inventory
import hashing as _hashing
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
    _core.create_archive = create_archive
    _core.verify_archive = verify_archive
    _core.build_forensic_inventory = build_inventory
    original_run_7zip = _archivers._run_7zip
    _archivers._run_7zip = _run_7zip
    try:
        return _CORE_RUN_SESSION(config, callbacks, token)
    finally:
        _archivers._run_7zip = original_run_7zip


def process_cases(*args, **kwargs):
    original_run_session = _core.run_session
    _core.run_session = run_session
    try:
        return _core.process_cases(*args, **kwargs)
    finally:
        _core.run_session = original_run_session


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
