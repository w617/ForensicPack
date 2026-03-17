import datetime as dt
import json
from pathlib import Path

from engine import APP_NAME, APP_VERSION, HASH_NAMES, JobConfig, JobResult


def write_report_txt(path: Path, results: list[JobResult], config: JobConfig, sys_info: dict[str, str]) -> None:
    lines = [
        f"{APP_NAME} v{APP_VERSION} - Session Report",
        f"Generated  : {dt.datetime.now().isoformat(timespec='seconds')}",
        f"Host       : {sys_info.get('Hostname', '')}",
        f"OS         : {sys_info.get('OS', '')}",
        f"Timezone   : {sys_info.get('Timezone', '')}",
        f"Source     : {config.source_dir}",
        f"Output     : {config.output_dir}",
        f"Format     : {config.archive_fmt}",
        f"Level      : {config.compress_level_label}",
        f"Algorithms : {', '.join(config.hash_algorithms)}",
        f"Delete Src : {'Yes' if config.delete_source else 'No'}",
        f"Skip Exists: {'Yes' if config.skip_existing else 'No'}",
        f"Scan Mode  : {config.scan_mode}",
        f"Hash Mode  : {config.archive_hash_mode}",
        f"Thread Mode: {config.thread_strategy}",
        f"Resume Used: {'Yes' if config.resume_used else 'No'}",
    ]

    if config.case_metadata:
        lines.append("-" * 30)
        for key, value in config.case_metadata.items():
            if value:
                lines.append(f"{key:<11}: {value}")

    lines += ["", "-" * 80, ""]
    for result in results:
        lines.append(f"Case       : {result.case_name}")
        lines.append(f"Files      : {result.file_count}")
        lines.append(f"Source Size: {result.source_size}")
        lines.append(f"Archive    : {result.archive_path}")
        lines.append(f"Arc Size   : {result.archive_size}")
        lines.append(f"Manifest   : {result.manifest_path}")
        lines.append(f"Verify     : {result.verify}")
        lines.append(f"Start      : {result.start_time}")
        lines.append(f"End        : {result.end_time}")
        lines.append(f"Elapsed    : {result.elapsed_seconds:.3f}s")
        for alg in HASH_NAMES:
            if result.hashes.get(alg):
                lines.append(f"{alg:<10}: {result.hashes[alg]}")
        if result.warnings:
            lines.append(f"Warnings   : {' | '.join(result.warnings)}")
        lines += ["", "-" * 80, ""]

    path.write_text("\n".join(lines), encoding="utf-8")


def write_report_csv(path: Path, results: list[JobResult], config: JobConfig) -> None:
    import csv

    fields = [
        "Case Name",
        "Format",
        "Start Time",
        "End Time",
        "File Count",
        "Source Size",
        "Archive Path",
        "Archive Size",
        "Verify",
        "Manifest Path",
        "Elapsed Seconds",
        "Warnings",
        "Scan Mode",
        "Archive Hash Mode",
        "Thread Strategy",
        "Resume Used",
    ] + HASH_NAMES
    if config.case_metadata:
        for key in config.case_metadata:
            if key not in fields:
                fields.append(key)

    rows = []
    for result in results:
        row = result.to_report_row()
        row.update(
            {
                "Scan Mode": config.scan_mode,
                "Archive Hash Mode": config.archive_hash_mode,
                "Thread Strategy": config.thread_strategy,
                "Resume Used": "Yes" if config.resume_used else "No",
            }
        )
        if config.case_metadata:
            row.update(config.case_metadata)
        rows.append(row)

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_report_json(path: Path, results: list[JobResult], config: JobConfig, sys_info: dict[str, str]) -> None:
    payload = {
        "app": {"name": APP_NAME, "version": APP_VERSION},
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "system": sys_info,
        "session": {
            "source": str(config.source_dir),
            "output": str(config.output_dir),
            "format": config.archive_fmt,
            "compression": config.compress_level_label,
            "hash_algorithms": config.hash_algorithms,
            "delete_source": config.delete_source,
            "skip_existing": config.skip_existing,
            "scan_mode": config.scan_mode,
            "archive_hash_mode": config.archive_hash_mode,
            "thread_strategy": config.thread_strategy,
            "resume_used": config.resume_used,
            "dry_run": config.dry_run,
        },
        "items": [result.to_report_row() for result in results],
    }
    if config.case_metadata:
        payload["session"]["case_metadata"] = config.case_metadata
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
