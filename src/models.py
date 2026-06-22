import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal, TypeAlias


ResultStatus: TypeAlias = Literal["success", "warning", "failed", "skipped", "cancelled"]
ScanErrorMode: TypeAlias = Literal["strict", "best-effort"]


class JobCancelled(Exception):
    pass


class JobSkipped(Exception):
    pass


@dataclass(frozen=True)
class ScanIssue:
    path: str
    operation: str
    error_type: str
    message: str
    errno: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "operation": self.operation,
            "error_type": self.error_type,
            "message": self.message,
            "errno": self.errno,
        }


@dataclass(frozen=True)
class FileRecord:
    path: Path
    manifest_rel: str
    archive_rel: str
    size: int
    created_utc: str
    modified_utc: str
    accessed_utc: str = ""
    changed_utc: str = ""
    created_semantics: str = ""
    mode: str = ""
    attributes: str = ""
    is_symlink: bool = False
    inode: int | None = None
    device: int | None = None
    hardlink_count: int | None = None
    alternate_data_streams: tuple[str, ...] = ()

    def to_manifest_dict(self, hashes: dict[str, str] | None = None) -> dict[str, object]:
        return {
            "relative_path": self.manifest_rel,
            "archive_path": self.archive_rel,
            "size": self.size,
            "created_utc": self.created_utc,
            "modified_utc": self.modified_utc,
            "accessed_utc": self.accessed_utc,
            "changed_utc": self.changed_utc,
            "created_semantics": self.created_semantics,
            "mode": self.mode,
            "attributes": self.attributes,
            "is_symlink": self.is_symlink,
            "inode": self.inode,
            "device": self.device,
            "hardlink_count": self.hardlink_count,
            "alternate_data_streams": list(self.alternate_data_streams),
            "hashes": hashes or {},
        }


@dataclass
class ProgressEvent:
    job_id: int
    phase: str
    bytes_done: int
    bytes_total: int
    message: str = ""


@dataclass
class JobConfig:
    source_dir: Path
    output_dir: Path
    archive_fmt: str
    compress_level_label: str
    split_enabled: bool
    split_size_str: str
    hash_algorithms: list[str]
    password: str | None
    delete_source: bool
    skip_existing: bool
    case_metadata: dict[str, str] | None = None
    threads: int = 1
    scan_mode: Literal["deterministic", "fast"] = "deterministic"
    archive_hash_mode: Literal["always", "skip"] = "always"
    thread_strategy: Literal["fixed", "auto"] = "fixed"
    progress_interval_ms: int = 200
    resume_enabled: bool = False
    dry_run: bool = False
    state_db_path: Path | None = None
    report_json: bool = False
    report_pdf: bool = False
    embed_manifest_in_archive: bool = True
    retain_manifests: bool = True
    verify_member_hashes: bool = True
    scan_error_mode: ScanErrorMode = "best-effort"
    exclude_generated_outputs: bool = True
    fail_on_collision: bool = True
    preflight_space_check: bool = True
    audit_log: bool = True
    seven_zip_path: Path | None = None
    signing_key_path: Path | None = None
    signing_certificate_path: Path | None = None
    agency_logo_path: Path | None = None
    long_path_warning_threshold: int = 240
    resume_used: bool = False
    selected_item_names: list[str] | None = None
    hash_threads: int = 4
    excluded_generated_items: list[str] = field(default_factory=list)
    preflight_warnings: list[str] = field(default_factory=list)


@dataclass
class JobResult:
    case_name: str
    format: str
    start_time: str
    end_time: str
    file_count: int | str
    source_size: int | str
    archive_path: str
    archive_size: int | str
    verify: str
    status: ResultStatus | str = ""
    hashes: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    manifest_path: str = ""
    external_manifest_json: str = ""
    checksum_path: str = ""
    signature_path: str = ""
    audit_log_path: str = ""
    content_verify: str = "NOT RUN"
    archive_member_count: int = 0
    scan_issues: list[ScanIssue] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.status:
            self.status = infer_result_status(self.verify)

    @property
    def is_failure(self) -> bool:
        return self.status == "failed"

    @property
    def causes_nonzero_exit(self) -> bool:
        return self.status in {"failed", "cancelled"}

    def to_report_row(self) -> dict[str, object]:
        from engine import HASH_NAMES

        row = {
            "Case Name": self.case_name,
            "Format": self.format,
            "Status": self.status,
            "Start Time": self.start_time,
            "End Time": self.end_time,
            "File Count": self.file_count,
            "Source Size": self.source_size,
            "Archive Path": self.archive_path,
            "Archive Size": self.archive_size,
            "Verify": self.verify,
            "Content Verify": self.content_verify,
            "Archive Member Count": self.archive_member_count,
            "Manifest Path": self.manifest_path,
            "Manifest JSON": self.external_manifest_json,
            "Checksum Path": self.checksum_path,
            "Signature Path": self.signature_path,
            "Audit Log Path": self.audit_log_path,
            "Scan Issue Count": len(self.scan_issues),
            "Scan Issues": " | ".join(
                f"{issue.operation}: {issue.path}: {issue.message}" for issue in self.scan_issues
            ),
            "Elapsed Seconds": f"{self.elapsed_seconds:.3f}",
            "Warnings": " | ".join(self.warnings),
        }
        for alg in HASH_NAMES:
            row[alg] = self.hashes.get(alg, "")
        return row


def infer_result_status(verify: str) -> ResultStatus:
    normalized = (verify or "").strip().upper()
    if normalized.startswith("PASS"):
        if "WARNING" in normalized or "SOURCE RETAINED" in normalized:
            return "warning"
        return "success"
    if normalized.startswith("DRY-RUN"):
        return "success"
    if normalized.startswith("SKIPPED"):
        return "skipped"
    if normalized.startswith("CANCELLED"):
        return "cancelled"
    return "failed"


def summarize_job_results(results: list[JobResult]) -> dict[ResultStatus, int]:
    counts: dict[ResultStatus, int] = {
        "success": 0,
        "warning": 0,
        "failed": 0,
        "skipped": 0,
        "cancelled": 0,
    }
    for result in results:
        counts[result.status] += 1
    return counts


@dataclass
class RuntimeState:
    active_processes: dict[int, subprocess.Popen] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def set_process(self, job_id: int, proc: subprocess.Popen | None) -> None:
        with self.lock:
            if proc is None:
                self.active_processes.pop(job_id, None)
            else:
                self.active_processes[job_id] = proc

    def kill_process(self, job_id: int) -> None:
        with self.lock:
            proc = self.active_processes.get(job_id)
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass

    def kill_all(self) -> None:
        with self.lock:
            ids = list(self.active_processes)
        for job_id in ids:
            self.kill_process(job_id)


class CancellationToken:
    def __init__(self) -> None:
        self._cancel = threading.Event()
        self._skips: set[int] = set()
        self._lock = threading.Lock()
        self._cancel_callbacks: list[Callable[[], None]] = []

    def request_cancel(self) -> None:
        already_set = self._cancel.is_set()
        self._cancel.set()
        if already_set:
            return
        with self._lock:
            callbacks = list(self._cancel_callbacks)
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass

    def on_cancel(self, callback: Callable[[], None]) -> None:
        if self._cancel.is_set():
            callback()
            return
        with self._lock:
            self._cancel_callbacks.append(callback)

    def request_skip(self, job_id: int) -> None:
        with self._lock:
            self._skips.add(job_id)

    def clear_skip(self, job_id: int) -> None:
        with self._lock:
            self._skips.discard(job_id)

    def state_for(self, job_id: int) -> str | None:
        if self._cancel.is_set():
            return "cancel"
        with self._lock:
            if job_id in self._skips:
                return "skip"
        return None

    def raise_if_requested(self, job_id: int) -> None:
        state = self.state_for(job_id)
        if state == "cancel":
            raise JobCancelled()
        if state == "skip":
            raise JobSkipped()


@dataclass
class JobCallbacks:
    log_cb: Callable[[str, str | None], None]
    progress_overall_cb: Callable[[float], None]
    progress_case_cb: Callable[[float], None]
    status_cb: Callable[[str], None]
    queue_cb: Callable[[list[str]], None] | None = None
    item_status_cb: Callable[[int, str], None] | None = None
    item_progress_cb: Callable[[int, float, str], None] | None = None
    item_failure_cb: Callable[[int, str], None] | None = None
    verbose_cb: Callable[[str], None] | None = None
    progress_interval_ms: int = 200
    _progress_last_emit: dict[tuple[int, str], float] = field(default_factory=dict, init=False, repr=False)

    def emit_progress(self, event: ProgressEvent) -> None:
        import time

        now = time.monotonic()
        key = (event.job_id, event.phase)
        interval_seconds = max(0, self.progress_interval_ms) / 1000.0
        if interval_seconds > 0 and event.bytes_total > 0 and event.bytes_done < event.bytes_total:
            last = self._progress_last_emit.get(key)
            if last is not None and (now - last) < interval_seconds:
                return
        self._progress_last_emit[key] = now
        fraction = 0.0 if event.bytes_total <= 0 else min(1.0, event.bytes_done / event.bytes_total)
        self.progress_case_cb(fraction)
        if self.item_progress_cb:
            self.item_progress_cb(event.job_id, fraction, event.phase)
        if event.message:
            self.status_cb(event.message)
