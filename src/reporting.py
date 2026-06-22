import datetime as dt
import json
from pathlib import Path

from engine import APP_NAME, APP_VERSION, HASH_NAMES, JobConfig, JobResult
from models import summarize_job_results


def _session_lines(config: JobConfig, sys_info: dict[str, str]) -> list[str]:
    lines = [
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
        f"Scan Errors: {config.scan_error_mode}",
        f"Member Hash: {'Enabled' if config.verify_member_hashes else 'Disabled'}",
        f"Hash Mode  : {config.archive_hash_mode}",
        f"Thread Mode: {config.thread_strategy}",
        f"Resume Used: {'Yes' if config.resume_used else 'No'}",
    ]
    if config.excluded_generated_items:
        lines.append("Excluded   : " + ", ".join(config.excluded_generated_items))
    for warning in config.preflight_warnings:
        lines.append(f"Preflight  : {warning}")
    return lines


def write_report_txt(path: Path, results: list[JobResult], config: JobConfig, sys_info: dict[str, str]) -> None:
    summary = summarize_job_results(results)
    lines = [f"{APP_NAME} v{APP_VERSION} - Session Report", *_session_lines(config, sys_info)]
    lines.append(
        f"Summary    : {summary['success']} success, {summary['warning']} warning, "
        f"{summary['failed']} failed, {summary['skipped']} skipped, {summary['cancelled']} cancelled"
    )
    if config.case_metadata:
        lines.append("-" * 30)
        for key, value in config.case_metadata.items():
            if value:
                lines.append(f"{key:<11}: {value}")

    lines += ["", "-" * 100, ""]
    for result in results:
        lines.extend(
            [
                f"Case       : {result.case_name}",
                f"Status     : {result.status}",
                f"Files      : {result.file_count}",
                f"Source Size: {result.source_size}",
                f"Archive    : {result.archive_path}",
                f"Arc Size   : {result.archive_size}",
                f"Manifest   : {result.manifest_path}",
                f"Manifest JS: {result.external_manifest_json}",
                f"Checksums  : {result.checksum_path}",
                f"Signature  : {result.signature_path or 'Not signed'}",
                f"Audit Log  : {result.audit_log_path}",
                f"Verify     : {result.verify}",
                f"Content Ver: {result.content_verify}",
                f"Members    : {result.archive_member_count}",
                f"Start      : {result.start_time}",
                f"End        : {result.end_time}",
                f"Elapsed    : {result.elapsed_seconds:.3f}s",
            ]
        )
        for algorithm in HASH_NAMES:
            if result.hashes.get(algorithm):
                lines.append(f"{algorithm:<10}: {result.hashes[algorithm]}")
        if result.scan_issues:
            lines.append(f"Scan Issues: {len(result.scan_issues)}")
            for issue in result.scan_issues:
                lines.append(
                    f"  - {issue.operation}: {issue.path}: {issue.error_type}: {issue.message}"
                )
        if result.warnings:
            lines.append(f"Warnings   : {' | '.join(result.warnings)}")
        lines += ["", "-" * 100, ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_report_csv(path: Path, results: list[JobResult], config: JobConfig) -> None:
    import csv

    fields = [
        "Case Name",
        "Format",
        "Status",
        "Start Time",
        "End Time",
        "File Count",
        "Source Size",
        "Archive Path",
        "Archive Size",
        "Verify",
        "Content Verify",
        "Archive Member Count",
        "Manifest Path",
        "Manifest JSON",
        "Checksum Path",
        "Signature Path",
        "Audit Log Path",
        "Scan Issue Count",
        "Scan Issues",
        "Elapsed Seconds",
        "Warnings",
        "Scan Mode",
        "Scan Error Mode",
        "Archive Hash Mode",
        "Thread Strategy",
        "Resume Used",
    ] + HASH_NAMES
    if config.case_metadata:
        fields.extend(key for key in config.case_metadata if key not in fields)

    rows = []
    for result in results:
        row = result.to_report_row()
        row.update(
            {
                "Scan Mode": config.scan_mode,
                "Scan Error Mode": config.scan_error_mode,
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
    payload: dict[str, object] = {
        "schema": "org.forensicpack.session-report/v2",
        "app": {"name": APP_NAME, "version": APP_VERSION},
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
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
            "scan_error_mode": config.scan_error_mode,
            "verify_member_hashes": config.verify_member_hashes,
            "archive_hash_mode": config.archive_hash_mode,
            "thread_strategy": config.thread_strategy,
            "resume_used": config.resume_used,
            "dry_run": config.dry_run,
            "excluded_generated_items": config.excluded_generated_items,
            "preflight_warnings": config.preflight_warnings,
            "case_metadata": config.case_metadata or {},
        },
        "summary": summarize_job_results(results),
        "items": [result.to_report_row() for result in results],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_report_pdf(path: Path, results: list[JobResult], config: JobConfig, sys_info: dict[str, str]) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise RuntimeError("PDF reporting requires the 'reportlab' package.") from exc

    styles = getSampleStyleSheet()
    document = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        title=f"{APP_NAME} Session Report",
        author=APP_NAME,
    )
    story = []
    if config.agency_logo_path and config.agency_logo_path.is_file():
        story.append(Image(str(config.agency_logo_path), width=1.2 * inch, height=1.2 * inch, kind="proportional"))
        story.append(Spacer(1, 8))
    story.append(Paragraph(f"{APP_NAME} v{APP_VERSION} - Session Report", styles["Title"]))
    story.append(Spacer(1, 8))
    session_data = [["Field", "Value"]]
    for line in _session_lines(config, sys_info):
        key, _, value = line.partition(":")
        session_data.append([Paragraph(key.strip(), styles["BodyText"]), Paragraph(value.strip(), styles["BodyText"])])
    session_table = Table(session_data, colWidths=[1.35 * inch, 5.55 * inch], repeatRows=1)
    session_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(session_table)
    story.append(Spacer(1, 12))

    summary = summarize_job_results(results)
    story.append(
        Paragraph(
            f"Summary: {summary['success']} success, {summary['warning']} warning, "
            f"{summary['failed']} failed, {summary['skipped']} skipped, "
            f"{summary['cancelled']} cancelled",
            styles["Heading2"],
        )
    )
    for index, result in enumerate(results):
        if index:
            story.append(PageBreak())
        story.append(Paragraph(result.case_name, styles["Heading2"]))
        rows = [
            ["Status", result.status],
            ["Verification", result.verify],
            ["Content Verification", result.content_verify],
            ["Files", str(result.file_count)],
            ["Source Size", str(result.source_size)],
            ["Archive", result.archive_path],
            ["Archive Size", str(result.archive_size)],
            ["Manifest JSON", result.external_manifest_json],
            ["Checksum", result.checksum_path],
            ["Audit Log", result.audit_log_path],
            ["Scan Issues", str(len(result.scan_issues))],
            ["Warnings", " | ".join(result.warnings) or "None"],
        ]
        table = Table(
            [[Paragraph(str(left), styles["BodyText"]), Paragraph(str(right), styles["BodyText"])] for left, right in rows],
            colWidths=[1.5 * inch, 5.4 * inch],
        )
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(table)
        if result.scan_issues:
            story.append(Spacer(1, 10))
            story.append(Paragraph("Scan Issues / Omitted Content", styles["Heading3"]))
            for issue in result.scan_issues:
                story.append(
                    Paragraph(
                        f"{issue.operation}: {issue.path} - {issue.error_type}: {issue.message}",
                        styles["BodyText"],
                    )
                )
    document.build(story)
