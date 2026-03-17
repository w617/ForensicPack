import sqlite3
import threading
from pathlib import Path
from typing import Literal

from models import JobConfig

def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")

def _item_key(path: Path) -> str:
    from utils import safe_resolve
    return str(safe_resolve(path))

class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._commit_count = 0
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    session_key TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    case_name TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    source_size_hint INTEGER DEFAULT 0,
                    source_mtime REAL DEFAULT 0,
                    source_ctime REAL DEFAULT 0,
                    archive_path TEXT DEFAULT '',
                    manifest_path TEXT DEFAULT '',
                    state TEXT NOT NULL,
                    verify TEXT DEFAULT '',
                    file_count INTEGER DEFAULT 0,
                    source_size INTEGER DEFAULT 0,
                    archive_size INTEGER DEFAULT 0,
                    warning_text TEXT DEFAULT '',
                    error_text TEXT DEFAULT '',
                    scan_mode TEXT NOT NULL,
                    archive_hash_mode TEXT NOT NULL,
                    thread_strategy TEXT NOT NULL,
                    split_enabled INTEGER DEFAULT 0,
                    split_size TEXT DEFAULT '',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(session_key, item_key)
                );
                CREATE TABLE IF NOT EXISTS parts (
                    session_key TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    part_path TEXT NOT NULL,
                    size INTEGER DEFAULT 0,
                    mtime REAL DEFAULT 0,
                    PRIMARY KEY(session_key, item_key, part_path)
                );
                """
            )
            self._conn.commit()

    def upsert_discovered(self, session_key: str, item_path: Path, config: JobConfig) -> None:
        stat = item_path.stat()
        payload = (
            session_key,
            _item_key(item_path),
            item_path.name,
            str(item_path),
            int(stat.st_size),
            float(stat.st_mtime),
            float(stat.st_ctime),
            "discovered",
            config.scan_mode,
            config.archive_hash_mode,
            config.thread_strategy,
            int(config.split_enabled),
            config.split_size_str,
            _now_iso(),
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO jobs (
                    session_key, item_key, case_name, source_path, source_size_hint, source_mtime, source_ctime,
                    state, scan_mode, archive_hash_mode, thread_strategy, split_enabled, split_size, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key, item_key) DO NOTHING
                """,
                payload,
            )
            self._conn.commit()

    def update_job(
        self,
        session_key: str,
        item_path: Path,
        state: str,
        *,
        verify: str = "",
        archive_path: str = "",
        manifest_path: str = "",
        file_count: int | str | None = None,
        source_size: int | str | None = None,
        archive_size: int | str | None = None,
        warning_text: str = "",
        error_text: str = "",
    ) -> None:
        def _as_int(value: int | str | None) -> int:
            if isinstance(value, int):
                return value
            return 0

        with self._lock:
            self._conn.execute(
                """
                UPDATE jobs
                SET state=?, verify=?, archive_path=COALESCE(NULLIF(?, ''), archive_path),
                    manifest_path=COALESCE(NULLIF(?, ''), manifest_path),
                    file_count=?, source_size=?, archive_size=?,
                    warning_text=COALESCE(NULLIF(?, ''), warning_text),
                    error_text=COALESCE(NULLIF(?, ''), error_text),
                    updated_at=?
                WHERE session_key=? AND item_key=?
                """,
                (
                    state,
                    verify,
                    archive_path,
                    manifest_path,
                    _as_int(file_count),
                    _as_int(source_size),
                    _as_int(archive_size),
                    warning_text,
                    error_text,
                    _now_iso(),
                    session_key,
                    _item_key(item_path),
                ),
            )
            self._conn.commit()
            self._commit_count += 1
            if self._commit_count % 50 == 0:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass

    def replace_parts(self, session_key: str, item_path: Path, parts: list[Path]) -> None:
        item = _item_key(item_path)
        with self._lock:
            self._conn.execute("DELETE FROM parts WHERE session_key=? AND item_key=?", (session_key, item))
            for part in parts:
                stat = part.stat()
                self._conn.execute(
                    "INSERT INTO parts(session_key, item_key, part_path, size, mtime) VALUES (?, ?, ?, ?, ?)",
                    (session_key, item, str(part), int(stat.st_size), float(stat.st_mtime)),
                )
            self._conn.commit()

    def completed_items(self, session_key: str) -> dict[str, sqlite3.Row]:
        """Return all items considered complete enough to skip on resume.

        Includes 'completed', 'skipped' (the nominal states) as well as
        'verified' and 'hashed' — items that finished archive verification
        or archive hashing but crashed before the final state write. This
        closes the gap where a restart between phases causes unnecessary
        re-processing of already-verified jobs.
        """
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM jobs
                WHERE session_key=? AND state IN ('completed', 'skipped', 'verified', 'hashed')
                """,
                (session_key,),
            ).fetchall()
        return {row["item_key"]: row for row in rows}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

def open_state_store(path: Path) -> StateStore:
    return StateStore(path)
