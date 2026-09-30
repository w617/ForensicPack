"""Persistent per-attempt job history; resume state remains a separate concern."""
import datetime as dt
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from utils import application_data_dir


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def file_digest(path: Path) -> str:
    from hashing import hash_file
    return hash_file(path, ["SHA256"])["SHA256"]


class HistoryStore:
    def __init__(self, path: Path | None = None):
        self.path = path or application_data_dir() / "history.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, started_utc TEXT NOT NULL, finished_utc TEXT DEFAULT '',
                    case_name TEXT NOT NULL, case_id TEXT DEFAULT '', evidence_id TEXT DEFAULT '',
                    source_path TEXT DEFAULT '', archive_path TEXT DEFAULT '', run_dir TEXT NOT NULL,
                    archive_format TEXT DEFAULT '', status TEXT NOT NULL, verification TEXT DEFAULT '',
                    content_verification TEXT DEFAULT '', warnings_json TEXT DEFAULT '[]',
                    manifest_path TEXT DEFAULT '', manifest_sha256 TEXT DEFAULT '', audit_path TEXT DEFAULT '',
                    checksum_path TEXT DEFAULT '', reports_json TEXT DEFAULT '[]', result_json TEXT DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS history_started ON runs(started_utc DESC);
                CREATE INDEX IF NOT EXISTS history_case ON runs(case_id, evidence_id);
                CREATE TABLE IF NOT EXISTS verification_events (
                    event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, checked_utc TEXT NOT NULL,
                    status TEXT NOT NULL, report_path TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS history_checks ON verification_events(run_id, checked_utc DESC);
            ''')

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def begin(self, run_id, run_dir, item_path, archive_path, config):
        metadata = config.case_metadata or {}
        with self.connect() as conn:
            conn.execute('''INSERT INTO runs
                (run_id, started_utc, case_name, case_id, evidence_id, source_path, archive_path,
                 run_dir, archive_format, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (run_id, utc_now(), item_path.name, metadata.get("Case ID", ""),
                 metadata.get("Evidence ID", ""), str(item_path), str(archive_path), str(run_dir),
                 config.archive_fmt, "running/incomplete"))

    def finish(self, result):
        digest = file_digest(Path(result.external_manifest_json)) if result.external_manifest_json else ""
        with self.connect() as conn:
            conn.execute('''UPDATE runs SET finished_utc=?, archive_path=COALESCE(NULLIF(?, ''), archive_path),
                status=?, verification=?, content_verification=?, warnings_json=?, manifest_path=?,
                manifest_sha256=?, audit_path=?, checksum_path=?, result_json=? WHERE run_id=?''',
                (utc_now(), result.archive_path, result.status, result.verify, result.content_verify,
                 json.dumps(result.warnings), result.external_manifest_json, digest, result.audit_log_path,
                 result.checksum_path, json.dumps(result.to_report_row()), result.run_id))

    def attach_reports(self, run_ids, paths):
        additions = [str(path) for path in paths if path.is_file()]
        with self.connect() as conn:
            for run_id in set(run_ids) - {""}:
                row = conn.execute("SELECT reports_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
                if row:
                    reports = list(dict.fromkeys([*json.loads(row[0]), *additions]))
                    conn.execute("UPDATE runs SET reports_json=? WHERE run_id=?", (json.dumps(reports), run_id))

    def search(self, query="", status="All", limit=200, offset=0):
        literal = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        args = {"query": query, "pattern": f"%{literal}%", "status": status,
                "limit": max(1, min(int(limit), 500)), "offset": max(0, int(offset))}
        with self.connect() as conn:
            total = conn.execute("""SELECT COUNT(*) FROM runs WHERE
                (:query='' OR case_name LIKE :pattern ESCAPE '\\' OR case_id LIKE :pattern ESCAPE '\\'
                OR evidence_id LIKE :pattern ESCAPE '\\' OR archive_path LIKE :pattern ESCAPE '\\'
                OR run_id LIKE :pattern ESCAPE '\\') AND (:status='All' OR status=:status)""", args).fetchone()[0]
            rows = conn.execute("""SELECT runs.*, (SELECT status FROM verification_events v WHERE
                v.run_id=runs.run_id ORDER BY checked_utc DESC LIMIT 1) AS last_check FROM runs WHERE
                (:query='' OR case_name LIKE :pattern ESCAPE '\\' OR case_id LIKE :pattern ESCAPE '\\'
                OR evidence_id LIKE :pattern ESCAPE '\\' OR archive_path LIKE :pattern ESCAPE '\\'
                OR run_id LIKE :pattern ESCAPE '\\') AND (:status='All' OR status=:status)
                ORDER BY started_utc DESC, run_id DESC LIMIT :limit OFFSET :offset""", args).fetchall()
        return [dict(row) for row in rows], total

    def get(self, run_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"Run not found: {run_id}")
        return dict(row)

    def events(self, run_id):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM verification_events WHERE run_id=? ORDER BY checked_utc", (run_id,))]

    def record_verification(self, run_id, status, details):
        row = self.get(run_id)
        event_id = uuid.uuid4().hex
        checked = utc_now()
        folder = Path(row["run_dir"]) / "Reverification"
        folder.mkdir(exist_ok=True)
        report = folder / f"{event_id}.json"
        report.write_text(json.dumps({"run_id": run_id, "checked_utc": checked, "status": status,
                                      **details}, indent=2), encoding="utf-8")
        with self.connect() as conn:
            conn.execute("INSERT INTO verification_events VALUES (?, ?, ?, ?, ?)",
                         (event_id, run_id, checked, status, str(report)))
        return report

    def import_previous_runs(self, token=None):
        """Index v2.2.2 records without altering their original files or audit chains."""
        imported = 0
        for run_dir in (application_data_dir() / "Cases").glob("*/Runs/*"):
            if token:
                token.raise_if_requested(0)
            if not run_dir.is_dir():
                continue
            with self.connect() as conn:
                if conn.execute("SELECT 1 FROM runs WHERE run_id=?", (run_dir.name,)).fetchone():
                    continue
            manifests = list(run_dir.glob("*.manifest.json"))
            audits = list(run_dir.glob("*.audit.jsonl"))
            if not manifests and not audits:
                continue
            manifest = manifests[0] if manifests else None
            audit = audits[0] if audits else None
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8")) if manifest else {}
                entries = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()] if audit else []
                start = next((e for e in entries if e.get("event") == "job_started"), {})
                details = start.get("details", {})
                name = payload.get("case_name") or Path(details.get("source", "Unknown")).name
                metadata = payload.get("case_metadata", {})
                status = "success" if payload.get("content_verification") == "PASS" else "running/incomplete"
                if payload.get("scan_issues"):
                    status = "warning"
                terminal = {"job_failed": "failed", "job_cancelled": "cancelled", "job_skipped": "skipped"}
                for entry in entries:
                    status = terminal.get(entry.get("event"), status)
                archive = details.get("output", "")
                parts = payload.get("archive_parts", {})
                if archive and parts:
                    archive = str(Path(archive).parent / sorted(parts)[0])
                checksums = list(run_dir.glob("*.sha256"))
                with self.connect() as conn:
                    conn.execute('''INSERT OR IGNORE INTO runs
                        (run_id,started_utc,finished_utc,case_name,case_id,evidence_id,source_path,archive_path,
                         run_dir,archive_format,status,verification,content_verification,manifest_path,
                         manifest_sha256,audit_path,checksum_path,warnings_json)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (run_dir.name, start.get("timestamp_utc", payload.get("generated_utc", "")),
                         entries[-1].get("timestamp_utc", "") if entries else "", name,
                         metadata.get("Case ID", ""), metadata.get("Evidence ID", ""),
                         payload.get("source_item", details.get("source", "")), archive, str(run_dir),
                         payload.get("archive_format", details.get("format", "")), status, "IMPORTED — NOT REVERIFIED",
                         payload.get("content_verification", "NOT RUN"), str(manifest) if manifest else "",
                         file_digest(manifest) if manifest else "", str(audit) if audit else "",
                         str(checksums[0]) if checksums else "",
                         json.dumps(["Imported record; original source-deletion outcome may not be recorded."])))
                imported += 1
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return imported
