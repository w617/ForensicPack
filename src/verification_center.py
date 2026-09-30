"""Reverify historical packages and export self-contained verification records."""
import hashlib
import json
import os
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from archivers import verify_archive
from audit import verify_audit_log
from content_verification import verify_archive_member_hashes
from hashing import hash_file
from history import HistoryStore, utc_now
from models import CancellationToken, FileRecord, JobCallbacks, JobCancelled, JobConfig
from utils import safe_resolve


def quiet_callbacks():
    return JobCallbacks(lambda *_: None, lambda *_: None, lambda *_: None, lambda *_: None)


def load_record_manifest(row):
    if not row["manifest_path"]:
        raise ValueError("This run has no completed package manifest.")
    path = Path(row["manifest_path"])
    data = path.read_bytes()
    if not row["manifest_sha256"] or hashlib.sha256(data).hexdigest().upper() != row["manifest_sha256"].upper():
        raise ValueError("The saved package manifest changed after it was indexed.")
    payload = json.loads(data)
    if payload.get("schema") != "org.forensicpack.package-manifest/v1":
        raise ValueError("Unsupported package manifest.")
    return payload


def validate_record_metadata(row, payload=None):
    audit = Path(row["audit_path"]) if row["audit_path"] else None
    if audit:
        ok, detail = verify_audit_log(audit)
        if not ok:
            raise ValueError(f"Audit verification failed: {detail}")
        expected = (payload or {}).get("audit", {}).get("final_chain_hash")
        if expected and detail != expected:
            raise ValueError("Audit chain differs from the completed package manifest.")
    # Check original metadata digests without requiring the original archive drive.
    if row["checksum_path"]:
        checksum = Path(row["checksum_path"])
        root = Path(row["run_dir"]).resolve()
        for line in checksum.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest, name = line.split(maxsplit=1)
            target = (checksum.parent / name.lstrip(" *")).resolve()
            if root in target.parents:
                if hash_file(target, ["SHA256"])["SHA256"] != digest.upper():
                    raise ValueError(f"Recorded metadata checksum mismatch: {target.name}")


def _part_name(name):
    normalized = name.replace("\\", "/")
    if PurePosixPath(normalized).name != name or name in {"", ".", ".."} or ":" in name:
        raise ValueError(f"Unsafe archive part name: {name}")
    return name


def reverify_run(run_id: str, *, archive_dir: Path | None = None, verify_temp_dir: Path | None = None,
                 password: str | None = None, token: CancellationToken | None = None,
                 callbacks: JobCallbacks | None = None, store: HistoryStore | None = None):
    store = store or HistoryStore()
    row = store.get(run_id)
    token = token or CancellationToken()
    callbacks = callbacks or quiet_callbacks()
    details = {"archive_dir": str(archive_dir or Path(row["archive_path"]).parent)}
    status = "failed"
    try:
        token.raise_if_requested(0)
        payload = load_record_manifest(row)
        validate_record_metadata(row, payload)
        parts = payload.get("archive_parts", {})
        if not parts:
            raise ValueError("No archive-volume hashes were recorded.")
        root = Path(details["archive_dir"])
        paths = [root / _part_name(name) for name in sorted(parts)]
        first = paths[0]
        if first.name.endswith(".001"):
            actual = {p.name for p in root.glob(first.name[:-3] + "*") if p.is_file()}
            if actual != set(parts):
                raise ValueError("Missing or unexpected archive volumes.")
        for path in paths:
            callbacks.status_cb(f"Checking archive hash: {path.name}")
            if hash_file(path, ["SHA256"], job_id=0, token=token)["SHA256"] != parts[path.name].upper():
                raise ValueError(f"Archive SHA256 mismatch: {path.name}")
        fmt = payload["archive_format"]
        if not verify_archive(first, fmt, callbacks, job_id=0, token=token, password=password):
            raise ValueError("Archive structural verification failed.")
        records, hashes = [], {}
        for entry in payload["files"]:
            path = Path(entry["archive_path"])
            record = FileRecord(path, entry["relative_path"], entry["archive_path"], entry["size"],
                                entry.get("created_utc", ""), entry.get("modified_utc", ""))
            records.append(record)
            hashes[path] = entry["hashes"]
        cfg = JobConfig(source_dir=Path(row["source_path"]), output_dir=root, archive_fmt=fmt,
                        compress_level_label="Normal (5)", split_enabled=first.name.endswith(".001"),
                        split_size_str="", hash_algorithms=["SHA256"], password=password,
                        delete_source=False, skip_existing=False, verify_temp_dir=verify_temp_dir)
        ok, message, count = verify_archive_member_hashes(first, records, hashes, cfg, token, callbacks, 0)
        if not ok or count != len(records) or not records:
            raise ValueError(message)
        details.update(message=message, member_count=count, archive_parts=list(parts))
        # The original source is not required or claimed to have been re-examined.
        details["scope"] = "Archive and retained records compared with the original manifest; source not re-examined."
        status = "warning" if payload.get("scan_issues") else "pass"
        if status == "warning":
            details["warning"] = "The original package contains recorded source-scan omissions."
    except JobCancelled:
        status = "cancelled"
        details["message"] = "Reverification cancelled."
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        details["message"] = str(exc)
    report = store.record_verification(run_id, status, details)
    return {"status": status, "report_path": str(report), **details}


def _hash_stream(handle, token):
    digest = hashlib.sha256()
    while True:
        token.raise_if_requested(0)
        block = handle.read(1 << 20)
        if not block:
            return digest.hexdigest().upper()
        digest.update(block)


def export_records(run_ids: list[str], destination: Path, *, store: HistoryStore | None = None,
                   token: CancellationToken | None = None) -> Path:
    store = store or HistoryStore()
    token = token or CancellationToken()
    if not run_ids:
        raise ValueError("Select at least one run to export.")
    if destination.exists():
        raise ValueError("Export destination already exists; choose a new filename.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    selections = []
    sources = {}
    for run_id in dict.fromkeys(run_ids):
        token.raise_if_requested(0)
        row = store.get(run_id)
        payload = load_record_manifest(row) if row["manifest_path"] else None
        validate_record_metadata(row, payload)
        folder = Path(row["run_dir"])
        if safe_resolve(destination) == folder.resolve() or folder.resolve() in safe_resolve(destination).parents:
            raise ValueError("Export outside the original run-record folder.")
        for path in folder.iterdir():
            if path.is_file() and not path.is_symlink() and path.name.endswith(
                (".manifest.txt", ".manifest.json", ".audit.jsonl", ".sha256", ".manifest.json.sig", ".certificate.pem")
            ):
                sources[f"records/{run_id}/{path.name}"] = path
        for report in json.loads(row["reports_json"]):
            path = Path(report)
            if not path.is_file():
                raise ValueError(f"Session report is missing: {path}")
            sources[f"records/{run_id}/reports/{path.name}"] = path
        for event in store.events(run_id):
            path = Path(event["report_path"])
            sources[f"records/{run_id}/reverification/{path.name}"] = path
        selections.append({"run_id": run_id, "case_name": row["case_name"], "case_id": row["case_id"],
                           "evidence_id": row["evidence_id"], "status": row["status"],
                           "archive_parts": (payload or {}).get("archive_parts", {}),
                           "manifest_member": f"records/{run_id}/{Path(row['manifest_path']).name}" if payload else ""})
    if not sources:
        raise ValueError("No retained records are available for the selected runs.")
    temporary = destination.with_name(destination.name + f".{uuid.uuid4().hex}.partial")
    try:
        hashes = {}
        with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for name, path in sources.items():
                token.raise_if_requested(0)
                before = path.stat()
                digest = hashlib.sha256()
                with path.open("rb") as source, archive.open(name, "w", force_zip64=True) as target:
                    while True:
                        token.raise_if_requested(0)
                        block = source.read(1 << 20)
                        if not block:
                            break
                        digest.update(block)
                        target.write(block)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError(f"Record changed during export: {path.name}")
                hashes[name] = digest.hexdigest().upper()
            index = {"schema": "org.forensicpack.records-bundle/v1", "created_utc": utc_now(),
                     "runs": selections, "members": hashes,
                     "scope": "Verification records only; evidence archives are not included. Original records are unchanged.",
                     "reports": "Session reports may describe other items processed in the same session.",
                     "integrity": "Hashes establish consistency, not independent provenance or authenticity."}
            archive.writestr("bundle_manifest.json", json.dumps(index, indent=2))
        verify_record_bundle(temporary, token=token)
        token.raise_if_requested(0)
        # Reserve the destination without overwriting an existing file.
        with destination.open("xb"):
            pass
        try:
            os.replace(temporary, destination)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def verify_record_bundle(bundle: Path, *, archive_dir: Path | None = None,
                         token: CancellationToken | None = None) -> dict:
    token = token or CancellationToken()
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate bundle member names.")
        index = json.loads(archive.read("bundle_manifest.json"))
        if index.get("schema") != "org.forensicpack.records-bundle/v1":
            raise ValueError("Unsupported records bundle.")
        expected = index["members"]
        if not expected or set(names) != {*expected, "bundle_manifest.json"}:
            raise ValueError("Missing or unexpected bundle members.")
        for name, digest in expected.items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                raise ValueError("Unsafe bundle member path.")
            with archive.open(name) as handle:
                if _hash_stream(handle, token) != digest.upper():
                    raise ValueError(f"Bundle checksum mismatch: {name}")
        checked_parts = 0
        if archive_dir:
            for run in index["runs"]:
                for name, digest in run["archive_parts"].items():
                    path = archive_dir / _part_name(name)
                    if hash_file(path, ["SHA256"], token=token, job_id=0)["SHA256"] != digest.upper():
                        raise ValueError(f"Archive SHA256 mismatch: {name}")
                    checked_parts += 1
        return {"status": "pass", "records_checked": len(expected), "archive_parts_checked": checked_parts,
                "runs": index["runs"]}
