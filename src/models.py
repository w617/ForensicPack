import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal


class JobCancelled(Exception):
    pass


class JobSkipped(Exception):
    pass


@dataclass(frozen=True)
class FileRecord:
    path: Path
    manifest_rel: str
    archive_rel: str
    size: int
    created_utc: str
    modified_utc: str


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
    embed_manifest_in_archive: bool = True
    resume_used: bool = False
    selected_item_names: list[str] | None = None
    hash_threads: int = 4


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
    hashes: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    manifest_path: str = ""

    def to_report_row(self) -> dict[str, object]:
        from engine import HASH_NAMES
        row = {
            "Case Name": self.case_name,
            "Format": self.format,
            "Start Time": self.start_time,
            "End Time": self.end_time,
            "File Count": self.file_count,
            "Source Size": self.source_size,
            "Archive Path": self.archive_path,
            "Archive Size": self.archive_size,
            "Verify": self.verify,
            "Manifest Path": self.manifest_path,
            "Elapsed Seconds": f"{self.elapsed_seconds:.3f}",
            "Warnings": " | ".join(self.warnings),
        }
        for alg in HASH_NAMES:
            row[alg] = self.hashes.get(alg, "")
        return row


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
