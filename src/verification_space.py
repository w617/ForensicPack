"""Capacity planning and in-process reservations for 7z verification scratch."""
import shutil
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

_lock = threading.Lock()
_reserved: dict[int, int] = {}


def scratch_root(config) -> Path:
    root = Path(config.verify_temp_dir) if config.verify_temp_dir else Path(tempfile.gettempdir())
    if not root.is_dir():
        raise ValueError(f"Verification temporary folder does not exist: {root}")
    root = root.resolve()
    for protected in (config.source_dir, config.output_dir):
        protected = protected.resolve()
        if root == protected or protected in root.parents:
            raise ValueError("Verification temporary folder must be outside the source and destination folders.")
    return root


def required_scratch_bytes(source_bytes: int) -> int:
    return max(0, source_bytes) + max(source_bytes // 10, 64 * 1024 * 1024)


def check_scratch_space(root: Path, source_bytes: int) -> int:
    required = required_scratch_bytes(source_bytes)
    available = shutil.disk_usage(root).free - _reserved.get(root.stat().st_dev, 0)
    if available < required:
        raise ValueError(f"Insufficient verification space on {root}: need {required:,} bytes; "
                         f"{max(0, available):,} available after active reservations.")
    return required


@contextmanager
def reserve_scratch(root: Path, source_bytes: int):
    device = root.stat().st_dev
    with _lock:
        required = check_scratch_space(root, source_bytes)
        _reserved[device] = _reserved.get(device, 0) + required
    try:
        yield
    finally:
        with _lock:
            _reserved[device] -= required
            if not _reserved[device]:
                del _reserved[device]
