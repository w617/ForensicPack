import hashlib
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

from models import CancellationToken, FileRecord, JobCallbacks, JobConfig
from utils import redact_command


def _new_hasher(algorithm: str):
    return hashlib.new(algorithm.lower().replace("-", ""))


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


def _compare(actual: dict[str, str], expected: dict[str, str]) -> tuple[bool, str]:
    missing = sorted(set(expected) - set(actual))
    mismatched = sorted(name for name in expected.keys() & actual.keys() if expected[name] != actual[name])
    if missing or mismatched:
        details: list[str] = []
        if missing:
            details.append("missing members: " + ", ".join(missing[:10]))
        if mismatched:
            details.append("hash mismatches: " + ", ".join(mismatched[:10]))
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
    if not expected:
        return False, f"No {algorithm} source hashes were available for member verification.", 0

    callbacks.log_cb(f"  Comparing archived member {algorithm} values with the source manifest ...", "#8b949e")
    actual: dict[str, str] = {}

    if config.archive_fmt == "ZIP":
        with zipfile.ZipFile(archive_path, "r") as archive:
            for name in expected:
                token.raise_if_requested(job_id)
                try:
                    with archive.open(name, "r") as handle:
                        actual[name] = _hash_stream(handle, algorithm, token, job_id)
                except KeyError:
                    continue
        ok, detail = _compare(actual, expected)
        return ok, detail, len(actual)

    if config.archive_fmt in {"TAR.GZ", "TAR.BZ2"}:
        mode = "r:gz" if config.archive_fmt == "TAR.GZ" else "r:bz2"
        with tarfile.open(archive_path, mode) as archive:
            members = {member.name.replace("\\", "/"): member for member in archive.getmembers() if member.isfile()}
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
        ok, detail = _compare(actual, expected)
        return ok, detail, len(actual)

    executable = str(config.seven_zip_path) if config.seven_zip_path else shutil.which("7z")
    if not executable:
        for candidate in (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
            if Path(candidate).is_file():
                executable = candidate
                break
    if not executable:
        return False, "7-Zip was not found for member extraction verification.", 0

    with tempfile.TemporaryDirectory(prefix="ForensicPack_verify_") as temporary:
        extraction_root = Path(temporary)
        command = [executable, "x", str(archive_path), f"-o{extraction_root}", "-y"]
        if config.password:
            command.append(f"-p{config.password}")
        callbacks.log_cb(f"  [CMD] {redact_command(command)}", "#8b949e")
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            return False, "7-Zip extraction verification failed.", 0
        root_resolved = extraction_root.resolve()
        for name in expected:
            token.raise_if_requested(job_id)
            candidate = (extraction_root / Path(name)).resolve()
            try:
                candidate.relative_to(root_resolved)
            except ValueError:
                return False, f"Unsafe archive member path detected: {name}", len(actual)
            if not candidate.is_file():
                continue
            with candidate.open("rb") as handle:
                actual[name] = _hash_stream(handle, algorithm, token, job_id)
    ok, detail = _compare(actual, expected)
    return ok, detail, len(actual)
