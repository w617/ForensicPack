import datetime as dt
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Literal

from models import FileRecord, ProgressEvent, CancellationToken, JobCallbacks

def utc_ts(value: float) -> str:
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def build_inventory(
    item_path: Path,
    job_id: int,
    token: CancellationToken,
    callbacks: JobCallbacks,
    scan_mode: Literal["deterministic", "fast"] = "deterministic",
) -> tuple[list[FileRecord], int]:
    callbacks.emit_progress(ProgressEvent(job_id, "scan", 0, 1, f"Scanning {item_path.name}"))
    files: list[FileRecord] = []
    total_size = 0
    
    if item_path.is_file():
        token.raise_if_requested(job_id)
        stat = item_path.stat()
        files.append(
            FileRecord(
                path=item_path,
                manifest_rel=item_path.name,
                archive_rel=item_path.name,
                size=stat.st_size,
                created_utc=utc_ts(stat.st_ctime),
                modified_utc=utc_ts(stat.st_mtime),
            )
        )
        total_size = stat.st_size
    else:
        root_name = item_path.name
        
        def _scan_dir(current_dir: str, rel_path: str):
            nonlocal total_size
            token.raise_if_requested(job_id)
            try:
                # Use os.scandir instead of os.walk for performance
                with os.scandir(current_dir) as it:
                    entries = list(it)
            except OSError:
                return

            if scan_mode == "deterministic":
                entries.sort(key=lambda e: e.name)

            for entry in entries:
                token.raise_if_requested(job_id)
                entry_rel = os.path.join(rel_path, entry.name) if rel_path else entry.name
                
                if entry.is_file(follow_symlinks=False):
                    stat = entry.stat(follow_symlinks=False)
                    files.append(
                        FileRecord(
                            path=Path(entry.path),
                            manifest_rel=entry_rel.replace("\\", "/"),
                            archive_rel=f"{root_name}/{entry_rel}".replace("\\", "/"),
                            size=stat.st_size,
                            created_utc=utc_ts(stat.st_ctime),
                            modified_utc=utc_ts(stat.st_mtime),
                        )
                    )
                    total_size += stat.st_size
                elif entry.is_dir(follow_symlinks=False):
                    _scan_dir(entry.path, entry_rel)

        _scan_dir(str(item_path), "")

    callbacks.emit_progress(ProgressEvent(job_id, "scan", total_size, max(total_size, 1), f"Scanned {item_path.name}"))
    return files, total_size

def hash_file(
    path: Path, 
    algorithms: list[str], 
    job_id: int | None = None, 
    token: CancellationToken | None = None,
    progress_cb: Callable[[int], None] | None = None
) -> dict[str, str]:
    hashers = {alg: hashlib.new(alg.lower().replace("-", "")) for alg in algorithms}
    buf_size = 1 << 22  # 4 MB — reduces syscall overhead on large files
    with path.open("rb") as fh:
        while True:
            if token is not None and job_id is not None:
                token.raise_if_requested(job_id)
            chunk = fh.read(buf_size)
            if not chunk:
                break
            for hasher in hashers.values():
                hasher.update(chunk)
            if progress_cb:
                progress_cb(len(chunk))
    return {alg: hashers[alg].hexdigest().upper() for alg in algorithms}

def pre_hash_files(
    inventory: list[FileRecord],
    algorithms: list[str],
    job_id: int,
    token: CancellationToken,
    callbacks: JobCallbacks,
    item_path: Path,
    hash_threads: int = 4,
) -> dict[Path, dict[str, str]]:
    """Hash all files in inventory concurrently.

    Returns a mapping of file path -> {algorithm -> hex digest}.
    Files are hashed in parallel using a thread pool; progress is reported
    atomically via a shared counter so the overall manifest progress bar
    advances smoothly regardless of which thread completes first.
    """
    if not algorithms or not inventory:
        return {}

    total_bytes = max(sum(r.size for r in inventory), 1)
    bytes_done_counter = [0]
    counter_lock = threading.Lock()
    results: dict[Path, dict[str, str]] = {}
    worker_count = max(1, min(hash_threads, len(inventory), os.cpu_count() or 4))

    def _hash_one(record: FileRecord) -> tuple[Path, dict[str, str]]:
        def _progress(inc: int) -> None:
            with counter_lock:
                bytes_done_counter[0] += inc
                done = bytes_done_counter[0]
            callbacks.emit_progress(
                ProgressEvent(job_id, "manifest", min(done, total_bytes), total_bytes, f"Hashing {item_path.name}")
            )
        hashes = hash_file(record.path, algorithms, job_id=job_id, token=token, progress_cb=_progress)
        return record.path, hashes

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(_hash_one, record): record for record in inventory}
        for future in as_completed(futures):
            # Propagate cancellation / skip exceptions from hash workers
            path, hashes = future.result()
            results[path] = hashes

    return results

def write_manifest(
    item_path: Path, 
    manifest_path: Path, 
    inventory: list[FileRecord], 
    algorithms: list[str], 
    case_metadata: dict[str, str] | None,
    job_id: int, 
    token: CancellationToken, 
    callbacks: JobCallbacks,
    hash_threads: int = 4,
) -> tuple[int, int]:
    """Write the manifest file.

    When hash algorithms are requested, all files are pre-hashed in parallel
    via *pre_hash_files* before the manifest is written sequentially. This
    means I/O wait is overlapped across files, significantly reducing total
    hashing time on multi-file directories — especially on SSDs and NAS shares.
    """
    total_bytes = max(sum(record.size for record in inventory), 1)
    source_size = 0

    # --- Parallel pre-hash phase ---
    file_hashes: dict[Path, dict[str, str]] = {}
    if algorithms:
        callbacks.emit_progress(ProgressEvent(job_id, "manifest", 0, total_bytes, f"Hashing {item_path.name}"))
        file_hashes = pre_hash_files(
            inventory, algorithms, job_id, token, callbacks, item_path, hash_threads=hash_threads
        )
        # Signal hashing done before we start writing
        callbacks.emit_progress(ProgressEvent(job_id, "manifest", total_bytes, total_bytes, f"Writing manifest {item_path.name}"))

    # --- Sequential manifest write phase ---
    with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("ForensicPack Manifest\n")
        handle.write(f"Case Name: {item_path.name}\n")
        handle.write(f"Generated: {dt.datetime.now().isoformat()}\n")
        handle.write(f"Hash Algorithms: {', '.join(algorithms) if algorithms else 'None (disabled)'}\n")
        if case_metadata:
            handle.write("-" * 30 + "\n")
            for key, value in case_metadata.items():
                if value:
                    handle.write(f"{key}: {value}\n")
        handle.write("=" * 120 + "\n\n")
        header = f"{'Relative Path':<50} {'Size':>12} {'Created (UTC)':<20} {'Modified (UTC)':<20}"
        for alg in algorithms:
            header += f"  {alg:<36}"
        handle.write(header + "\n")
        handle.write("-" * len(header) + "\n")

        for record in inventory:
            token.raise_if_requested(job_id)
            hashes = file_hashes.get(record.path, {})
            source_size += record.size
            row = f"{record.manifest_rel:<50} {record.size:>12,} {record.created_utc:<20} {record.modified_utc:<20}"
            for alg in algorithms:
                row += f"  {hashes.get(alg, ''):<36}"
            handle.write(row + "\n")
            if callbacks.verbose_cb:
                callbacks.verbose_cb(f"[HASH] {record.path.name} ({record.size:,} B)")

        handle.write(f"\nTotal Files : {len(inventory):,}\n")
        handle.write(f"Total Size  : {source_size:,} bytes ({source_size / (1024 ** 3):.4f} GB)\n")
    return len(inventory), source_size
