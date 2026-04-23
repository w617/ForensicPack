import datetime as dt
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Literal

from models import CancellationToken, JobCallbacks, JobConfig, JobResult, ProgressEvent, RuntimeState, JobSkipped, JobCancelled, summarize_job_results
from archivers import verify_archive, create_archive
from hashing import build_inventory, hash_file, write_manifest
from state_db import StateStore, open_state_store
from utils import (
    ARCHIVE_FORMATS, ARCHIVE_HASH_MODES, SCAN_MODES, THREAD_STRATEGIES,
    archive_suffix, cleanup_cancel_artifacts,
    cleanup_partial_outputs, expected_archive_path, normalize_hash_algorithms,
    output_size_bytes, rename_matching_outputs, safe_resolve, select_worker_count, session_profile_key, system_info,
    split_entry_path, split_output_parts, resolve_state_db_path
)

from version import APP_NAME, APP_VERSION

def _default_result(item_path: Path, config: JobConfig) -> JobResult:
    return JobResult(
        case_name=item_path.name,
        format=config.archive_fmt,
        start_time=dt.datetime.now().isoformat(timespec="seconds"),
        end_time="",
        file_count=0,
        source_size=0,
        archive_path="",
        archive_size=0,
        verify="FAILED",
        status="failed",
    )

def _result_from_resume_row(row: sqlite3.Row, config: JobConfig) -> JobResult:
    warnings: list[str] = []
    if row["warning_text"]:
        warnings.append(row["warning_text"])
    warnings.append("Resumed from prior completed state.")
    result = JobResult(
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
    return result

def _process_single_item(
    job_id: int, 
    item_path: Path, 
    config: JobConfig, 
    token: CancellationToken, 
    runtime: RuntimeState,
    callbacks: JobCallbacks, 
    session_key: str | None = None, 
    state_store: StateStore | None = None
) -> JobResult:
    start = dt.datetime.now().isoformat(timespec="seconds")
    started_at = dt.datetime.now()
    terminal_state: str | None = None
    result = _default_result(item_path, config)
    result.start_time = start

    expected_archive = expected_archive_path(item_path, config.output_dir, config.archive_fmt)
    existing_verify_path = split_entry_path(expected_archive, config)
    if config.skip_existing and existing_verify_path.exists():
        existing_ok = verify_archive(existing_verify_path, config.archive_fmt, callbacks, job_id=job_id, token=token)
        if existing_ok:
            result.end_time = dt.datetime.now().isoformat(timespec="seconds")
            result.file_count = "Skipped"
            result.source_size = "Skipped"
            result.archive_path = str(existing_verify_path)
            result.archive_size = output_size_bytes(expected_archive, config)
            result.verify = "SKIPPED (Verified Existing)"
            result.status = "skipped"
            if callbacks.item_status_cb:
                callbacks.item_status_cb(job_id, "skipped")
            if state_store and session_key:
                state_store.update_job(
                    session_key,
                    item_path,
                    "skipped",
                    verify=result.verify,
                    archive_path=result.archive_path,
                    archive_size=result.archive_size,
                )
                state_store.replace_parts(session_key, item_path, split_output_parts(expected_archive, config))
            return result

    if callbacks.item_status_cb:
        callbacks.item_status_cb(job_id, "running")
    callbacks.log_cb(f"  [START] {item_path.name}", "#58a6ff")

    final_manifest_name = f"{item_path.name}_manifest.txt"
    manifest_path = config.output_dir / f"tmp_{job_id}_{final_manifest_name}"
    temp_archive = config.output_dir / f"{item_path.name}{archive_suffix(config.archive_fmt)}.partial"
    final_archive = expected_archive
    temp_verify_path = split_entry_path(temp_archive, config)
    final_verify_path = split_entry_path(final_archive, config)

    try:
        token.raise_if_requested(job_id)
        inventory, total_size = build_inventory(item_path, job_id, token, callbacks, scan_mode=config.scan_mode)
        file_count, source_size = write_manifest(
            item_path,
            manifest_path,
            inventory,
            config.hash_algorithms,
            config.case_metadata,
            job_id,
            token,
            callbacks,
            hash_threads=config.hash_threads,
        )
        result.file_count = file_count
        result.source_size = source_size
        if state_store and session_key:
            state_store.update_job(
                session_key,
                item_path,
                "manifested",
                verify="IN_PROGRESS",
                manifest_path=final_manifest_name,
                file_count=file_count,
                source_size=source_size,
            )
        if config.dry_run:
            result.verify = "DRY-RUN"
            result.status = "success"
            result.archive_path = ""
            result.archive_size = 0
            result.warnings.append("Dry-run mode: archive creation skipped.")
            terminal_state = "done"
            callbacks.emit_progress(ProgressEvent(job_id, "done", 1, 1, f"Planned {item_path.name}"))
            callbacks.log_cb(f"  [DONE] {item_path.name} (DRY-RUN)", "#d29922")
            if state_store and session_key:
                state_store.update_job(
                    session_key,
                    item_path,
                    "skipped",
                    verify=result.verify,
                    manifest_path=final_manifest_name,
                    file_count=file_count,
                    source_size=source_size,
                )
            return result
        create_archive(job_id, item_path, inventory, manifest_path, config, token, runtime, callbacks, temp_archive)
        if state_store and session_key:
            state_store.update_job(
                session_key,
                item_path,
                "archived",
                verify="IN_PROGRESS",
                manifest_path=final_manifest_name,
                file_count=file_count,
                source_size=source_size,
            )
        callbacks.emit_progress(ProgressEvent(job_id, "verify", 0, 1, f"Verifying {item_path.name}"))
        verify_ok = verify_archive(temp_verify_path, config.archive_fmt, callbacks, job_id=job_id, token=token)
        if not verify_ok:
            raise RuntimeError("Archive verification failed.")
        rename_matching_outputs(temp_archive, final_archive)
        if state_store and session_key:
            state_store.replace_parts(session_key, item_path, split_output_parts(final_archive, config))
        result.archive_path = str(final_verify_path)
        result.archive_size = output_size_bytes(final_archive, config)
        result.verify = "PASS"
        result.status = "success"
        if state_store and session_key:
            state_store.update_job(
                session_key,
                item_path,
                "verified",
                verify=result.verify,
                archive_path=result.archive_path,
                manifest_path=final_manifest_name,
                file_count=file_count,
                source_size=source_size,
                archive_size=result.archive_size,
            )
        if config.archive_hash_mode == "always" and config.hash_algorithms:
            callbacks.emit_progress(ProgressEvent(job_id, "hash_archive", 0, max(result.archive_size, 1), f"Hashing archive {item_path.name}"))
            bytes_done = 0

            def _archive_hash_progress(inc: int) -> None:
                nonlocal bytes_done
                bytes_done += inc
                callbacks.emit_progress(
                    ProgressEvent(
                        job_id,
                        "hash_archive",
                        min(result.archive_size, bytes_done),
                        max(result.archive_size, 1),
                        f"Hashing archive {item_path.name}",
                    )
                )

            result.hashes = hash_file(
                final_verify_path,
                config.hash_algorithms,
                job_id=job_id,
                token=token,
                progress_cb=_archive_hash_progress,
            )
            bytes_done = result.archive_size
            callbacks.emit_progress(ProgressEvent(job_id, "hash_archive", bytes_done, max(result.archive_size, 1), f"Hashing archive {item_path.name}"))
        elif config.archive_hash_mode == "always":
            result.warnings.append("Archive hash skipped: no hash algorithms selected.")
        else:
            result.warnings.append("Archive hash skipped by policy.")
        if state_store and session_key:
            state_store.update_job(
                session_key,
                item_path,
                "hashed",
                verify=result.verify,
                archive_path=result.archive_path,
                manifest_path=final_manifest_name,
                file_count=file_count,
                source_size=source_size,
                archive_size=result.archive_size,
                warning_text=" | ".join(result.warnings),
            )
        if config.delete_source:
            callbacks.emit_progress(ProgressEvent(job_id, "delete", 0, 1, f"Deleting source {item_path.name}"))
            try:
                if item_path.is_file():
                    item_path.unlink()
                else:
                    shutil.rmtree(item_path)
            except OSError as exc:
                detail = f"Archive verified, but source cleanup failed; source retained. {exc}"
                result.verify = "PASS (SOURCE RETAINED)"
                result.status = "warning"
                result.warnings.append(detail)
                terminal_state = "warning"
                callbacks.log_cb(f"  [WARN] {item_path.name}: {detail}", "#d29922")
                if callbacks.item_failure_cb:
                    callbacks.item_failure_cb(job_id, detail)
            else:
                callbacks.emit_progress(ProgressEvent(job_id, "delete", 1, 1, f"Deleted source {item_path.name}"))
        callbacks.emit_progress(ProgressEvent(job_id, "done", 1, 1, f"Completed {item_path.name}"))
        if terminal_state == "warning":
            callbacks.log_cb(f"  [DONE] {item_path.name} ({result.verify})", "#d29922")
        else:
            callbacks.log_cb(f"  [DONE] {item_path.name} (PASS)", "#3fb950")
            terminal_state = "done"
        if state_store and session_key:
            state_store.update_job(
                session_key,
                item_path,
                "completed",
                verify=result.verify,
                archive_path=result.archive_path,
                manifest_path=final_manifest_name,
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
        callbacks.log_cb(f"  [SKIP] {item_path.name}", "#d29922")
        if state_store and session_key:
            state_store.update_job(
                session_key,
                item_path,
                "skipped",
                verify=result.verify,
                warning_text=" | ".join(result.warnings),
            )
        return result
    except JobCancelled:
        runtime.kill_process(job_id)
        cleanup_partial_outputs(temp_archive)
        terminal_state = "cancelled"
        result.verify = "CANCELLED"
        result.status = "cancelled"
        result.warnings.append("Job cancelled by operator.")
        callbacks.log_cb(f"  [CANCEL] {item_path.name}", "#d29922")
        if state_store and session_key:
            state_store.update_job(
                session_key,
                item_path,
                "cancelled",
                verify=result.verify,
                warning_text=" | ".join(result.warnings),
            )
        return result
    except Exception as exc:
        runtime.kill_process(job_id)
        cleanup_partial_outputs(temp_archive)
        terminal_state = "error"
        result.verify = "FAILED"
        result.status = "failed"
        result.warnings.append(str(exc))
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
        if manifest_path.exists():
            try:
                manifest_path.unlink()
            except OSError:
                pass
        result.end_time = dt.datetime.now().isoformat(timespec="seconds")
        result.elapsed_seconds = (dt.datetime.now() - started_at).total_seconds()
        result.manifest_path = final_manifest_name
        token.clear_skip(job_id)
        if callbacks.item_status_cb and terminal_state:
            callbacks.item_status_cb(job_id, terminal_state)


def validate_config(config: JobConfig) -> None:
    from utils import is_relative_to
    if not config.source_dir.is_dir():
        raise ValueError(f"Source directory not found: {config.source_dir}")
    if config.archive_fmt not in ARCHIVE_FORMATS:
        raise ValueError(f"Unsupported archive format: {config.archive_fmt}")
    config.hash_algorithms = normalize_hash_algorithms(config.hash_algorithms)
    unsupported_password = config.password and config.archive_fmt in {"ZIP", "TAR.GZ", "TAR.BZ2"}
    if unsupported_password:
        raise ValueError(f"Password protection is only supported for 7z archives, not {config.archive_fmt}.")
    if config.scan_mode not in SCAN_MODES:
        raise ValueError(f"Unsupported scan mode: {config.scan_mode}")
    if config.archive_hash_mode not in ARCHIVE_HASH_MODES:
        raise ValueError(f"Unsupported archive hash mode: {config.archive_hash_mode}")
    if config.thread_strategy not in THREAD_STRATEGIES:
        raise ValueError(f"Unsupported thread strategy: {config.thread_strategy}")
    if config.progress_interval_ms < 0:
        raise ValueError("Progress interval must be >= 0.")
    source_resolved = safe_resolve(config.source_dir)
    output_resolved = safe_resolve(config.output_dir)
    if source_resolved == output_resolved:
        raise ValueError("Source and output directories must be different.")
    if is_relative_to(output_resolved, source_resolved):
        raise ValueError("Output directory cannot be inside source directory.")
    if is_relative_to(source_resolved, output_resolved):
        raise ValueError("Source directory cannot be inside output directory.")

def report_paths(output_dir: Path) -> tuple[Path, Path, Path]:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = output_dir / f"ForensicPack_Report_{stamp}"
    return stem.with_suffix(".txt"), stem.with_suffix(".csv"), stem.with_suffix(".json")

def write_reports(txt_path: Path, csv_path: Path, json_path: Path, results: list[JobResult], config: JobConfig) -> None:
    from reporting import write_report_csv, write_report_json, write_report_txt

    info = system_info()
    write_report_txt(txt_path, results, config, info)
    write_report_csv(csv_path, results, config)
    if config.report_json:
        write_report_json(json_path, results, config, info)

def run_session(config: JobConfig, callbacks: JobCallbacks, token: CancellationToken | None = None) -> list[JobResult]:
    validate_config(config)
    token = token or CancellationToken()
    runtime = RuntimeState()
    
    def _force_cancel_cleanup() -> None:
        runtime.kill_all()
        removed = cleanup_cancel_artifacts(config.output_dir)
        if removed:
            callbacks.log_cb(f"  [CANCEL] Removed {removed} temporary artifact(s).", "#d29922")

    token.on_cancel(_force_cancel_cleanup)
    callbacks.progress_interval_ms = config.progress_interval_ms
    config.output_dir.mkdir(parents=True, exist_ok=True)
    state_store = open_state_store(resolve_state_db_path(config))
    session_key = session_profile_key(config)
    items = sorted(config.source_dir.iterdir())
    
    if config.selected_item_names is not None:
        selected_lookup = set(config.selected_item_names)
        items = [item for item in items if item.name in selected_lookup]
        
    if not items:
        if config.selected_item_names is None:
            callbacks.log_cb("[ERROR] No items found in source directory.", "#f85149")
        else:
            callbacks.log_cb("[ERROR] No selected items found in source directory.", "#f85149")
        state_store.close()
        return []
        
    for item in items:
        state_store.upsert_discovered(session_key, item, config)
    if callbacks.queue_cb:
        callbacks.queue_cb([item.name for item in items])

    results: dict[int, JobResult] = {}
    completed_prior = state_store.completed_items(session_key) if config.resume_enabled else {}
    
    if config.resume_enabled:
        from state_db import _item_key
        for idx, item in enumerate(items):
            row = completed_prior.get(_item_key(item))
            if row is None:
                continue
            results[idx] = _result_from_resume_row(row, config)
            if callbacks.item_status_cb:
                callbacks.item_status_cb(idx, "skipped")
            if callbacks.item_progress_cb:
                callbacks.item_progress_cb(idx, 1.0, "resume")
                
    config.resume_used = bool(results)
    pending = [(idx, item) for idx, item in enumerate(items) if idx not in results]
    worker_count = select_worker_count(config, [item for _, item in pending]) if pending else 1

    callbacks.log_cb(f"\n{'=' * 60}", "#58a6ff")
    callbacks.log_cb(f"  {APP_NAME} v{APP_VERSION} - Session started", "#f0f6fc")
    callbacks.log_cb(f"  Parallel Threads: {worker_count}", "#bc8cff")
    callbacks.log_cb(f"  Scan Mode: {config.scan_mode} | Archive Hash: {config.archive_hash_mode}", "#8b949e")
    callbacks.log_cb(f"  Thread Strategy: {config.thread_strategy} | Resume: {'Yes' if config.resume_enabled else 'No'}", "#8b949e")
    callbacks.log_cb(f"{'=' * 60}\n", "#58a6ff")

    completed = len(results)
    callbacks.progress_overall_cb(completed / len(items))
    try:
        with ThreadPoolExecutor(max_workers=max(1, worker_count)) as executor:
            future_map = {
                executor.submit(_process_single_item, idx, item, config, token, runtime, callbacks, session_key, state_store): idx
                for idx, item in pending
            }
            try:
                for future in as_completed(future_map):
                    idx = future_map[future]
                    results[idx] = future.result()
                    completed += 1
                    callbacks.progress_overall_cb(completed / len(items))
                    if token.state_for(idx) == "cancel":
                        runtime.kill_all()
            except KeyboardInterrupt:
                token.request_cancel()
                runtime.kill_all()
                raise
        ordered_results = [results[idx] for idx in sorted(results)]
        txt_path, csv_path, json_path = report_paths(config.output_dir)
        callbacks.status_cb("Writing session report ...")
        write_reports(txt_path, csv_path, json_path, ordered_results, config)
        summary = summarize_job_results(ordered_results)
        callbacks.progress_overall_cb(1.0)
        callbacks.status_cb("Complete")
        callbacks.log_cb(f"\n{'=' * 60}", "#58a6ff")
        callbacks.log_cb(f"  Session complete - {len(ordered_results)} item(s) processed", "#f0f6fc")
        callbacks.log_cb(
            f"  Summary: {summary['failed']} failed, {summary['warning']} warning, {summary['skipped']} skipped, {summary['cancelled']} cancelled",
            "#c9d1d9",
        )
        if config.report_json:
            callbacks.log_cb(f"  Report: {txt_path.name} / {csv_path.name} / {json_path.name}", "#c9d1d9")
        else:
            callbacks.log_cb(f"  Report: {txt_path.name} / {csv_path.name}", "#c9d1d9")
        callbacks.log_cb(f"{'=' * 60}\n", "#58a6ff")
        return ordered_results
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
    for path in sorted(input_path.rglob("*")):
        if not path.is_file():
            continue
        fmt = _archive_format_for_path(path)
        if not fmt:
            continue
        lower = path.name.lower()
        if lower.endswith(".7z.001") or (fmt != "7z"):
            targets.append((path, fmt))
        elif lower.endswith(".7z"):
            targets.append((path, fmt))
    return targets

def run_verify_session(
    verify_input: Path,
    output_dir: Path,
    callbacks: JobCallbacks,
    hash_algorithms: list[str] | None = None,
    report_json: bool = False,
) -> list[JobResult]:
    verify_input = Path(verify_input)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    algorithms = normalize_hash_algorithms(hash_algorithms or ["SHA256"])
    targets = _discover_verify_targets(verify_input)
    if not targets:
        callbacks.log_cb("[ERROR] No supported archives found for verification.", "#f85149")
        return []
    if callbacks.queue_cb:
        callbacks.queue_cb([path.name for path, _fmt in targets])
    token = CancellationToken()
    callbacks.progress_interval_ms = 200

    def _now_iso() -> str:
        return dt.datetime.now().isoformat(timespec="seconds")
    
    def _verify_item(idx: int, path: Path, fmt: str) -> JobResult:
        start = _now_iso()
        if callbacks.item_status_cb:
            callbacks.item_status_cb(idx, "running")
        callbacks.emit_progress(ProgressEvent(idx, "verify", 0, 1, f"Verifying {path.name}"))
        passed = verify_archive(path, fmt, callbacks, job_id=idx, token=token)
        
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
            result.hashes = hash_file(path, algorithms, job_id=idx, token=token)
            callbacks.emit_progress(ProgressEvent(idx, "verify", 1, 1, f"Verified {path.name}"))
        else:
            result.warnings.append("Verification failed.")
            if callbacks.item_failure_cb:
                callbacks.item_failure_cb(idx, "Verification failed.")

        result.elapsed_seconds = (
            dt.datetime.fromisoformat(result.end_time) - dt.datetime.fromisoformat(result.start_time)
        ).total_seconds()
        if callbacks.item_status_cb:
            callbacks.item_status_cb(idx, "done" if result.status == "success" else "error")
        return result

    ordered_results: dict[int, JobResult] = {}
    worker_count = min(max(1, len(targets)), 4)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(_verify_item, idx, path, fmt): idx for idx, (path, fmt) in enumerate(targets)}
        done_count = 0
        for future in as_completed(futures):
            idx = futures[future]
            ordered_results[idx] = future.result()
            done_count += 1
            callbacks.progress_overall_cb(done_count / len(targets))

    results = [ordered_results[idx] for idx in sorted(ordered_results)]

    cfg = JobConfig(
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
    )
    cfg.resume_used = False
    txt_path, csv_path, json_path = report_paths(output_dir)
    write_reports(txt_path, csv_path, json_path, results, cfg)
    callbacks.log_cb(f"  Verify report: {txt_path.name} / {csv_path.name}" + (f" / {json_path.name}" if report_json else ""), "#c9d1d9")
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
    running_jobs: set[int] = set()
    state_lock = threading.Lock()
    last_started_job_id: int | None = None

    def _item_status_proxy(idx: int, state: str) -> None:
        nonlocal last_started_job_id
        with state_lock:
            if state == "running":
                running_jobs.add(idx)
                last_started_job_id = idx
            elif state in {"done", "warning", "error", "skipped", "cancelled"}:
                running_jobs.discard(idx)
        if item_status_cb:
            item_status_cb(idx, state)

    def _watch_flags() -> None:
        last_skip_request_state = False
        while True:
            if cancel_flag and cancel_flag.is_set():
                token.request_cancel()
                break
            if skip_current_flag:
                skip_state = skip_current_flag.is_set()
                if skip_state and not last_skip_request_state:
                    target: int | None = None
                    with state_lock:
                        if running_jobs:
                            target = max(running_jobs)
                        elif last_started_job_id is not None:
                            target = last_started_job_id
                    if target is not None:
                        token.request_skip(target)
                last_skip_request_state = skip_state
            if getattr(threading.current_thread(), "_stop_requested", False):
                break
            
            # Use wait instead of sleep for optimized CPU load
            if cancel_flag and skip_current_flag:
                # Event.wait is better but we need to check both flags
                # Fallback to a small sleep if wait isn't viable across two events directly
                import time
                time.sleep(0.05)
            elif cancel_flag:
                if cancel_flag.wait(timeout=0.05):
                    continue
            else:
                import time
                time.sleep(0.05)

    watcher = None
    if cancel_flag or skip_current_flag:
        watcher = threading.Thread(target=_watch_flags, daemon=True)
        watcher.start()

    callbacks = JobCallbacks(
        log_cb=log_cb,
        progress_overall_cb=progress_overall_cb,
        progress_case_cb=progress_case_cb,
        status_cb=status_cb,
        queue_cb=queue_cb,
        item_status_cb=_item_status_proxy,
        item_progress_cb=item_progress_cb,
        item_failure_cb=item_failure_cb,
    )
    results = run_session(config, callbacks, token)
    if watcher is not None:
        setattr(watcher, "_stop_requested", True)
    return [result.to_report_row() for result in results]
