import argparse
import os
import signal
from pathlib import Path

from engine import (
    ARCHIVE_HASH_MODES,
    ARCHIVE_FORMATS,
    COMPRESSION_LEVELS,
    SCAN_MODES,
    THREAD_STRATEGIES,
    CancellationToken,
    JobCallbacks,
    JobConfig,
    normalize_hash_algorithms,
    run_verify_session,
    run_session,
)


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

    pack = sub.add_parser("pack", help="Run a headless archiving session")
    pack.add_argument("--source", required=True, type=Path)
    pack.add_argument("--output", required=True, type=Path)
    pack.add_argument("--format", required=True, choices=["7z", "ZIP", "TAR.GZ", "TAR.BZ2", "zip", "tar.gz", "tar.bz2"])
    pack.add_argument("--compression", default="Normal (5)", choices=list(COMPRESSION_LEVELS))
    pack.add_argument("--hash", dest="hashes", action="append", default=[], help="Optional repeatable hash algorithm value, e.g. --hash SHA256")
    pack.add_argument("--threads", type=int, default=min(4, os.cpu_count() or 1))
    pack.add_argument("--password", default="")
    pack.add_argument("--delete-source", action="store_true")
    pack.add_argument("--skip-existing", action="store_true")
    pack.add_argument("--split-size", default="")
    pack.add_argument("--split", action="store_true")
    pack.add_argument("--resume", action="store_true", help="Resume from SQLite state store and skip completed items.")
    pack.add_argument("--dry-run", action="store_true", help="Build inventory/manifest only; do not archive.")
    pack.add_argument("--scan-mode", default="deterministic", choices=SCAN_MODES)
    pack.add_argument("--archive-hash-mode", default="always", choices=ARCHIVE_HASH_MODES)
    pack.add_argument("--thread-strategy", default="fixed", choices=THREAD_STRATEGIES)
    pack.add_argument("--progress-interval-ms", type=int, default=200)
    pack.add_argument("--state-db", type=Path, default=None, help="Optional path to SQLite state database.")
    pack.add_argument("--report-json", action="store_true", help="Write JSON report alongside TXT/CSV.")
    pack.add_argument("--no-embed-manifest", action="store_true", help="Do not embed manifest report inside archive.")
    pack.add_argument("--hash-threads", type=int, default=4, help="Parallel threads for file hashing within each job (default: 4).")
    pack.add_argument("--examiner", default="")
    pack.add_argument("--case-id", default="")
    pack.add_argument("--evidence-id", default="")
    pack.add_argument("--notes", default="")

    verify = sub.add_parser("verify", help="Verify existing archives without repacking")
    verify.add_argument("--input", required=True, type=Path, help="Archive path or directory to verify.")
    verify.add_argument("--output", type=Path, default=None, help="Output directory for verify reports.")
    verify.add_argument("--hash", dest="hashes", action="append", default=["SHA256"])
    verify.add_argument("--report-json", action="store_true", help="Write JSON report alongside TXT/CSV.")
    return parser


def normalize_format(value: str) -> str:
    mapping = {"zip": "ZIP", "tar.gz": "TAR.GZ", "tar.bz2": "TAR.BZ2"}
    return mapping.get(value, value)


def run_cli(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command in {None, "gui"}:
        from gui import launch_gui

        launch_gui()
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
        )
        failed = any(result.is_failure for result in results)
        return 1 if failed else 0

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
        archive_hash_mode=args.archive_hash_mode,
        thread_strategy=args.thread_strategy,
        progress_interval_ms=args.progress_interval_ms,
        resume_enabled=args.resume,
        dry_run=args.dry_run,
        state_db_path=args.state_db,
        report_json=args.report_json,
        embed_manifest_in_archive=not args.no_embed_manifest,
        hash_threads=max(1, args.hash_threads),
    )
    token = CancellationToken()

    def _sigint_handler(sig, frame):
        print("\n[!] Interrupt received — cancelling jobs and cleaning up ...")
        token.request_cancel()

    prev_handler = signal.signal(signal.SIGINT, _sigint_handler)
    try:
        results = run_session(config, _console_callbacks(), token)
    finally:
        signal.signal(signal.SIGINT, prev_handler)
    failed = any(result.causes_nonzero_exit for result in results)
    return 1 if failed else 0
