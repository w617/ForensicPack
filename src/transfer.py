import shutil
from dataclasses import dataclass
from pathlib import Path

from hashing import hash_file


@dataclass(frozen=True)
class TransferRecord:
    source: str
    destination: str
    size: int
    hashes: dict[str, str]
    verified: bool


def _copy_file_verified(source: Path, destination: Path, algorithms: list[str]) -> TransferRecord:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_hashes = hash_file(source, algorithms)
    shutil.copy2(source, destination)
    destination_hashes = hash_file(destination, algorithms)
    verified = source_hashes == destination_hashes and source.stat().st_size == destination.stat().st_size
    if not verified:
        try:
            destination.unlink()
        except OSError:
            pass
        raise RuntimeError(f"Transfer verification failed for {source} -> {destination}")
    return TransferRecord(
        source=str(source),
        destination=str(destination),
        size=source.stat().st_size,
        hashes=source_hashes,
        verified=True,
    )


def copy_with_verification(
    source: Path,
    destination: Path,
    algorithms: list[str] | None = None,
) -> list[TransferRecord]:
    algorithms = algorithms or ["SHA256"]
    source = Path(source)
    destination = Path(destination)
    if not source.exists():
        raise FileNotFoundError(source)

    if source.is_file():
        target = destination / source.name if destination.is_dir() or not destination.suffix else destination
        return [_copy_file_verified(source, target, algorithms)]

    target_root = destination / source.name
    records: list[TransferRecord] = []
    for path in sorted(source.rglob("*"), key=lambda value: str(value).casefold()):
        relative = path.relative_to(source)
        target = target_root / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            records.append(_copy_file_verified(path, target, algorithms))
    return records
