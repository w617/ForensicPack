import datetime as dt
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Literal

from archivers import create_archive, verify_archive
from audit import AuditLogger
from content_verification import verify_archive_member_hashes
from forensic_inventory import build_forensic_inventory, write_forensic_manifest
from hashing import hash_file
from models import (
    CancellationToken,
    JobCallbacks,
    JobCancelled,
    JobConfig,
    JobResult,
    JobSkipped,
    ProgressEvent,
    RuntimeState,
    summarize_job_results,
)
from safety import classify_source_items, output_collisions, preflight_session
from sidecars import verify_checksum_file, write_package_sidecars
from state_db import StateStore, open_state_store
from utils import (
    ARCHIVE_FORMATS,
    ARCHIVE_HASH_MODES,
    SCAN_MODES,
    THREAD_STRATEGIES,
    archive_suffix,
    cleanup_cancel_artifacts,
    cleanup_partial_outputs,
    expected_archive_path,
    is_relative_to,
    metadata_output_dir,
    normalize_hash_algorithms,
    output_size_bytes,
    rename_matching_outputs,
    resolve_state_db_path,
    safe_resolve,
    select_worker_count,
    session_profile_key,
    split_entry_path,
    split_output_parts,
    system_info,
)
from version import APP_NAME, APP_VERSION


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _default_result(item_path: Path, config: JobConfig) -> JobResult:
    return JobResult(
        case_name=item_path.name,
        format=config.archive_fmt,
        start_time=_now_iso(),
        end_time="",
        file_count=0,
        source_size=0,
        archive_path="",
        archive_size=0,
        verify="FAILED",
        status="failed",
    )


def _result_from_resume_row(row: sqlite3.Row, config: JobConfig) -> JobResult:
    warnings = ["Resumed from prior completed state."]
    if row["warning_text"]:
        warnings.insert(0, row["warning_text"])
    return JobResult(
        case_name=row["case_name"],
        format=config.archive_fmt,
        start_time=row["updated_at"],
        end_time=row["updated_at"],
        file_count=row["file_count"] or "Skipped",
        source_size=row["source_size"] or "Skipped",
        archive_path=row["archive_path"] or "",
        archive_size=row["archive_size"] or 0,
        verify="SKIPPED (Resume Preserved)",
        status="skipped",
        warnings=warnings,
        manifest_path=row["manifest_path"] or "",
    )


def validate_config(config: JobConfig) -> None:
    if not config.source_dir.is_dir():
        raise ValueError(f"Source directory not found: {config.source_dir}")
    if config.archive_fmt not in ARCHIVE_FORMATS:
        raise ValueError(f"Unsupported archive format: {config.archive_fmt}")
    config.hash_algorithms = normalize_hash_algorithms(config.hash_algorithms)
    if config.verify_member_hashes and "SHA256" not in config.hash_algorithms:
        config.hash_algorithms.append("SHA256")
    if config.password and config.archive_fmt in {"ZIP", "TAR.GZ", "TAR.BZ2"}:
        raise ValueError(f"Password protection is only supported for 7z archives, not {config.archive_fmt}.")
    if config.scan_mode not in SCAN_MODES:
        raise ValueError(f"Unsupported scan mode: {config.scan_mode}")
    if config.scan_error_mode not in {"strict", "best-effort"}:
        raise ValueError(f"Unsupported scan error mode: {config.scan_error_mode}")
    if config.archive_hash_mode not in ARCHIVE_HASH_MODES:
        raise ValueError(f"Unsupported archive hash mode: {config.archive_hash_mode}")
    if config.thread_strategy not in THREAD_STRATEGIES:
        raise ValueError(f"Unsupported thread strategy: {config.thread_strategy}")
    if config.progress_interval_ms < 0:
        raise ValueError("Progress interval must be >= 0.")
    if config.split_enabled and config.split_size_str:
        try:
            split_value = float(config.split_size_str)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid split size: {config.split_size_str!r}.") from exc
        if not (0.1 <= split_value <= 100_000):
            raise ValueError(f"Split size {split_value} GB is out of range [0.1, 100000].")

    source = safe_resolve(config.source_dir)
    output = safe_resolve(config.output_dir)
    if source != output:
        if is_relative_to(output, source):
            raise ValueError("Output directory cannot be inside source directory.")
        if is_relative_to(source, output):
            raise ValueError("Source directory cannot be inside output directory.")
    if config.signing_key_path and not config.signing_key_path.is_file():
        raise ValueError(f"Signing key not found: {config.signing_key_path}")
    if config.signing_certificate_path and not config.signing_certificate_path.is_file():
        raise ValueError(f"Signing certificate not found: {config.signing_certificate_path}")
    if config.seven_zip_path and not config.seven_zip_path.is_file():
        raise ValueError(f"Configured 7-Zip executable not found: {config.seven_zip_path}")


def report_paths(output_dir: Path) -> tuple[Path, Path, Path, Path]:
    metadata_dir = metadata_output_dir(output_dir)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = metadata_dir / f"ForensicPack_Report_{stamp}"
    return (
        stem.with_suffix(".txt"),
        stem.with_suffix(".csv"),
        stem.with_suffix(".json"),
        stem.with_suffix(".pdf"),
    )


def write_reports(paths: tuple[Path, Path, Path, Path], results: list[JobResult], config: JobConfig) -> None:
    from reporting import write_report_csv, write_report_json, write_report_pdf, write_report_txt

    txt_path, csv_path, json_path, pdf_path = paths
    info = system_info()
    write_report_txt(txt_path, results, config, info)
    write_report_csv(csv_path, results, config)
    if config.report_json:
        write_report_json(json_path, results, config, info)
    if config.report_pdf:
        write_report_pdf(pdf_path, results, config, info)


def _process_single_item(
    job_id: int,
    item_path: Path,
    config: JobConfig,
    token: CancellationToken,
    runtime: RuntimeState,
    callbacks: JobCallbacks,
    session_key: str | None = None,
    state_store: StateStore | None = None,
) -> JobResult:
    started_at = dt.datetime.now()
    result = _default_result(item_path, config)
    result.start_time = _now_iso()
    terminal_state: str | None = None

    metadata_dir = metadata_output_dir(config.output_dir)
    metadata_dir.mkdir(parents=True, exist_ok=True)
    expected_archive = expected_archive_path(item_path, config.output_dir, config.archive_fmt)
    existing_verify_path = split_entry_path(expected_archive, config)
    if config.skip_existing and existing_verify_path.exists():
        if verify_archive(existing_verify_path, config.archive_fmt, callbacks, job_id=job_id, token=token):
            result.end_time = _now_iso()
            result.file_count = "Skipped"
            result.source_size = "Skipped"
            result.archive_path = str(existing_verify_path)
            result.archive_size = output_size_bytes(expected_archive, config)
            result.verify = "SKIPPED (Verified Existing)"
            result.status = "skipped"
            if callbacks.item_status_cb:
                callbacks.item_status_cb(job_id, "skipped")
            return result

    if callbacks.item_status_cb:
        callbacks.item_status_cb(job_id, "running")
    callbacks.log_cb(f"  [START] {item_path.name}", "#58a6ff")

    final_manifest_name = f"{item_path.name}.manifest.txt"
    temp_manifest = metadata_dir / f"tmp_{job_id}_{item_path.name}_manifest.txt"
    temp_archive = config.output_dir / f"{item_path.name}{archive_suffix(config.archive_fmt)}.partial"
    temp_verify_path = split_entry_path(temp_archive, config)
    final_verify_path = split_entry_path(expected_archive, config)
    item_audit_path = metadata_dir / f"{item_path.name}.audit.jsonl"
    audit = AuditLogger(item_audit_path, enabled=config.audit_log)
    result.audit_log_path = str(item_audit_path) if config.audit_log else ""

    try:
        audit.record("job_started", source=str(item_path), output=str(expected_archive), format=config.archive_fmt)
        token.raise_if_requested(job_id)
        records, _total_size, scan_issues = build_forensic_inventory(
            item_path,
            job_id,
            token,
            callbacks,
            scan_mode=config.scan_mode,
            scan_error_mode=config.scan_error_mode,
        )
        result.scan_issues = scan_issues
        audit.record("inventory_completed", file_count=len(records), scan_issue_count=len(scan_issues))

        file_count, source_size, file_hashes = write_forensic_manifest(
            item_path,
            temp_manifest,
            records,
            config.hash_algorithms,
            config.case_metadata,
            job_id,
            token,
            callbacks,
            config.hash_threads,
            scan_issues,
        )
        result.file_count = file_count
        result.source_size = source_size
        audit.record("source_hashed", algorithms=config.hash_algorithms, bytes=source_size)

        if state_store and session_key:
            state_store.update_job(
                session_key,
                item_path,
                "manifested",
                verify="IN_PROGRESS",
                manifest_path=str(metadata_dir / final_manifest_name),
                file_count=file_count,
                source_size=source_size,
            )

        if config.dry_run:
            retained = metadata_dir / final_manifest_name
            if config.retain_manifests:
                shutil.copy2(temp_manifest, retained)
                result.manifest_path = str(retained)
            result.verify = "DRY-RUN"
            result.status = "warning" if scan_issues else "success"
            if scan_issues:
                result.verify = "DRY-RUN WITH WARNINGS"
                result.warnings.append(f"{len(scan_issues)} path(s) could not be fully inventoried.")
            terminal_state = "warning" if scan_issues else "done"
            audit.record("dry_run_completed", status=result.status)
            return result

        create_archive(
            job_id,
            item_path,
            records,
            temp_manifest,
            config,
            token,
            runtime,
            callbacks,
            temp_archive,
        )
        audit.record("archive_created", temporary_archive=str(temp_verify_path))
        callbacks.emit_progress(ProgressEvent(job_id, "verify", 0, 1, f"Verifying {item_path.name}"))
        if not verify_archive(temp_verify_path, config.archive_fmt, callbacks, job_id=job_id, token=token):
            raise RuntimeError("Archive structural verification failed.")
        audit.record("archive_structure_verified")

        if config.verify_member_hashes:
            member_ok, member_detail, member_count = verify_archive_member_hashes(
                temp_verify_path,
                records,
                file_hashes,
                config,
                token,
                callbacks,
                job_id,
                algorithm="SHA256",
            )
            result.archive_member_count = member_count
            result.content_verify = "PASS" if member_ok else f"FAILED: {member_detail}"
            audit.record("archive_member_hashes_verified", passed=member_ok, detail=member_detail)
            if not member_ok:
                raise RuntimeError(f"Archive member hash verification failed: {member_detail}")
        else:
            result.content_verify = "SKIPPED BY POLICY"
            result.warnings.append("Archive member hash verification was disabled.")

        rename_matching_outputs(temp_archive, expected_archive)
        if state_store and session_key:
            state_store.replace_parts(session_key, item_path, split_output_parts(expected_archive, config))
        result.archive_path = str(final_verify_path)
        result.archive_size = output_size_bytes(expected_archive, config)

        if config.archive_hash_mode == "always" and config.hash_algorithms:
            result.hashes = hash_file(final_verify_path, config.hash_algorithms, job_id=job_id, token=token)
        elif config.archive_hash_mode == "always":
            result.warnings.append("Archive hash skipped: no hash algorithms selected.")
        else:
            result.warnings.append("Archive hash skipped by policy.")
        audit.record("archive_finalized", archive=str(final_verify_path), size=result.archive_size, hashes=result.hashes)

        audit.record("package_completed", scan_issue_count=len(scan_issues))
        sidecars = write_package_sidecars(
            item_path,
            expected_archive,
            temp_manifest,
            records,
            file_hashes,
            scan_issues,
            config,
            result.content_verify,
            item_audit_path if config.audit_log else None,
            audit.final_hash,
        )
        result.manifest_path = sidecars["text_manifest"]
        result.external_manifest_json = sidecars["json_manifest"]
        result.checksum_path = sidecars["checksum"]
        result.signature_path = sidecars["signature"]

        result.verify = "PASS"
        result.status = "success"
        if scan_issues or result.warnings:
            result.verify = "PASS WITH WARNINGS"
            result.status = "warning"
        if scan_issues:
            result.warnings.append(f"{len(scan_issues)} path(s) could not be fully inventoried; see manifest.")

        if config.delete_source:
            callbacks.emit_progress(ProgressEvent(job_id, "delete", 0, 1, f"Deleting source {item_path.name}"))
            try:
                if item_path.is_file():
                    item_path.unlink()
                else:
                    shutil.rmtree(item_path)
            except OSError as exc:
                result.verify = "PASS WITH WARNINGS (SOURCE RETAINED)"
                result.status = "warning"
                result.warnings.append(f"Archive verified, but source cleanup failed; source retained. {exc}")
            else:
                callbacks.emit_progress(ProgressEvent(job_id, "delete", 1, 1, f"Deleted source {item_path.name}"))

        terminal_state = "warning" if result.status == "warning" else "done"
        callbacks.emit_progress(ProgressEvent(job_id, "done", 1, 1, f"Completed {item_path.name}"))
        callbacks.log_cb(
            f"  [DONE] {item_path.name} ({result.verify})",
            "#d29922" if result.status == "warning" else "#3fb950",
        )
        if state_store and session_key:
            state_store.update_job(
                session_key,
                item_path,
                "completed",
                verify=result.verify,
                archive_path=result.archive_path,
                manifest_path=result.manifest_path,
                file_count=file_count,
                source_size=source_size,
                archive_size=result.archive_size,
                warning_text=" | ".join(result.warnings),
            )
        return result
    except JobSkipped:
        runtime.kill_process(job_id)
        cleanup_partial_outputs(temp_archive)
        terminal_state = "skipped"
        result.verify = "SKIPPED"
        result.status = "skipped"
        result.warnings.append("Job skipped by operator.")
        audit.record("job_skipped")
        return result
    except JobCancelled:
        runtime.kill_process(job_id)
        cleanup_partial_outputs(temp_archive)
        terminal_state = "cancelled"
        result.verify = "CANCELLED"
        result.status = "cancelled"
        result.warnings.append("Job cancelled by operator.")
        audit.record("job_cancelled")
        return result
    except (OSError, RuntimeError, ValueError, EOFError) as exc:
        runtime.kill_process(job_id)
        cleanup_partial_outputs(temp_archive)
        terminal_state = "error"
        result.verify = "FAILED"
        result.status = "failed"
        result.warnings.append(str(exc))
        audit.record("job_failed", error_type=type(exc).__name__, error=str(exc))
        callbacks.log_cb(f"  [ERROR] {item_path.name}: {exc}", "#f85149")
        if callbacks.item_failure_cb:
            callbacks.item_failure_cb(job_id, str(exc))
        if state_store and session_key:
            state_store.update_job(
                session_key,
                item_path,
                "failed",
                verify=result.verify,
                warning_text=" | ".join(result.warnings),
                error_text=str(exc),
            )
        return result
    finally:
        if temp_manifest.exists():
            try:
                temp_manifest.unlink()
            except OSError:
                pass
        result.end_time = _now_iso()
        result.elapsed_seconds = (dt.datetime.now() - started_at).total_seconds()
        token.clear_skip(job_id)
        if callbacks.item_status_cb and terminal_state:
            callbacks.item_status_cb(job_id, terminal_state)


def run_session(
    config: JobConfig, callbacks: JobCallbacks, token: CancellationToken | None = None
) -> list[JobResult]:
    validate_config(config)
    token = token or CancellationToken()
    runtime = RuntimeState()
    callbacks.progress_interval_ms = config.progress_interval_ms
    config.output_dir.mkdir(parents=True, exist_ok=True)
    metadata_output_dir(config.output_dir).mkdir(parents=True, exist_ok=True)

    def force_cleanup() -> None:
        runtime.kill_all()
        cleanup_cancel_artifacts(config.output_dir)

    token.on_cancel(force_cleanup)
    items, excluded = classify_source_items(config.source_dir, config)
    config.excluded_generated_items = [path.name for path in excluded]
    if config.selected_item_names is not None:
        selected = set(config.selected_item_names)
        items = [item for item in items if item.name in selected]
    collisions = output_collisions(items, config, excluded)
    if collisions and config.fail_on_collision:
        raise ValueError("Output collision(s) detected:\n" + "\n".join(collisions))
    config.preflight_warnings = preflight_session(items, config)

    if config.excluded_generated_items:
        callbacks.log_cb(
            "  Excluded generated outputs: " + ", ".join(config.excluded_generated_items), "#8b949e"
        )
    for warning in config.preflight_warnings:
        callbacks.log_cb(f"  [WARN] {warning}", "#d29922")
    if not items:
        callbacks.log_cb("[ERROR] No eligible source items found.", "#f85149")
        return []

    state_store = open_state_store(resolve_state_db_path(config))
    session_key = session_profile_key(config)
    for item in items:
        try:
            state_store.upsert_discovered(session_key, item, config)
        except OSError as exc:
            callbacks.log_cb(f"  [WARN] Could not persist discovery state for {item}: {exc}", "#d29922")
    if callbacks.queue_cb:
        callbacks.queue_cb([item.name for item in items])

    results: dict[int, JobResult] = {}
    completed_prior = state_store.completed_items(session_key) if config.resume_enabled else {}
    if config.resume_enabled:
        from state_db import _item_key

        for index, item in enumerate(items):
            row = completed_prior.get(_item_key(item))
            if row is not None:
                results[index] = _result_from_resume_row(row, config)
                if callbacks.item_status_cb:
                    callbacks.item_status_cb(index, "skipped")
    config.resume_used = bool(results)
    pending = [(index, item) for index, item in enumerate(items) if index not in results]
    worker_count = select_worker_count(config, [item for _, item in pending]) if pending else 1

    callbacks.log_cb(f"\n{'=' * 60}", "#58a6ff")
    callbacks.log_cb(f"  {APP_NAME} v{APP_VERSION} - Session started", "#f0f6fc")
    callbacks.log_cb(f"  Parallel Threads: {worker_count}", "#bc8cff")
    callbacks.log_cb(f"  Scan Error Mode: {config.scan_error_mode}", "#8b949e")
    callbacks.log_cb(f"  Member Hash Verification: {'Yes' if config.verify_member_hashes else 'No'}", "#8b949e")
    callbacks.log_cb(f"{'=' * 60}\n", "#58a6ff")

    completed = len(results)
    callbacks.progress_overall_cb(completed / len(items))
    try:
        with ThreadPoolExecutor(max_workers=max(1, worker_count)) as executor:
            future_map = {
                executor.submit(
                    _process_single_item,
                    index,
                    item,
                    config,
                    token,
                    runtime,
                    callbacks,
                    session_key,
                    state_store,
                ): index
                for index, item in pending
            }
            for future in as_completed(future_map):
                index = future_map[future]
                results[index] = future.result()
                completed += 1
                callbacks.progress_overall_cb(completed / len(items))

        ordered = [results[index] for index in sorted(results)]
        paths = report_paths(config.output_dir)
        callbacks.status_cb("Writing session report ...")
        write_reports(paths, ordered, config)
        summary = summarize_job_results(ordered)
        callbacks.progress_overall_cb(1.0)
        callbacks.status_cb("Complete")
        callbacks.log_cb(
            f"  Summary: {summary['success']} success, {summary['warning']} warning, "
            f"{summary['failed']} failed, {summary['skipped']} skipped, {summary['cancelled']} cancelled",
            "#c9d1d9",
        )
        return ordered
    finally:
        cleanup_cancel_artifacts(config.output_dir)
        state_store.close()


def _archive_format_for_path(path: Path) -> str | None:
    name = path.name.lower()
    if name.endswith(".zip"):
        return "ZIP"
    if name.endswith(".tar.gz"):
        return "TAR.GZ"
    if name.endswith(".tar.bz2"):
        return "TAR.BZ2"
    if name.endswith(".7z") or name.endswith(".7z.001"):
        return "7z"
    return None


def _discover_verify_targets(input_path: Path) -> list[tuple[Path, str]]:
    if input_path.is_file():
        fmt = _archive_format_for_path(input_path)
        return [(input_path, fmt)] if fmt else []
    targets: list[tuple[Path, str]] = []
    for path in sorted(input_path.rglob("*"), key=lambda value: str(value).casefold()):
        if not path.is_file():
            continue
        fmt = _archive_format_for_path(path)
        if fmt:
            targets.append((path, fmt))
    return targets


def run_verify_session(
    verify_input: Path,
    output_dir: Path,
    callbacks: JobCallbacks,
    hash_algorithms: list[str] | None = None,
    report_json: bool = False,
    report_pdf: bool = False,
) -> list[JobResult]:
    verify_input = Path(verify_input)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_output_dir(output_dir).mkdir(parents=True, exist_ok=True)
    algorithms = normalize_hash_algorithms(hash_algorithms or ["SHA256"])
    targets = _discover_verify_targets(verify_input)
    if not targets:
        callbacks.log_cb("[ERROR] No supported archives found for verification.", "#f85149")
        return []
    if callbacks.queue_cb:
        callbacks.queue_cb([path.name for path, _ in targets])
    token = CancellationToken()

    def verify_item(index: int, path: Path, fmt: str) -> JobResult:
        start = _now_iso()
        if callbacks.item_status_cb:
            callbacks.item_status_cb(index, "running")
        passed = verify_archive(path, fmt, callbacks, job_id=index, token=token)
        result = JobResult(
            case_name=path.name,
            format=fmt,
            start_time=start,
            end_time=_now_iso(),
            file_count="N/A",
            source_size="N/A",
            archive_path=str(path),
            archive_size=path.stat().st_size if path.exists() else 0,
            verify="PASS" if passed else "FAILED",
            status="success" if passed else "failed",
            manifest_path="N/A",
        )
        if passed:
            result.hashes = hash_file(path, algorithms, job_id=index, token=token)
            source_name = path.name
            for suffix in (".7z.001", ".tar.bz2", ".tar.gz", ".zip", ".7z"):
                if source_name.lower().endswith(suffix):
                    source_name = source_name[: -len(suffix)]
                    break
            checksum_candidates = [
                path.parent / f"{source_name}.sha256",
                metadata_output_dir(path.parent) / f"{source_name}.sha256",
            ]
            checksum = next((candidate for candidate in checksum_candidates if candidate.is_file()), None)
            if checksum is not None:
                checksum_ok, issues = verify_checksum_file(checksum)
                if not checksum_ok:
                    result.status = "failed"
                    result.verify = "FAILED (CHECKSUM SIDECAR)"
                    result.warnings.extend(issues)
                else:
                    result.warnings.append("Package checksum sidecar verified.")
        else:
            result.warnings.append("Archive structural verification failed.")
        result.elapsed_seconds = (
            dt.datetime.fromisoformat(result.end_time) - dt.datetime.fromisoformat(result.start_time)
        ).total_seconds()
        if callbacks.item_status_cb:
            callbacks.item_status_cb(index, "done" if result.status == "success" else "error")
        return result

    ordered: dict[int, JobResult] = {}
    with ThreadPoolExecutor(max_workers=min(max(1, len(targets)), 4)) as executor:
        futures = {
            executor.submit(verify_item, index, path, fmt): index
            for index, (path, fmt) in enumerate(targets)
        }
        done_count = 0
        for future in as_completed(futures):
            index = futures[future]
            ordered[index] = future.result()
            done_count += 1
            callbacks.progress_overall_cb(done_count / len(targets))
    results = [ordered[index] for index in sorted(ordered)]
    config = JobConfig(
        source_dir=verify_input if verify_input.is_dir() else verify_input.parent,
        output_dir=output_dir,
        archive_fmt="MIXED",
        compress_level_label="N/A",
        split_enabled=False,
        split_size_str="",
        hash_algorithms=algorithms,
        password=None,
        delete_source=False,
        skip_existing=False,
        report_json=report_json,
        report_pdf=report_pdf,
        verify_member_hashes=False,
        preflight_space_check=False,
    )
    write_reports(report_paths(output_dir), results, config)
    return results


def process_cases(
    source_dir: str,
    output_dir: str,
    archive_fmt: str,
    compress_level_label: str,
    split_enabled: bool,
    split_size_str: str,
    hash_algorithms: list,
    password: str,
    delete_source: bool,
    skip_existing: bool = False,
    progress_overall_cb: Callable[[float], None] = lambda _fraction: None,
    progress_case_cb: Callable[[float], None] = lambda _fraction: None,
    log_cb: Callable[[str, str | None], None] = lambda _message, _colour=None: None,
    status_cb: Callable[[str], None] = lambda _text: None,
    cancel_flag: threading.Event | None = None,
    skip_current_flag: threading.Event | None = None,
    queue_cb: Callable[[list[str]], None] | None = None,
    item_status_cb: Callable[[int, str], None] | None = None,
    case_metadata: dict | None = None,
    threads: int = 1,
    item_progress_cb: Callable[[int, float, str], None] | None = None,
    item_failure_cb: Callable[[int, str], None] | None = None,
    scan_mode: Literal["deterministic", "fast"] = "deterministic",
    archive_hash_mode: Literal["always", "skip"] = "always",
    thread_strategy: Literal["fixed", "auto"] = "fixed",
    progress_interval_ms: int = 200,
    resume_enabled: bool = False,
    dry_run: bool = False,
    state_db_path: str | None = None,
    report_json: bool = False,
    embed_manifest_in_archive: bool = True,
) -> list[dict[str, object]]:
    config = JobConfig(
        source_dir=Path(source_dir),
        output_dir=Path(output_dir),
        archive_fmt=archive_fmt,
        compress_level_label=compress_level_label,
        split_enabled=split_enabled,
        split_size_str=split_size_str,
        hash_algorithms=list(hash_algorithms),
        password=password.strip() or None,
        delete_source=delete_source,
        skip_existing=skip_existing,
        case_metadata=case_metadata,
        threads=threads,
        scan_mode=scan_mode,
        archive_hash_mode=archive_hash_mode,
        thread_strategy=thread_strategy,
        progress_interval_ms=progress_interval_ms,
        resume_enabled=resume_enabled,
        dry_run=dry_run,
        state_db_path=Path(state_db_path) if state_db_path else None,
        report_json=report_json,
        embed_manifest_in_archive=embed_manifest_in_archive,
    )
    token = CancellationToken()
    callbacks = JobCallbacks(
        log_cb=log_cb,
        progress_overall_cb=progress_overall_cb,
        progress_case_cb=progress_case_cb,
        status_cb=status_cb,
        queue_cb=queue_cb,
        item_status_cb=item_status_cb,
        item_progress_cb=item_progress_cb,
        item_failure_cb=item_failure_cb,
    )
    results = run_session(config, callbacks, token)
    return [result.to_report_row() for result in results]
