import datetime as dt
import hashlib
import json
import threading
from pathlib import Path
from typing import Any


class AuditLogger:
    """Append-only JSONL audit log with a SHA-256 hash chain."""

    def __init__(self, path: Path, enabled: bool = True) -> None:
        self.path = Path(path)
        self.enabled = enabled
        self._lock = threading.Lock()
        self._sequence = 0
        self._previous_hash = "0" * 64
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    @staticmethod
    def _canonical(payload: dict[str, Any]) -> bytes:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def record(self, event: str, **details: Any) -> str:
        if not self.enabled:
            return ""
        with self._lock:
            self._sequence += 1
            body = {
                "sequence": self._sequence,
                "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
                "event": event,
                "details": details,
                "previous_hash": self._previous_hash,
            }
            entry_hash = hashlib.sha256(self._canonical(body)).hexdigest().upper()
            entry = dict(body)
            entry["entry_hash"] = entry_hash
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
            self._previous_hash = entry_hash
            return entry_hash

    @property
    def final_hash(self) -> str:
        return self._previous_hash if self.enabled else ""


def verify_audit_log(path: Path) -> tuple[bool, str]:
    previous = "0" * 64
    expected_sequence = 1
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return False, str(exc)
    for line in lines:
        try:
            entry = json.loads(line)
            recorded_hash = str(entry.pop("entry_hash"))
        except (ValueError, KeyError, TypeError) as exc:
            return False, f"Invalid audit entry: {exc}"
        if entry.get("sequence") != expected_sequence:
            return False, f"Unexpected sequence at entry {expected_sequence}."
        if entry.get("previous_hash") != previous:
            return False, f"Broken previous-hash link at entry {expected_sequence}."
        calculated = hashlib.sha256(AuditLogger._canonical(entry)).hexdigest().upper()
        if calculated != recorded_hash:
            return False, f"Hash mismatch at entry {expected_sequence}."
        previous = recorded_hash
        expected_sequence += 1
    return True, previous
