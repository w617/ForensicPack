import datetime as dt
import os
import stat as stat_module
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal

from hashing import hash_file
from models import CancellationToken, FileRecord, JobCallbacks, ProgressEvent, ScanErrorMode, ScanIssue


def utc_ts(value: float | None) -> str:
    if value is None:
        return ""
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f UTC")


def _extended_attributes(path: Path) -> tuple[str, ...]:
    list_xattr = getattr(os, "listxattr", None)
    if list_xattr is None:
        return ()
    try:
        names = list_xattr(path, follow_symlinks=False)
    except (OSError, TypeError):
        return ()
    return tuple(sorted(str(name) for name in names))


def _record(path: Path, manifest_rel: str, archive_rel: str, st: os.stat_result) -> FileRecord:
    birth_time = getattr(st, "st_birthtime", None)
    if birth_time is not None:
        created = utc_ts(birth_time)
        created_semantics = "filesystem birth/creation time"
        changed = utc_ts(st.st_ctime)
    elif os.name == "nt":
        created = utc_ts(st.st_ctime)
        created_semantics = "Windows creation time"
        changed = ""
    else:
        created = ""
        created_semantics = "not available; st_ctime is metadata-change time"
        changed = utc_ts(st.st_ctime)

    raw_attributes = getattr(st, "st_file_attributes", None)
    attributes = f"0x{raw_attributes:08X}" if isinstance(raw_attributes, int) else ""
    return FileRecord(
        path=path,
        manifest_rel=manifest_rel.replace("\\", "/"),
        archive_rel=archive_rel.replace("\\", "/"),
        size=st.st_size,
        created_utc=created,
        modified_utc=utc_ts(st.st_mtime),
        accessed_utc=utc_ts(st.st_atime),
        changed_utc=changed,
        created_semantics=created_semantics,
        mode=stat_module.filemode(st.st_mode),
        attributes=attributes,
        is_symlink=path.is_symlink(),
        inode=getattr(st, "st_ino", None),
        device=getattr(st, "st_dev", None),
        hardlink_count=getattr(st, "st_nlink", None),
        alternate_data_streams=_extended_attributes(path),
    )


def build_forensic_inventory(
    item_path: Path,
    job_id: int,
    token: CancellationToken,
    callbacks: JobCallbacks,
    scan_mode: Literal["deterministic", "fast"] = "deterministic",
    scan_error_mode: ScanErrorMode = "best-effort",
) -> tuple[list[FileRecord], int, list[ScanIssue]]:
    callbacks.emit_progress(ProgressEvent(job_id, "scan", 0, 1, f"Scanning {item_path.name}"))
    records: list[FileRecord] = []
    issues: list[ScanIssue] = []
    total_size = 0

    def capture_issue(path: Path | str, operation: str, exc: BaseException) -> None:
        issue = ScanIssue(
            path=str(path),
            operation=operation,
            error_type=type(exc).__name__,
            message=str(exc),
            errno=getattr(exc, "errno", None),
        )
        issues.append(issue)
        callbacks.log_cb(f"  [WARN] {operation} failed for {path}: {exc}", "#d29922")
        if scan_error_mode == "strict":
            raise RuntimeError(f"Strict scan failed during {operation} for {path}: {exc}") from exc

    def add_file(path: Path, manifest_rel: str, archive_rel: str) -> None:
        nonlocal total_size
        token.raise_if_requested(job_id)
        try:
            if path.is_symlink():
                raise OSError("Symbolic links/reparse targets are not followed during evidence packaging.")
            st = path.stat(follow_symlinks=False)
            record = _record(path, manifest_rel, archive_rel, st)
        except OSError as exc:
            capture_issue(path, "read metadata", exc)
            return
        records.append(record)
        total_size += record.size

    if item_path.is_file():
        add_file(item_path, item_path.name, item_path.name)
    else:
        root_name = item_path.name

        def scan_dir(current_dir: Path, relative_dir: str) -> None:
            token.raise_if_requested(job_id)
            try:
                with os.scandir(current_dir) as iterator:
                    entries = list(iterator)
            except OSError as exc:
                capture_issue(current_dir, "enumerate directory", exc)
                return

            if scan_mode == "deterministic":
                entries.sort(key=lambda entry: entry.name.casefold())

            for entry in entries:
                token.raise_if_requested(job_id)
                path = Path(entry.path)
                relative = os.path.join(relative_dir, entry.name) if relative_dir else entry.name
                try:
                    if entry.is_symlink():
                        raise OSError("Symbolic links/reparse targets are not followed during evidence packaging.")
                    if entry.is_file(follow_symlinks=False):
                        add_file(path, relative, f"{root_name}/{relative}")
                    elif entry.is_dir(follow_symlinks=False):
                        scan_dir(path, relative)
                except OSError as exc:
                    capture_issue(path, "classify entry", exc)

        scan_dir(item_path, "")

    callbacks.emit_progress(
        ProgressEvent(job_id, "scan", total_size, max(total_size, 1), f"Scanned {item_path.name}")
    )
    return records, total_size, issues


def hash_inventory(
    records: list[FileRecord],
    algorithms: list[str],
    job_id: int,
    token: CancellationToken,
    callbacks: JobCallbacks,
    item_path: Path,
    hash_threads: int,
) -> dict[Path, dict[str, str]]:
    if not algorithms or not records:
        return {}
    total_bytes = max(sum(record.size for record in records), 1)
    completed_bytes = [0]
    lock = threading.Lock()
    worker_count = max(1, min(hash_threads, len(records), os.cpu_count() or 4))
    results: dict[Path, dict[str, str]] = {}

    def hash_one(record: FileRecord) -> tuple[Path, dict[str, str]]:
        def progress(increment: int) -> None:
            with lock:
                completed_bytes[0] += increment
                done = completed_bytes[0]
            callbacks.emit_progress(
                ProgressEvent(job_id, "manifest", min(done, total_bytes), total_bytes, f"Hashing {item_path.name}")
            )

        return record.path, hash_file(
            record.path,
            algorithms,
            job_id=job_id,
            token=token,
            progress_cb=progress,
        )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(hash_one, record) for record in records]
        for future in as_completed(futures):
            path, hashes = future.result()
            results[path] = hashes
    return results


def write_forensic_manifest(
    item_path: Path,
    manifest_path: Path,
    records: list[FileRecord],
    algorithms: list[str],
    case_metadata: dict[str, str] | None,
    job_id: int,
    token: CancellationToken,
    callbacks: JobCallbacks,
    hash_threads: int,
    scan_issues: list[ScanIssue],
) -> tuple[int, int, dict[Path, dict[str, str]]]:
    total_bytes = max(sum(record.size for record in records), 1)
    file_hashes: dict[Path, dict[str, str]] = {}
    if algorithms:
        callbacks.emit_progress(ProgressEvent(job_id, "manifest", 0, total_bytes, f"Hashing {item_path.name}"))
        file_hashes = hash_inventory(
            records, algorithms, job_id, token, callbacks, item_path, hash_threads
        )

    with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("ForensicPack Forensic Manifest\n")
        handle.write(f"Case Name: {item_path.name}\n")
        handle.write(f"Generated UTC: {dt.datetime.now(dt.timezone.utc).isoformat()}\n")
        handle.write(f"Hash Algorithms: {', '.join(algorithms) if algorithms else 'None'}\n")
        if case_metadata:
            for key, value in case_metadata.items():
                if value:
                    handle.write(f"{key}: {value}\n")
        handle.write("=" * 100 + "\n")
        for record in records:
            token.raise_if_requested(job_id)
            hashes = file_hashes.get(record.path, {})
            handle.write(f"Path: {record.manifest_rel}\n")
            handle.write(f"Archive Path: {record.archive_rel}\n")
            handle.write(f"Size: {record.size}\n")
            handle.write(f"Created UTC: {record.created_utc or 'Unavailable'}\n")
            handle.write(f"Created Semantics: {record.created_semantics}\n")
            handle.write(f"Modified UTC: {record.modified_utc}\n")
            handle.write(f"Accessed UTC: {record.accessed_utc}\n")
            handle.write(f"Metadata Changed UTC: {record.changed_utc or 'Unavailable'}\n")
            handle.write(f"Mode: {record.mode}\n")
            handle.write(f"Attributes: {record.attributes or 'Unavailable'}\n")
            handle.write(f"Inode/File ID: {record.inode}\n")
            handle.write(f"Hard Links: {record.hardlink_count}\n")
            handle.write(
                "Extended Attributes/Streams: "
                + (", ".join(record.alternate_data_streams) if record.alternate_data_streams else "None detected")
                + "\n"
            )
            for algorithm in algorithms:
                handle.write(f"{algorithm}: {hashes.get(algorithm, '')}\n")
            handle.write("-" * 100 + "\n")

        if scan_issues:
            handle.write("\nSCAN ISSUES / OMITTED CONTENT\n")
            for issue in scan_issues:
                handle.write(
                    f"{issue.operation} | {issue.path} | {issue.error_type} | errno={issue.errno} | {issue.message}\n"
                )
        handle.write(f"\nTotal Files: {len(records)}\n")
        handle.write(f"Total Size: {sum(record.size for record in records)} bytes\n")
        handle.write(f"Scan Issues: {len(scan_issues)}\n")
    return len(records), sum(record.size for record in records), file_hashes
