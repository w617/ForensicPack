import hashlib
import json
import os
import platform
import socket
from pathlib import Path
from typing import Callable

from models import JobConfig

HASH_NAMES = ["MD5", "SHA1", "SHA256", "SHA512"]
ARCHIVE_FORMATS = ["7z", "ZIP", "TAR.GZ", "TAR.BZ2"]
COMPRESSION_LEVELS = {
    "Store (0)": "0",
    "Fastest (1)": "1",
    "Fast (3)": "3",
    "Normal (5)": "5",
    "Maximum (7)": "7",
    "Ultra (9)": "9",
}

ARCHIVE_HASH_MODES = ["always", "skip"]
SCAN_MODES = ["deterministic", "fast"]
THREAD_STRATEGIES = ["fixed", "auto"]

def normalize_hash_name(value: str) -> str:
    cleaned = "".join(ch for ch in value.upper() if ch.isalnum())
    mapping = {"MD5": "MD5", "SHA1": "SHA1", "SHA256": "SHA256", "SHA512": "SHA512"}
    if cleaned not in mapping:
        supported = ", ".join(HASH_NAMES)
        raise ValueError(f"Unsupported hash algorithm: {value}. Supported values: {supported}")
    return mapping[cleaned]

def normalize_hash_algorithms(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized_name = normalize_hash_name(value)
        if normalized_name not in seen:
            normalized.append(normalized_name)
            seen.add(normalized_name)
    return normalized

def safe_resolve(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)

def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False

def archive_suffix(fmt: str) -> str:
    if fmt == "7z":
        return ".7z"
    if fmt == "ZIP":
        return ".zip"
    if fmt == "TAR.GZ":
        return ".tar.gz"
    return ".tar.bz2"

def split_size_arg(enabled: bool, archive_fmt: str, split_size_str: str, log_cb: Callable[[str, str | None], None]) -> str | None:
    if not enabled or archive_fmt != "7z" or not split_size_str.strip():
        return None
    try:
        value = float(split_size_str.strip())
    except ValueError:
        log_cb("[WARN] Invalid split size; split disabled.", "#d29922")
        return None
    if value <= 0:
        log_cb("[WARN] Split size must be positive; split disabled.", "#d29922")
        return None
    if value >= 1:
        return f"{int(value)}g"
    return f"{max(1, int(value * 1024))}m"

def is_split_job(config: JobConfig) -> bool:
    return bool(split_size_arg(config.split_enabled, config.archive_fmt, config.split_size_str, lambda *_args: None))

def split_entry_path(base_archive: Path, config: JobConfig) -> Path:
    if config.archive_fmt == "7z" and is_split_job(config):
        return base_archive.with_name(f"{base_archive.name}.001")
    return base_archive

def split_output_parts(base_archive: Path, config: JobConfig) -> list[Path]:
    if config.archive_fmt == "7z" and is_split_job(config):
        return sorted(base_archive.parent.glob(f"{base_archive.name}.*"))
    return [base_archive] if base_archive.exists() else []

def output_size_bytes(base_archive: Path, config: JobConfig) -> int:
    parts = split_output_parts(base_archive, config)
    return sum(path.stat().st_size for path in parts if path.exists())

def redact_command(cmd: list[str]) -> str:
    redacted: list[str] = []
    for index, arg in enumerate(cmd):
        if arg.startswith("-p") and len(arg) > 2:
            redacted.append("-p***")
            continue
        if arg == "-p" and index + 1 < len(cmd):
            redacted.append("-p")
            continue
        if index > 0 and cmd[index - 1] == "-p":
            redacted.append("***")
            continue
        redacted.append(arg)
    return " ".join(redacted)

def resolve_state_db_path(config: JobConfig) -> Path:
    if config.state_db_path:
        return config.state_db_path
    return config.output_dir / "forensicpack_state.db"

def session_profile_key(config: JobConfig) -> str:
    payload = {
        "source": str(safe_resolve(config.source_dir)),
        "output": str(safe_resolve(config.output_dir)),
        "format": config.archive_fmt,
        "level": config.compress_level_label,
        "split": config.split_enabled,
        "split_size": config.split_size_str,
        "hashes": list(config.hash_algorithms),
        "scan_mode": config.scan_mode,
        "archive_hash_mode": config.archive_hash_mode,
        "thread_strategy": config.thread_strategy,
        "dry_run": config.dry_run,
    }
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def select_worker_count(config: JobConfig, items: list[Path]) -> int:
    requested = max(1, config.threads)
    if config.thread_strategy == "fixed":
        return min(requested, max(1, len(items)))
    cpu = os.cpu_count() or 1
    max_workers = min(max(1, len(items)), max(1, cpu))
    file_sizes = [path.stat().st_size for path in items if path.is_file()]
    avg_size = (sum(file_sizes) / len(file_sizes)) if file_sizes else 0
    if avg_size >= 512 * 1024 * 1024:
        return min(max_workers, 2)
    if len(items) >= max_workers * 2:
        return min(max_workers, 8)
    return min(max_workers, max(1, requested))

def system_info() -> dict[str, str]:
    return {
        "Hostname": socket.gethostname(),
        "OS": platform.platform(),
        "Timezone": time_zone_name(),
    }

def time_zone_name() -> str:
    import time
    tzname = getattr(time, "tzname", None)
    if tzname:
        return tzname[0]
    return "Unknown"

def expected_archive_path(item_path: Path, output_dir: Path, archive_fmt: str) -> Path:
    base_name = item_path.name
    return output_dir / f"{base_name}{archive_suffix(archive_fmt)}"

def rename_matching_outputs(temp_archive: Path, final_archive: Path) -> None:
    if temp_archive.exists():
        temp_archive.replace(final_archive)
    pattern = temp_archive.name + ".*"
    for path in temp_archive.parent.glob(pattern):
        suffix = path.name[len(temp_archive.name):]
        target = final_archive.parent / f"{final_archive.name}{suffix}"
        path.replace(target)

def cleanup_partial_outputs(temp_archive: Path) -> None:
    for path in [temp_archive, *temp_archive.parent.glob(temp_archive.name + ".*")]:
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass

def cleanup_cancel_artifacts(output_dir: Path) -> int:
    removed = 0
    patterns = ["*.partial", "*.partial.*", "tmp_*_manifest.txt"]
    for pattern in patterns:
        for path in output_dir.glob(pattern):
            if not path.is_file():
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed
