<p align="center">
  <a href="./actions"><img alt="CI" src="https://img.shields.io/badge/CI-matrix%20%2B%20security-0f172a?style=for-the-badge"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-2563eb?style=for-the-badge">
  <img alt="Platform" src="https://img.shields.io/badge/Platform-Windows-0ea5e9?style=for-the-badge">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-14b8a6?style=for-the-badge">
</p>

<h1 align="center">ForensicPack</h1>

<p align="center">
  <strong>DFIR-focused evidence packaging, hashing, verification, reporting, and transfer.</strong>
</p>

ForensicPack turns folders and files into documented, verifiable evidence packages through a streamlined Windows interface and repeatable CLI workflow.

## Significant changes — v2.3.0

Version 2.3.0 adds the Job History & Verification window, portable verification
records, and more reliable large-archive verification. Download the complete
Windows package, checksum, and SBOM from the
[v2.3.0 release](https://github.com/w617/ForensicPack/releases/tag/v2.3.0).

| Change | Effect on your workflow |
|---|---|
| Exact 7z inventory | Packages only inventoried files, preserving exclusions and ignoring files added after the scan. |
| Verification temporary folder | Choose a scratch drive, check extraction capacity, and cancel extraction or hashing. |
| Searchable job history | Find runs by case, evidence ID, item, location, or run ID; open logs and reports; reverify moved archives. |
| Export Records | Export selected runs' metadata and reports in a checked ZIP, without copying evidence archives. |
| Failure regression coverage | Exercises cancellation, disk-full/disconnected-drive errors, long paths, and actual split archives. |

All five v2.2.2 integrity repairs remain included: guarded source deletion,
repaired 7-Zip execution, validated resume, retained per-run records, and complete
member verification. See [CHANGELOG.md](CHANGELOG.md) for details.

## Core capabilities

| Capability | Description |
|---|---|
| Multiple archive formats | `7z`, ZIP, TAR.GZ, and TAR.BZ2 |
| Archive-only delivery folders | Selected destinations contain the package, not loose derivative metadata |
| Private application data | Reports, manifests, audit logs, checksums, and resume state remain under the current user's ForensicPack data folder |
| Same-folder output | Source and output may be the same populated directory while generated outputs remain excluded |
| Source and member hashing | Source hashes are compared with archived-member hashes |
| Structural verification | `7z t`, ZIP CRC validation, or full TAR member readback |
| External metadata | Text/JSON manifests, SHA-256 sidecars, optional signatures, and audit logs |
| Reporting | TXT, CSV, optional JSON, and optional PDF |
| Job history | Search retained runs, open logs/reports, and record independent reverification outcomes |
| Portable records | Export and check metadata/report bundles without including archive evidence |
| Resume support | SQLite-backed recovery with manifest, archive-volume, and source revalidation |
| Verified transfer | Copies a package and confirms destination hashes |
| Release assurance | Matrix tests, Bandit, dependency audit, CodeQL, Windows build smoke tests, checksums, and CycloneDX SBOM |

## Clean destination behavior

A normal package destination now contains only the deliverable:

```text
Case Files.zip
```

Generated application metadata is stored separately.

### Windows

The application-data root is `%LOCALAPPDATA%\ForensicPack`. The resume database
is `forensicpack_state.db` under that root. The separate searchable history index
is `history.db`. Each destination has a metadata
workspace at `Cases\<destination-name>-<identifier>`.

| Location within the workspace | Contents |
|---|---|
| `Runs\<run-id>\<item-name>.audit.jsonl` | Original event log for that packaging attempt, including failed attempts |
| `Runs\<run-id>\<item-name>.manifest.txt` | Retained source inventory and hashes, when enabled |
| `Runs\<run-id>\<item-name>.manifest.json` | Package metadata, archive-volume hashes, and verification information |
| `Runs\<run-id>\<item-name>.sha256` | Checksums referencing that run's package and metadata files |
| `History\<identifier>` | Copies of legacy metadata preserved before replacement |
| `<item-name>.manifest.json`, `.manifest.txt`, `.audit.jsonl`, `.sha256` | Compatibility files for the latest completed package, when generated |
| `ForensicPack_Report_<timestamp>.*` | Session reports with paths to the original run records |

Use **Open Metadata** to reach the destination's workspace. For a particular
archive-creation attempt, follow the audit-log path in its report or inspect its
`Runs` folder. The root audit copy represents the latest completed package;
it may not show a later failed attempt.

The workspace identifier is derived from the resolved destination path. Repeated
work against the same destination uses the same workspace, with separate run
folders. Historical records preserve prior documentation; they do not retain
archive bytes overwritten by a later rebuild. A historical checksum may therefore
no longer match a rebuilt archive at the same path.

`FORENSICPACK_APPDATA` may be set for managed deployments or isolated testing.

## Refreshed examiner interface

The default UI is organized around the normal workflow:

1. **Evidence & Destination**
2. **Package Settings**
3. **Integrity**
4. **Case Details**

Technical controls are available under **Show Advanced Settings** rather than occupying the normal intake screen. Delete-source behavior is isolated in a separate **Danger Zone**.

The footer keeps common actions visible:

- Open Destination
- Open Metadata
- Open Last Report
- Job History
- More Actions

## Same-folder packaging

A source folder can also be selected as the output folder. ForensicPack snapshots eligible source items and excludes recognized generated outputs, including:

- prior archives corresponding to sibling source items
- split archive volumes
- manifests, audit logs, checksum sidecars, reports, and state files
- legacy `_ForensicPack_Metadata` folders
- temporary and partial files

Standalone archive evidence remains eligible when it does not correspond to a sibling source item. Unrelated submitted PEM, SIG, and JSON evidence remains eligible.

## Verification levels

A normal verified package completes three checks:

1. Source files are inventoried and hashed.
2. The archive passes its format-specific structural test.
3. Archived-member SHA-256 values are compared with the source manifest.

The external checksum sidecar can also be used for later package verification from the original destination and its associated application-data workspace.

A structural test establishes that an archive can be read. A complete content
verification also requires archived-member SHA-256 values to match every
inventoried source file. Review **Content Verify**, warnings, and scan issues
alongside the overall result, particularly when optional checks are disabled.

### Large 7z archives and temporary storage

7z member verification extracts into a temporary directory. Select **Verification
temporary folder** under Advanced Settings, or pass `--verify-temp-dir` when
packaging or reverifying. Leave it blank for the system temporary folder. The
folder must already exist and be outside the source and destination folders.
ZIP and TAR member checks stream their contents without this extraction step.

Before packaging, ForensicPack checks capacity for the largest queued 7z job.
Each extraction checks again and reserves space against other active jobs in the
same application process: inventoried bytes plus the larger of 10% or 64 MiB.
Other applications and separate ForensicPack processes can still consume disk
space. Temporary extraction files are removed on completion, failure, or normal
cancellation. Abrupt power loss or forced process termination can leave temporary
files. Closing the GUI during active work requests cancellation and waits for cleanup.

7z creation uses an explicit UTF-8 file list with recursion and wildcard matching
disabled. Filenames containing newline, carriage return, or NUL cannot be
represented safely and fail explicitly. Source files must remain stable while
packaging; the file list is not a filesystem snapshot.

### Job History & Verification

Open **Job History** to search or filter packaging attempts. Run IDs link archive
locations, original outcomes, warnings, logs, and reports. **Import Prior Runs**
indexes retained v2.2.2 `Runs` folders without modifying them; imports are marked
as not reverified. Interrupted processes can leave a `running/incomplete` record.

Select a run and choose **Reverify**, then select the archive's current folder.
ForensicPack checks the retained manifest and audit, all volume hashes, archive
structure, and member SHA-256 values. Original source files are not required or
re-examined. Each check receives a separate timestamped report; it never rewrites
the original packaging outcome or audit. Encrypted 7z archives require their
password again. Missing or overwritten archive bytes cannot be restored from history.

Select one or more runs and choose **Export Records** to create a ZIP containing
retained manifests, audit logs, checksums, available signature/certificate files,
session reports, and reverification reports. Evidence archives are excluded.
Session reports can contain other items from the same session; review them before
sharing. The bundle includes a SHA-256 inventory and is checked before publication.
Existing export files are never overwritten. A recipient can verify the bundle
without the sender's history database and optionally compare supplied archive
volumes. These hashes establish consistency, not independent authenticity;
exporting signature files does not itself validate their signatures.

### Source deletion

Delete Source remains an optional advanced operation. Before it is allowed,
ForensicPack requires a complete scan, successful member-hash verification for
every inventoried file, and no unresolved packaging warnings. It then inventories
and hashes the source again to detect changes. If a requirement is unmet, the
source is retained and the result identifies the warning. Cancellation or a
verification failure prevents cleanup.

The source check is a point-in-time comparison, not a filesystem snapshot or lock.
Use stable evidence sources throughout packaging and verification.

### Resume and upgrades

Resume considers only jobs that previously completed without warnings. Before
skipping one, it checks the saved manifest's SHA-256 against the database, the
expected archive path, the complete split-volume set, each archive-volume hash,
and the current source inventory and SHA-256 values.

A missing or changed archive, source, or saved manifest causes rebuilding instead
of an unchecked skip. Unrelated output collisions remain protected. When Resume
and Skip Existing are both selected, Skip Existing cannot bypass these checks.

Existing databases receive the new manifest-validation fields automatically.
Older jobs without a saved manifest digest must be rebuilt before they can be
safely resumed. Revalidation reads source and archive bytes, so large cases take
longer than the former database-only skip.

## Quick start

### Launch the GUI

```powershell
cd src
python forensicpack.py gui
```

### Package evidence

```powershell
cd src
python forensicpack.py pack `
  --source .\Input `
  --output .\Output `
  --format zip `
  --hash SHA256 `
  --report-json `
  --examiner "Examiner Name" `
  --case-id "2026-001" `
  --evidence-id "Item-1"
```

### Verify existing packages

```powershell
python forensicpack.py verify `
  --input .\Output `
  --hash SHA256 `
  --report-json
```

### Copy and verify a package

```powershell
python forensicpack.py transfer-verify `
  --source .\Output `
  --destination E:\EvidenceDelivery `
  --hash SHA256 `
  --report .\transfer-report.json
```

### History, reverification, and portable records

```powershell
python forensicpack.py history --query "2026-001"
python forensicpack.py history --import-existing
python forensicpack.py reverify --run RUN_ID --archive-dir E:\EvidenceDelivery --verify-temp-dir D:\Scratch
# Add --ask-password for an encrypted 7z archive; the prompt does not echo it.
python forensicpack.py export-records --run RUN_ID --output .\verification-records.zip
# Repeat --run to include multiple runs.
python forensicpack.py verify-records --input .\verification-records.zip --archive-dir E:\EvidenceDelivery
```

`verify-records` checks bundle files and optional archive-volume hashes. Use
`reverify` with an indexed run for archive structural and member-content checks.

## Advanced controls

| Control | Purpose |
|---|---|
| Split archive | Segment 7-Zip output into fixed-size volumes |
| Resume | Revalidate saved manifests, all archive volumes, and current source hashes before skipping completed work |
| Dry run | Inventory and plan without producing an archive |
| Fast scan | Optimize discovery for large file counts |
| Skip archive hash | Retain file-level integrity work while skipping the final container hash |
| JSON report | Produce a machine-readable session report |
| Embed manifest | Add the text manifest inside the archive |
| Verification temporary folder | Choose a drive with capacity for extracted 7z members |
| Resume DB override | Use a custom SQLite path instead of the application-data default |

## Requirements

- Python 3.10+
- Windows for the packaged EXE workflow
- 7-Zip for 7z creation and verification
- dependencies in `src/requirements-dev.txt` for development and release builds

## Testing

```powershell
cd src
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The integrity regression suite covers source retention, changed and missing
resume inputs, split volumes, audit-history preservation, invalid signatures,
and nonrecursive 7-Zip execution. Native round-trip tests require an installed
7-Zip executable and cover plain, encrypted, and split archives; they skip if
7-Zip is unavailable. Additional regression tests cover exact file selection,
quiet subprocess cancellation, extraction cleanup, low scratch capacity,
concurrent scratch reservations, simulated ENOSPC/ENODEV/EIO write failures,
long paths, history searches, moved archives, and changed verification records.

Enable the 128 MiB native split-archive round trip with:

```powershell
$env:FORENSICPACK_LARGE_TESTS = "1"
python -m pytest -q test_verification_center.py -k large_native
```

This opt-in test needs 7-Zip and sufficient source, output, and temporary space.
Simulated I/O failures exercise application error handling; they do not replace
physical-drive or power-loss validation.

CI also runs Ruff, mypy analysis, Bandit, pip-audit, coverage, CodeQL, a Windows PyInstaller build, and an executable smoke launch.

## Build the Windows EXE

```powershell
.\src\scripts\build_windows.ps1
```

Expected build output:

```text
src\dist\ForensicPack\ForensicPack.exe
```

See `docs/CODE_SIGNING.md` for the external code-signing hook.

## Archive limitations

ForensicPack creates evidence archives, not physical, filesystem, or bit-for-bit forensic images. ZIP and TAR formats do not preserve every Windows or NTFS property. Available metadata is documented in the manifest, and limitations are included in reports.

## Operational notes

- Password protection is supported only for `7z` output.
- Split archives are supported only for `7z` output.
- The packaged EXE does not bundle `7z.exe`.
- GUI passwords are never persisted.
- A custom resume database path may be selected under Advanced Settings.
- Resume rechecks bytes and may take time on large cases; legacy jobs without a saved manifest digest rebuild.
- Source deletion requires complete verification and a fresh source check. Scan omissions, disabled verification, or unresolved warnings retain the source.
- Legacy `_ForensicPack_Metadata` folders are not deleted automatically; they remain excluded from later source scans.

## Intended use

ForensicPack is intended for lawful DFIR, digital-evidence handling, packaging, transfer, and integrity-verification workflows.

## License

Released under the MIT License. See `LICENSE.txt`.
