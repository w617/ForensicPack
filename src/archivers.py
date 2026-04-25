import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Callable

from models import CancellationToken, JobCallbacks, JobConfig, ProgressEvent, RuntimeState
from utils import split_size_arg, COMPRESSION_LEVELS

SEVENZIP_PATHS = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
]

def find_7zip() -> str | None:
    import os
    for path in SEVENZIP_PATHS:
        if os.path.isfile(path):
            return path
    return shutil.which("7z")

class _CancelableReader:
    def __init__(
        self,
        handle,
        token: CancellationToken,
        job_id: int,
        on_bytes_cb: Callable[[int], None] | None = None,
    ) -> None:
        self._handle = handle
        self._token = token
        self._job_id = job_id
        self._on_bytes_cb = on_bytes_cb

    def read(self, size: int = -1):
        self._token.raise_if_requested(self._job_id)
        chunk = self._handle.read(size)
        if chunk and self._on_bytes_cb:
            self._on_bytes_cb(len(chunk))
        return chunk

    def close(self) -> None:
        self._handle.close()

    def __getattr__(self, name: str):
        return getattr(self._handle, name)

def _run_7zip(job_id: int, args: list[str], token: CancellationToken, runtime: RuntimeState, callbacks: JobCallbacks) -> bool:
    from utils import redact_command
    exe = find_7zip()
    if not exe:
        callbacks.log_cb("  [ERROR] 7-Zip not found. Install from https://www.7-zip.org/", "#f85149")
        return False
    cmd = [exe] + args
    callbacks.log_cb(f"  [CMD] {redact_command(cmd)}", "#8b949e")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    runtime.set_process(job_id, proc)
    try:
        if proc.stdout is None:
            raise RuntimeError("subprocess stdout is None; cannot read 7z output.")
        output_lines: list[str] = []
        for line in proc.stdout:
            token.raise_if_requested(job_id)
            line = line.rstrip()
            if line:
                output_lines.append(line)
                if callbacks.verbose_cb:
                    callbacks.verbose_cb(line)
        proc.wait()
        if proc.returncode != 0:
            for ln in output_lines[-15:]:
                callbacks.log_cb(f"  [7z] {ln}", "#f85149")
            return False
        return True
    finally:
        runtime.set_process(job_id, None)

def create_archive(
    job_id: int, 
    item_path: Path, 
    inventory: list, 
    manifest_path: Path, 
    config: JobConfig,
    token: CancellationToken, 
    runtime: RuntimeState, 
    callbacks: JobCallbacks, 
    temp_archive: Path
) -> Path:
    level = COMPRESSION_LEVELS.get(config.compress_level_label, "5")
    split_arg = split_size_arg(config.split_enabled, config.archive_fmt, config.split_size_str, callbacks.log_cb)
    callbacks.emit_progress(ProgressEvent(job_id, "archive", 0, max(sum(r.size for r in inventory), 1), f"Archiving {item_path.name}"))

    if config.archive_fmt == "7z":
        args = ["a", f"-mx={level}", "-mmt=on"]
        if config.password:
            args += [f"-p{config.password}", "-mhe=on"]
        if split_arg:
            args.append(f"-v{split_arg}")
        args += [str(temp_archive), str(item_path)]
        if not _run_7zip(job_id, args, token, runtime, callbacks):
            raise RuntimeError("7z archive creation failed.")
        if config.embed_manifest_in_archive and manifest_path.exists():
            callbacks.log_cb("  Embedding manifest inside archive ...", "#8b949e")
            if not _run_7zip(job_id, ["a", str(temp_archive), str(manifest_path)], token, runtime, callbacks):
                raise RuntimeError("Failed to embed manifest into 7z archive.")
        callbacks.emit_progress(ProgressEvent(job_id, "archive", 1, 1, f"Archived {item_path.name}"))
        return temp_archive

    if config.archive_fmt == "ZIP":
        compress_level = max(1, min(9, int(level))) if level != "0" else 0
        compression = zipfile.ZIP_STORED if level == "0" else zipfile.ZIP_DEFLATED
        total_bytes = max(sum(r.size for r in inventory), 1)
        bytes_done = 0
        with zipfile.ZipFile(
            temp_archive,
            "w",
            compression=compression,
            compresslevel=compress_level if level != "0" else None,
            allowZip64=True,
            strict_timestamps=False,
        ) as zf:
            for record in inventory:
                token.raise_if_requested(job_id)
                with record.path.open("rb") as source_fh:
                    with zf.open(record.archive_rel, "w", force_zip64=True) as archive_fh:
                        while True:
                            token.raise_if_requested(job_id)
                            chunk = source_fh.read(1 << 20)
                            if not chunk:
                                break
                            archive_fh.write(chunk)
                            bytes_done += len(chunk)
                            callbacks.emit_progress(ProgressEvent(job_id, "archive", min(bytes_done, total_bytes), total_bytes, f"Archiving {item_path.name}"))
                if callbacks.verbose_cb:
                    callbacks.verbose_cb(f"[ZIP] {record.archive_rel} ({record.size:,} B)")
            if config.embed_manifest_in_archive:
                zf.write(manifest_path, manifest_path.name)
        return temp_archive

    mode = "w:gz" if config.archive_fmt == "TAR.GZ" else "w:bz2"
    level_value = int(level) if level != "0" else 1
    total_bytes = max(sum(r.size for r in inventory), 1)
    bytes_done = 0
    with tarfile.open(temp_archive, mode, compresslevel=level_value) as tf:
        for record in inventory:
            token.raise_if_requested(job_id)
            with record.path.open("rb") as source_fh:
                tar_info = tf.gettarinfo(str(record.path), arcname=record.archive_rel)

                def _on_tar_bytes(count: int) -> None:
                    nonlocal bytes_done
                    bytes_done += count
                    callbacks.emit_progress(ProgressEvent(job_id, "archive", min(bytes_done, total_bytes), total_bytes, f"Archiving {item_path.name}"))

                tf.addfile(
                    tar_info,
                    fileobj=_CancelableReader(source_fh, token, job_id, on_bytes_cb=_on_tar_bytes),
                )
            if callbacks.verbose_cb:
                callbacks.verbose_cb(f"[TAR] {record.archive_rel} ({record.size:,} B)")
        if config.embed_manifest_in_archive:
            tf.add(manifest_path, arcname=manifest_path.name)
    return temp_archive

def verify_archive(archive_path: Path, archive_fmt: str, callbacks: JobCallbacks, job_id: int | None = None,
                   token: CancellationToken | None = None) -> bool:
    callbacks.log_cb(f"  Verifying integrity of {archive_path.name} ...", "#8b949e")
    if archive_fmt == "7z":
        if token is None or job_id is None:
            raise ValueError("7z verification requires job context.")
        return _run_7zip(job_id, ["t", str(archive_path)], token, RuntimeState(), callbacks)
    if archive_fmt == "ZIP":
        with zipfile.ZipFile(archive_path, "r") as zf:
            bad = zf.testzip()
            if bad:
                callbacks.log_cb(f"  [ERROR] First bad file in ZIP: {bad}", "#f85149")
                return False
        callbacks.log_cb("  ZIP integrity test: PASSED", "#3fb950")
        return True
    mode = "r:gz" if archive_fmt == "TAR.GZ" else "r:bz2"
    try:
        with tarfile.open(archive_path, mode) as tf:
            for member in tf.getmembers():
                if member.isfile():
                    extracted = tf.extractfile(member)
                    if extracted is None:
                        return False
                    while True:
                        chunk = extracted.read(1 << 20)
                        if not chunk:
                            break
        callbacks.log_cb("  TAR integrity test: PASSED", "#3fb950")
        return True
    except (tarfile.TarError, EOFError, OSError) as exc:
        callbacks.log_cb(f"  [ERROR] TAR verification failed: {exc}", "#f85149")
        return False
