import argparse
import json
import os
import signal
from pathlib import Path

from engine import (
    ARCHIVE_FORMATS,
    ARCHIVE_HASH_MODES,
    COMPRESSION_LEVELS,
    SCAN_MODES,
    THREAD_STRATEGIES,
    CancellationToken,
    JobCallbacks,
    JobConfig,
    normalize_hash_algorithms,
    run_session,
    run_verify_session,
)
from transfer import copy_with_verification


def _console_callbacks() -> JobCallbacks:
    return JobCallbacks(
        log_cb=lambda message, _colour=None: print(message),
        progress_overall_cb=lambda _fraction: None,
        progress_case_cb=lambda _fraction: None,
        status_cb=lambda message: print(message),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="forensicpack.py", description="ForensicPack DFIR archiver")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("gui", help="Launch the GUI")

    pack = sub.add_parser("pack", help="Run a headless evidence packaging session")
    pack.add_argument("--source", required=True, type=Path)
    pack.add_argument("--output", required=True, type=Path)
    pack.add_argument(
        "--format",
        required=True,
        choices=["7z", "ZIP", "TAR.GZ", "TAR.BZ2", "zip", "tar.gz", "tar.bz2"],
    )
    pack.add_argument("--compression", default="Normal (5)", choices=list(COMPRESSION_LEVELS))
    pack.add_argument("--hash", dest="hashes", action="append", default=[])
    pack.add_argument("--threads", type=int, default=min(4, os.cpu_count() or 1))
    pack.add_argument("--hash-threads", type=int, default=4)
    pack.add_argument("--password", default="")
    pack.add_argument("--delete-source", action="store_true")
    pack.add_argument("--skip-existing", action="store_true")
    pack.add_argument("--split-size", default="")
    pack.add_argument("--split", action="store_true")
    pack.add_argument("--resume", action="store_true")
    pack.add_argument("--dry-run", action="store_true")
    pack.add_argument("--scan-mode", default="deterministic", choices=SCAN_MODES)
    pack.add_argument("--scan-errors", default="best-effort", choices=["strict", "best-effort"])
    pack.add_argument("--archive-hash-mode", default="always", choices=ARCHIVE_HASH_MODES)
    pack.add_argument("--thread-strategy", default="fixed", choices=THREAD_STRATEGIES)
    pack.add_argument("--progress-interval-ms", type=int, default=200)
    pack.add_argument("--state-db", type=Path, default=None)
    pack.add_argument("--report-json", action="store_true")
    pack.add_argument("--report-pdf", action="store_true")
    pack.add_argument("--no-embed-manifest", action="store_true")
    pack.add_argument("--no-retain-manifests", action="store_true")
    pack.add_argument("--no-member-hash-verify", action="store_true")
    pack.add_argument("--include-generated-outputs", action="store_true")
    pack.add_argument("--allow-output-collision", action="store_true")
    pack.add_argument("--no-preflight", action="store_true")
    pack.add_argument("--no-audit-log", action="store_true")
    pack.add_argument("--7zip-path", type=Path, default=None)
    pack.add_argument("--signing-key", type=Path, default=None, help="PEM private key for manifest signing.")
    pack.add_argument("--signing-certificate", type=Path, default=None)
    pack.add_argument("--agency-logo", type=Path, default=None)
    pack.add_argument("--examiner", default="")
    pack.add_argument("--case-id", default="")
    pack.add_argument("--evidence-id", default="")
    pack.add_argument("--notes", default="")

    verify = sub.add_parser("verify", help="Verify existing archives and package checksums")
    verify.add_argument("--input", required=True, type=Path)
    verify.add_argument("--output", type=Path, default=None)
    verify.add_argument("--hash", dest="hashes", action="append", default=["SHA256"])
    verify.add_argument("--report-json", action="store_true")
    verify.add_argument("--report-pdf", action="store_true")

    transfer = sub.add_parser("transfer-verify", help="Copy evidence and verify destination hashes")
    transfer.add_argument("--source", required=True, type=Path)
    transfer.add_argument("--destination", required=True, type=Path)
    transfer.add_argument("--hash", dest="hashes", action="append", default=["SHA256"])
    transfer.add_argument("--report", type=Path, default=None, help="Optional JSON transfer report path.")
    return parser


def normalize_format(value: str) -> str:
    return {"zip": "ZIP", "tar.gz": "TAR.GZ", "tar.bz2": "TAR.BZ2"}.get(value, value)


def run_cli(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in {None, "gui"}:
        from gui import launch_gui

        launch_gui()
        return 0

    if args.command == "transfer-verify":
        try:
            algorithms = normalize_hash_algorithms(args.hashes)
            records = copy_with_verification(args.source, args.destination, algorithms)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"[ERROR] {exc}")
            return 1
        payload = [record.__dict__ for record in records]
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Verified {len(records)} transferred file(s).")
        return 0

    if args.command == "verify":
        try:
            hashes = normalize_hash_algorithms(args.hashes)
        except ValueError as exc:
            parser.error(str(exc))
        verify_output = args.output or (args.input if args.input.is_dir() else args.input.parent)
        results = run_verify_session(
            verify_input=args.input,
            output_dir=verify_output,
            callbacks=_console_callbacks(),
            hash_algorithms=hashes,
            report_json=args.report_json,
            report_pdf=args.report_pdf,
        )
        return 1 if any(result.is_failure for result in results) else 0

    case_metadata = {
        "Examiner": args.examiner,
        "Case ID": args.case_id,
        "Evidence ID": args.evidence_id,
        "Notes": args.notes,
    }
    if not any(case_metadata.values()):
        case_metadata = None
    try:
        hashes = normalize_hash_algorithms(args.hashes)
    except ValueError as exc:
        parser.error(str(exc))

    config = JobConfig(
        source_dir=args.source,
        output_dir=args.output,
        archive_fmt=normalize_format(args.format),
        compress_level_label=args.compression,
        split_enabled=args.split,
        split_size_str=args.split_size,
        hash_algorithms=hashes,
        password=args.password or None,
        delete_source=args.delete_source,
        skip_existing=args.skip_existing,
        case_metadata=case_metadata,
        threads=max(1, args.threads),
        scan_mode=args.scan_mode,
        scan_error_mode=args.scan_errors,
        archive_hash_mode=args.archive_hash_mode,
        thread_strategy=args.thread_strategy,
        progress_interval_ms=args.progress_interval_ms,
        resume_enabled=args.resume,
        dry_run=args.dry_run,
        state_db_path=args.state_db,
        report_json=args.report_json,
        report_pdf=args.report_pdf,
        embed_manifest_in_archive=not args.no_embed_manifest,
        retain_manifests=not args.no_retain_manifests,
        verify_member_hashes=not args.no_member_hash_verify,
        exclude_generated_outputs=not args.include_generated_outputs,
        fail_on_collision=not args.allow_output_collision,
        preflight_space_check=not args.no_preflight,
        audit_log=not args.no_audit_log,
        seven_zip_path=args.__dict__.get("7zip_path"),
        signing_key_path=args.signing_key,
        signing_certificate_path=args.signing_certificate,
        agency_logo_path=args.agency_logo,
        hash_threads=max(1, args.hash_threads),
    )
    token = CancellationToken()

    def sigint_handler(_signal, _frame):
        print("\n[!] Interrupt received - cancelling jobs and cleaning up ...")
        token.request_cancel()

    previous_handler = signal.signal(signal.SIGINT, sigint_handler)
    try:
        results = run_session(config, _console_callbacks(), token)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}")
        return 1
    finally:
        signal.signal(signal.SIGINT, previous_handler)
    return 1 if any(result.causes_nonzero_exit for result in results) else 0
