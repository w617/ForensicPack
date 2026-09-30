"""Fail-closed validation for resume and destructive source cleanup."""
import hashlib
import json
from pathlib import Path

from forensic_inventory import build_forensic_inventory
from hashing import hash_file
from models import CancellationToken, JobCallbacks, JobConfig
from utils import expected_archive_path, safe_resolve, split_entry_path, split_output_parts


def source_snapshot_error(
    item_path: Path, expected_files: list[dict], token: CancellationToken,
    callbacks: JobCallbacks, job_id: int,
) -> str | None:
    """Compare a fresh inventory and SHA-256 readback with the packaged source."""
    if not expected_files:
        return "No complete source hash inventory is available."
    records, _, issues = build_forensic_inventory(item_path, job_id, token, callbacks)
    if issues:
        return "The source cannot be completely inventoried."
    expected = {entry["archive_path"]: entry for entry in expected_files}
    if len(expected) != len(expected_files) or set(expected) != {record.archive_rel for record in records}:
        return "The source file inventory changed."
    for record in records:
        entry = expected[record.archive_rel]
        digest = entry.get("hashes", {}).get("SHA256")
        if not digest:
            return f"Missing source SHA256: {record.archive_rel}"
        if record.size != entry.get("size") or record.modified_utc != entry.get("modified_utc"):
            return f"Source metadata changed: {record.archive_rel}"
        before = record.path.stat(follow_symlinks=False)
        actual = hash_file(record.path, ["SHA256"], job_id=job_id, token=token)["SHA256"]
        after = record.path.stat(follow_symlinks=False)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns
        ):
            return f"Source changed during validation: {record.archive_rel}"
        if actual.upper() != digest.upper():
            return f"Source content changed: {record.archive_rel}"
    return None


def resume_validation_error(
    row, item_path: Path, config: JobConfig, token: CancellationToken,
    callbacks: JobCallbacks, job_id: int,
) -> str | None:
    """A database state is only a candidate; verify its saved evidence again."""
    try:
        if row["state"] != "completed" or row["verify"] != "PASS":
            return "The previous job did not complete without warnings."
        if not row["manifest_json_path"] or not row["manifest_sha256"]:
            return "Legacy state has no recorded manifest digest; rebuild required."
        raw = Path(row["manifest_json_path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest().upper() != row["manifest_sha256"].upper():
            return "The saved manifest changed."
        manifest = json.loads(raw)
        if (manifest.get("schema") != "org.forensicpack.package-manifest/v1"
                or safe_resolve(Path(manifest["source_item"])) != safe_resolve(item_path)
                or manifest.get("archive_format") != config.archive_fmt
                or manifest.get("content_verification") != "PASS"
                or manifest.get("scan_issues")):
            return "The saved manifest does not identify a completely verified package."
        base = expected_archive_path(item_path, config.output_dir, config.archive_fmt)
        if safe_resolve(Path(row["archive_path"])) != safe_resolve(split_entry_path(base, config)):
            return "The archive destination changed."
        expected_parts = manifest.get("archive_parts")
        parts = split_output_parts(base, config)
        if not expected_parts or set(expected_parts) != {part.name for part in parts}:
            return "Archive volumes are missing or unexpected volumes are present."
        for part in parts:
            actual = hash_file(part, ["SHA256"], job_id=job_id, token=token)["SHA256"]
            if actual.upper() != expected_parts[part.name].upper():
                return f"Archive SHA256 mismatch: {part.name}"
        return source_snapshot_error(item_path, manifest["files"], token, callbacks, job_id)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return f"Saved package could not be revalidated: {exc}"
