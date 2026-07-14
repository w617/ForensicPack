import datetime as dt
import hashlib
import json
import os
import shutil
from pathlib import Path

from hashing import hash_file
from models import FileRecord, JobConfig, ScanIssue
from utils import metadata_output_dir, split_output_parts
from version import APP_NAME, APP_VERSION


def _canonical_json(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sign_manifest(manifest_path: Path, key_path: Path, signature_path: Path) -> None:
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    except ImportError as exc:
        raise RuntimeError("Manifest signing requires the 'cryptography' package.") from exc

    private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    data = manifest_path.read_bytes()
    if isinstance(private_key, rsa.RSAPrivateKey):
        signature = private_key.sign(data, padding.PKCS1v15(), hashes.SHA256())
    elif isinstance(private_key, ec.EllipticCurvePrivateKey):
        signature = private_key.sign(data, ec.ECDSA(hashes.SHA256()))
    else:
        raise RuntimeError(f"Unsupported signing key type: {type(private_key).__name__}")
    signature_path.write_bytes(signature)


def _checksum_name(target: Path, checksum_dir: Path) -> str:
    relative = os.path.relpath(target, checksum_dir)
    return Path(relative).as_posix()


def write_package_sidecars(
    item_path: Path,
    base_archive: Path,
    text_manifest_source: Path,
    records: list[FileRecord],
    file_hashes: dict[Path, dict[str, str]],
    scan_issues: list[ScanIssue],
    config: JobConfig,
    content_verify: str,
    audit_log_path: Path | None,
    audit_final_hash: str,
) -> dict[str, str]:
    metadata_dir = metadata_output_dir(config.output_dir)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    stem = metadata_dir / item_path.name
    text_manifest_path = stem.with_name(f"{stem.name}.manifest.txt")
    json_manifest_path = stem.with_name(f"{stem.name}.manifest.json")
    checksum_path = stem.with_name(f"{stem.name}.sha256")
    signature_path = stem.with_name(f"{stem.name}.manifest.json.sig")
    certificate_copy = stem.with_name(f"{stem.name}.certificate.pem")

    if config.retain_manifests:
        shutil.copy2(text_manifest_source, text_manifest_path)

    archive_parts = split_output_parts(base_archive, config)
    archive_part_hashes = {
        part.name: hash_file(part, ["SHA256"])["SHA256"] for part in archive_parts if part.is_file()
    }
    files_payload = [
        record.to_manifest_dict(file_hashes.get(record.path, {})) for record in records
    ]
    payload: dict[str, object] = {
        "schema": "org.forensicpack.package-manifest/v1",
        "tool": {"name": APP_NAME, "version": APP_VERSION},
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
        "case_name": item_path.name,
        "source_item": str(item_path),
        "archive_format": config.archive_fmt,
        "archive_parts": archive_part_hashes,
        "content_verification": content_verify,
        "hash_algorithms": config.hash_algorithms,
        "case_metadata": config.case_metadata or {},
        "files": files_payload,
        "scan_issues": [issue.to_dict() for issue in scan_issues],
        "audit": {
            "path": str(audit_log_path) if audit_log_path else "",
            "final_chain_hash": audit_final_hash,
        },
        "limitations": [
            "This package is an evidence archive, not a bit-for-bit forensic image.",
            "Archive formats may not preserve all source filesystem metadata or alternate data streams.",
        ],
    }
    json_manifest_path.write_bytes(_canonical_json(payload) + b"\n")

    checksum_targets = [*archive_parts]
    if text_manifest_path.exists():
        checksum_targets.append(text_manifest_path)
    checksum_targets.append(json_manifest_path)
    if audit_log_path and audit_log_path.exists():
        checksum_targets.append(audit_log_path)
    checksum_lines = []
    for target in checksum_targets:
        digest = hash_file(target, ["SHA256"])["SHA256"]
        checksum_lines.append(f"{digest} *{_checksum_name(target, checksum_path.parent)}")
    checksum_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")

    signature_value = ""
    if config.signing_key_path:
        _sign_manifest(json_manifest_path, config.signing_key_path, signature_path)
        signature_value = str(signature_path)
        if config.signing_certificate_path:
            shutil.copy2(config.signing_certificate_path, certificate_copy)

    return {
        "text_manifest": str(text_manifest_path) if text_manifest_path.exists() else "",
        "json_manifest": str(json_manifest_path),
        "checksum": str(checksum_path),
        "signature": signature_value,
        "certificate": str(certificate_copy) if certificate_copy.exists() else "",
        "manifest_sha256": hashlib.sha256(json_manifest_path.read_bytes()).hexdigest().upper(),
    }


def verify_checksum_file(checksum_path: Path, package_dir: Path | None = None) -> tuple[bool, list[str]]:
    root = package_dir or checksum_path.parent
    issues: list[str] = []
    try:
        lines = checksum_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return False, [str(exc)]
    for line in lines:
        if not line.strip():
            continue
        try:
            expected, filename = line.split(maxsplit=1)
        except ValueError:
            issues.append(f"Malformed checksum line: {line}")
            continue
        filename = filename.lstrip(" *")
        target = root / filename
        if not target.is_file():
            issues.append(f"Missing checksum target: {filename}")
            continue
        actual = hash_file(target, ["SHA256"])["SHA256"]
        if actual.upper() != expected.upper():
            issues.append(f"SHA256 mismatch: {filename}")
    return not issues, issues
