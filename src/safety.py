import fnmatch
import os
import shutil
from pathlib import Path

from models import JobConfig
from utils import archive_suffix, safe_resolve


_GENERATED_PATTERNS = (
    "ForensicPack_Report_*.txt",
    "ForensicPack_Report_*.csv",
    "ForensicPack_Report_*.json",
    "ForensicPack_Report_*.pdf",
    "ForensicPack_Audit_*.jsonl",
    "forensicpack_state.db",
    "forensicpack_state.db-wal",
    "forensicpack_state.db-shm",
    "tmp_*_manifest.txt",
    "*.partial",
    "*.partial.*",
    "*.manifest.json",
    "*.manifest.txt",
    "*.audit.jsonl",
    "*.sha256",
    "*.sig",
    "*.pem",
)

_ARCHIVE_SUFFIXES = (".tar.bz2", ".tar.gz", ".7z", ".zip")


def _matches_generated_pattern(name: str) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, pattern.lower()) for pattern in _GENERATED_PATTERNS)


def archive_source_name(name: str) -> str | None:
    lowered = name.lower()
    split_base = name
    if lowered.endswith(tuple(f"{suffix}.001" for suffix in _ARCHIVE_SUFFIXES)):
        split_base = name[:-4]
        lowered = lowered[:-4]
    for suffix in _ARCHIVE_SUFFIXES:
        if lowered.endswith(suffix):
            return split_base[: -len(suffix)]
    return None


def classify_source_items(source_dir: Path, config: JobConfig) -> tuple[list[Path], list[Path]]:
    """Return (processable, generated/excluded) immediate source children.

    A prior archive is considered generated only when its archive basename maps
    to another sibling source item. Standalone archive evidence remains eligible
    for packaging.
    """
    raw_items = sorted(source_dir.iterdir(), key=lambda path: path.name.casefold())
    if not config.exclude_generated_outputs:
        return raw_items, []

    obvious_generated = {item for item in raw_items if _matches_generated_pattern(item.name)}
    sibling_names = {item.name for item in raw_items if item not in obvious_generated}
    generated_archives: set[Path] = set()

    for item in raw_items:
        source_name = archive_source_name(item.name)
        if source_name and source_name in sibling_names:
            generated_archives.add(item)
            if item.name.casefold().endswith(".7z.001"):
                split_base = item.name[:-4]
                generated_archives.update(source_dir.glob(f"{split_base}.*"))

    excluded = obvious_generated | generated_archives
    processable = [item for item in raw_items if item not in excluded]
    return processable, sorted(excluded, key=lambda path: path.name.casefold())


def _planned_output_paths(item: Path, config: JobConfig) -> list[Path]:
    archive = config.output_dir / f"{item.name}{archive_suffix(config.archive_fmt)}"
    planned = [archive]
    if config.archive_fmt == "7z" and config.split_enabled:
        planned.append(archive.with_name(f"{archive.name}.001"))

    stem = config.output_dir / item.name
    if config.retain_manifests:
        planned.append(stem.with_name(f"{stem.name}.manifest.txt"))
    planned.extend(
        [
            stem.with_name(f"{stem.name}.manifest.json"),
            stem.with_name(f"{stem.name}.sha256"),
        ]
    )
    if config.audit_log:
        planned.append(stem.with_name(f"{stem.name}.audit.jsonl"))
    if config.signing_key_path:
        planned.append(stem.with_name(f"{stem.name}.manifest.json.sig"))
    if config.signing_certificate_path:
        planned.append(stem.with_name(f"{stem.name}.certificate.pem"))
    return planned


def output_collisions(items: list[Path], config: JobConfig, excluded: list[Path]) -> list[str]:
    excluded_resolved = {safe_resolve(path) for path in excluded}
    collisions: list[str] = []
    seen: set[Path] = set()
    for item in items:
        for target in _planned_output_paths(item, config):
            target_resolved = safe_resolve(target)
            if target_resolved in seen or not target.exists():
                continue
            seen.add(target_resolved)
            if target_resolved in excluded_resolved:
                continue
            if config.skip_existing and target == _planned_output_paths(item, config)[0]:
                continue
            collisions.append(
                f"Output already exists and is not a recognized prior ForensicPack output: {target}"
            )
    return collisions


def _walk_size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def preflight_session(items: list[Path], config: JobConfig) -> list[str]:
    warnings: list[str] = []
    if config.preflight_space_check:
        source_bytes = sum(_walk_size(item) for item in items)
        required_bytes = int(source_bytes * 1.10) + (64 * 1024 * 1024)
        try:
            probe = config.output_dir if config.output_dir.exists() else config.output_dir.parent
            free_bytes = shutil.disk_usage(probe).free
            if free_bytes < required_bytes:
                raise ValueError(
                    f"Insufficient free space. Estimated requirement: {required_bytes:,} bytes; "
                    f"available: {free_bytes:,} bytes."
                )
            if free_bytes < required_bytes * 2:
                warnings.append(
                    f"Free-space margin is low: {free_bytes:,} bytes available for an estimated "
                    f"{required_bytes:,}-byte operation."
                )
        except FileNotFoundError:
            warnings.append("Free-space preflight could not resolve the output volume.")

    threshold = max(1, config.long_path_warning_threshold)
    for item in items:
        if len(str(safe_resolve(item))) >= threshold:
            warnings.append(f"Long source path may exceed application or filesystem limits: {item}")

    if config.archive_fmt in {"ZIP", "TAR.GZ", "TAR.BZ2"}:
        warnings.append(
            f"{config.archive_fmt} does not preserve all Windows/NTFS metadata such as ACLs, "
            "alternate data streams, and every file attribute. The external manifest records "
            "available metadata, but the archive is not a forensic filesystem image."
        )
    return warnings
