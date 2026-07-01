import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Callable

from models import CancellationToken, JobCallbacks, JobConfig, ProgressEvent, RuntimeState
from utils import COMPRESSION_LEVELS, split_size_arg

SEVENZIP_PATHS = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
]


def find_7zip(preferred: str | Path | None = None) -> str | None:
    candidates = [preferred, os.environ.get("FORENSICPACK_7ZIP"), *SEVENZIP_PATHS]
    for candidate in candidates:
        if candidate and os.path.isfile(str(candidate)):
            return str(candidate)
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


def _run_7zip(
    job_id: int,
    args: list[str],
    token: CancellationToken,
    runtime: RuntimeState,
    callbacks: JobCallbacks,
) -> bool:
    from utils import redact_command

    executable = find_7zip()
    if not executable:
        callbacks.log_cb("  [ERROR] 7-Zip not found. Configure its path or install 7-Zip.", "#f85149")
        return False
    command = [executable] + args
    callbacks.log_cb(f"  [CMD] {redact_command(command)}", "#8b949e")
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    runtime.set_process(job_id, process)
    try:
        if process.stdout is None:
            raise RuntimeError("subprocess stdout is unavailable; cannot read 7-Zip output.")
        output_lines: list[str] = []
        for line in process.stdout:
            token.raise_if_requested(job_id)
            text = line.rstrip()
            if text:
                output_lines.append(text)
                if callbacks.verbose_cb:
                    callbacks.verbose_cb(text)
        process.wait()
        if process.returncode != 0:
            for text in output_lines[-15:]:
                callbacks.log_cb(f"  [7z] {text}", "#f85149")
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
    temp_archive: Path,
) -> Path:
    level = COMPRESSION_LEVELS.get(config.compress_level_label, "5")
    split_arg = split_size_arg(
        config.split_enabled,
        config.archive_fmt,
        config.split_size_str,
        callbacks.log_cb,
    )
    total_bytes = max(sum(record.size for record in inventory), 1)
    callbacks.emit_progress(
        ProgressEvent(job_id, "archive", 0, total_bytes, f"Archiving {item_path.name}")
    )

    previous_7zip = os.environ.get("FORENSICPACK_7ZIP")
    if config.seven_zip_path:
        os.environ["FORENSICPACK_7ZIP"] = str(config.seven_zip_path)
    try:
        if config.archive_fmt == "7z":
            args = ["a", f"-mx={level}", "-mmt=on", "-snl-"]
            if config.password:
                args += [f"-p{config.password}", "-mhe=on"]
            if split_arg:
                args.append(f"-v{split_arg}")
            args += [str(temp_archive), str(item_path)]
            if not _run_7zip(job_id, args, token, runtime, callbacks):
                raise RuntimeError("7z archive creation failed.")
            if config.embed_manifest_in_archive and manifest_path.exists():
                callbacks.log_cb("  Embedding manifest inside archive ...", "#8b949e")
                if not _run_7zip(
                    job_id,
                    ["a", str(temp_archive), str(manifest_path)],
                    token,
                    runtime,
                    callbacks,
                ):
                    raise RuntimeError("Failed to embed manifest into 7z archive.")
            callbacks.emit_progress(ProgressEvent(job_id, "archive", 1, 1, f"Archived {item_path.name}"))
            return temp_archive

        if config.archive_fmt == "ZIP":
            compress_level = max(1, min(9, int(level))) if level != "0" else 0
            compression = zipfile.ZIP_STORED if level == "0" else zipfile.ZIP_DEFLATED
            bytes_done = 0
            with zipfile.ZipFile(
                temp_archive,
                "w",
                compression=compression,
                compresslevel=compress_level if level != "0" else None,
                allowZip64=True,
                strict_timestamps=False,
            ) as archive:
                for record in inventory:
                    token.raise_if_requested(job_id)
                    with record.path.open("rb") as source_handle:
                        with archive.open(record.archive_rel, "w", force_zip64=True) as archive_handle:
                            while True:
                                token.raise_if_requested(job_id)
                                chunk = source_handle.read(1 << 20)
                                if not chunk:
                                    break
                                archive_handle.write(chunk)
                                bytes_done += len(chunk)
                                callbacks.emit_progress(
                                    ProgressEvent(
                                        job_id,
                                        "archive",
                                        min(bytes_done, total_bytes),
                                        total_bytes,
                                        f"Archiving {item_path.name}",
                                    )
                                )
                    if callbacks.verbose_cb:
                        callbacks.verbose_cb(f"[ZIP] {record.archive_rel} ({record.size:,} B)")
                if config.embed_manifest_in_archive:
                    archive.write(manifest_path, manifest_path.name)
            return temp_archive

        mode = "w:gz" if config.archive_fmt == "TAR.GZ" else "w:bz2"
        level_value = int(level) if level != "0" else 1
        bytes_done = 0
        with tarfile.open(temp_archive, mode, compresslevel=level_value) as archive:
            for record in inventory:
                token.raise_if_requested(job_id)
                with record.path.open("rb") as source_handle:
                    info = archive.gettarinfo(str(record.path), arcname=record.archive_rel)

                    def on_tar_bytes(count: int) -> None:
                        nonlocal bytes_done
                        bytes_done += count
                        callbacks.emit_progress(
                            ProgressEvent(
                                job_id,
                                "archive",
                                min(bytes_done, total_bytes),
                                total_bytes,
                                f"Archiving {item_path.name}",
                            )
                        )

                    archive.addfile(
                        info,
                        fileobj=_CancelableReader(source_handle, token, job_id, on_bytes_cb=on_tar_bytes),
                    )
                if callbacks.verbose_cb:
                    callbacks.verbose_cb(f"[TAR] {record.archive_rel} ({record.size:,} B)")
            if config.embed_manifest_in_archive:
                archive.add(manifest_path, arcname=manifest_path.name)
        return temp_archive
    finally:
        if config.seven_zip_path:
            if previous_7zip is None:
                os.environ.pop("FORENSICPACK_7ZIP", None)
            else:
                os.environ["FORENSICPACK_7ZIP"] = previous_7zip


def verify_archive(
    archive_path: Path,
    archive_fmt: str,
    callbacks: JobCallbacks,
    job_id: int | None = None,
    token: CancellationToken | None = None,
    seven_zip_path: str | Path | None = None,
) -> bool:
    callbacks.log_cb(f"  Verifying integrity of {archive_path.name} ...", "#8b949e")
    if archive_fmt == "7z":
        if token is None or job_id is None:
            raise ValueError("7z verification requires job context.")
        previous_7zip = os.environ.get("FORENSICPACK_7ZIP")
        if seven_zip_path:
            os.environ["FORENSICPACK_7ZIP"] = str(seven_zip_path)
        try:
            return _run_7zip(job_id, ["t", str(archive_path)], token, RuntimeState(), callbacks)
        finally:
            if seven_zip_path:
                if previous_7zip is None:
                    os.environ.pop("FORENSICPACK_7ZIP", None)
                else:
                    os.environ["FORENSICPACK_7ZIP"] = previous_7zip
    if archive_fmt == "ZIP":
        with zipfile.ZipFile(archive_path, "r") as archive:
            bad_member = archive.testzip()
            if bad_member:
                callbacks.log_cb(f"  [ERROR] First bad file in ZIP: {bad_member}", "#f85149")
                return False
        callbacks.log_cb("  ZIP integrity test: PASSED", "#3fb950")
        return True
    mode = "r:gz" if archive_fmt == "TAR.GZ" else "r:bz2"
    try:
        with tarfile.open(archive_path, mode) as archive:
            for member in archive.getmembers():
                if member.isfile():
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        return False
                    with extracted:
                        while extracted.read(1 << 20):
                            if token is not None and job_id is not None:
                                token.raise_if_requested(job_id)
        callbacks.log_cb("  TAR integrity test: PASSED", "#3fb950")
        return True
    except (tarfile.TarError, EOFError, OSError) as exc:
        callbacks.log_cb(f"  [ERROR] TAR verification failed: {exc}", "#f85149")
        return False
