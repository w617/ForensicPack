import hashlib
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

from models import CancellationToken, FileRecord, JobCallbacks, JobConfig, ProgressEvent
from process_runner import run_process
from verification_space import scratch_root, reserve_scratch
from utils import redact_command


def _new_hasher(algorithm: str):
    normalized = algorithm.lower().replace("-", "")
    if normalized == "md5":
        return hashlib.md5(usedforsecurity=False)
    if normalized == "sha1":
        return hashlib.sha1(usedforsecurity=False)
    if normalized == "sha256":
        return hashlib.sha256()
    if normalized == "sha512":
        return hashlib.sha512()
    raise ValueError(f"Unsupported hash algorithm: {algorithm}")


def _hash_stream(handle, algorithm: str, token: CancellationToken, job_id: int) -> str:
    hasher = _new_hasher(algorithm)
    while True:
        token.raise_if_requested(job_id)
        chunk = handle.read(1 << 20)
        if not chunk:
            break
        hasher.update(chunk)
    return hasher.hexdigest().upper()


def _expected_hashes(
    records: list[FileRecord], file_hashes: dict[Path, dict[str, str]], algorithm: str
) -> dict[str, str]:
    expected: dict[str, str] = {}
    for record in records:
        digest = file_hashes.get(record.path, {}).get(algorithm)
        if digest:
            expected[record.archive_rel.replace("\\", "/")] = digest.upper()
    return expected


def _is_embedded_manifest(name: str) -> bool:
    return name.lower().endswith("manifest.txt")


def _has_7z_signature(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(6) == bytes.fromhex("377ABCAF271C")
    except OSError:
        return False


def _compare(
    actual: dict[str, str], expected: dict[str, str], all_member_names: set[str]
) -> tuple[bool, str]:
    missing = sorted(set(expected) - set(actual))
    mismatched = sorted(name for name in expected.keys() & actual.keys() if expected[name] != actual[name])
    unexpected = sorted(name for name in all_member_names - set(expected) if not _is_embedded_manifest(name))
    if missing or mismatched or unexpected:
        details: list[str] = []
        if missing:
            details.append("missing members: " + ", ".join(missing[:10]))
        if mismatched:
            details.append("hash mismatches: " + ", ".join(mismatched[:10]))
        if unexpected:
            details.append("unexpected members: " + ", ".join(unexpected[:10]))
        return False, "; ".join(details)
    return True, f"Verified {len(expected)} archived member hash(es)."


def verify_archive_member_hashes(
    archive_path: Path,
    records: list[FileRecord],
    file_hashes: dict[Path, dict[str, str]],
    config: JobConfig,
    token: CancellationToken,
    callbacks: JobCallbacks,
    job_id: int,
    algorithm: str = "SHA256",
) -> tuple[bool, str, int]:
    expected = _expected_hashes(records, file_hashes, algorithm)
    if len(expected) != len(records):
        return False, f"Incomplete or duplicate {algorithm} source-hash inventory.", 0
    if not expected:
        return False, f"No {algorithm} source hashes were available for member verification.", 0

    callbacks.log_cb(f"  Comparing archived member {algorithm} values with the source manifest ...", "#8b949e")
    actual: dict[str, str] = {}

    if config.archive_fmt == "ZIP":
        with zipfile.ZipFile(archive_path, "r") as archive:
            member_names = {name.replace("\\", "/") for name in archive.namelist() if not name.endswith("/")}
            for name in expected:
                token.raise_if_requested(job_id)
                try:
                    with archive.open(name, "r") as handle:
                        actual[name] = _hash_stream(handle, algorithm, token, job_id)
                except KeyError:
                    continue
        ok, detail = _compare(actual, expected, member_names)
        return ok, detail, len(actual)

    if config.archive_fmt in {"TAR.GZ", "TAR.BZ2"}:
        mode = "r:gz" if config.archive_fmt == "TAR.GZ" else "r:bz2"
        with tarfile.open(archive_path, mode) as archive:
            members = {
                member.name.replace("\\", "/"): member
                for member in archive.getmembers()
                if member.isfile()
            }
            for name in expected:
                token.raise_if_requested(job_id)
                member = members.get(name)
                if member is None:
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                with handle:
                    actual[name] = _hash_stream(handle, algorithm, token, job_id)
        ok, detail = _compare(actual, expected, set(members))
        return ok, detail, len(actual)

    if not _has_7z_signature(archive_path):
        return False, "Invalid or unsupported 7-Zip signature; member verification was not performed.", 0

    from archivers import find_7zip
    executable = find_7zip(config.seven_zip_path)
    if not executable:
        return False, "7-Zip was not found for archive member extraction verification.", 0

    scratch = scratch_root(config)
    total_bytes = sum(record.size for record in records)
    callbacks.log_cb(f"  Verification temporary folder: {scratch} ({total_bytes:,} source bytes)", "#8b949e")
    with reserve_scratch(scratch, total_bytes), tempfile.TemporaryDirectory(
        prefix="ForensicPack_verify_", dir=scratch
    ) as temporary:
        extraction_root = Path(temporary)
        command = [executable, "x", str(archive_path.resolve()), f"-o{extraction_root}", "-y"]
        if config.password:
            command.append(f"-p{config.password}")
        callbacks.emit_progress(ProgressEvent(job_id, "verify_extract", 0, 1, "Extracting archive for verification"))
        if not run_process(command, token, job_id, callbacks):
            return False, "7-Zip extraction verification failed.", 0
        callbacks.emit_progress(ProgressEvent(job_id, "verify_extract", 1, 1, "Verification extraction complete"))
        root_resolved = extraction_root.resolve()
        all_members: set[str] = set()
        for extracted in extraction_root.rglob("*"):
            if extracted.is_file():
                all_members.add(extracted.relative_to(extraction_root).as_posix())
        for name in expected:
            token.raise_if_requested(job_id)
            callbacks.emit_progress(ProgressEvent(job_id, "verify_members", len(actual), len(expected), "Comparing member hashes"))
            candidate = (extraction_root / Path(name)).resolve()
            try:
                candidate.relative_to(root_resolved)
            except ValueError:
                return False, f"Unsafe archive member path detected: {name}", len(actual)
            if not candidate.is_file():
                continue
            with candidate.open("rb") as handle:
                actual[name] = _hash_stream(handle, algorithm, token, job_id)
    ok, detail = _compare(actual, expected, all_members)
    return ok, detail, len(actual)
