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

## Significant changes — v2.2.2

Version 2.2.2 adds five evidence-integrity fixes. Upgrade the Windows application
to receive them; the previous v2.2.1 executable does not include these changes.
Download the Windows package, checksum, and SBOM from the
[v2.2.2 release](https://github.com/w617/ForensicPack/releases/tag/v2.2.2).

| Change | Effect on your workflow |
|---|---|
| Source-deletion safeguards | Incomplete scans, disabled verification, unresolved packaging warnings, or changed source files retain the source and produce a warning. |
| 7-Zip execution repair | Removes the recursive runner failure, supplies passwords to structural verification, and embeds manifests during initial creation of encrypted or split archives. |
| Validated resume | Rechecks the saved manifest, every archive volume, and current source files before skipping completed work. |
| Retained run history | Each packaging attempt receives its own audit and metadata folder; reruns preserve earlier records. |
| Complete member verification | Invalid 7z signatures and zero or partial member checks cannot be reported as a full verification pass. |

See [CHANGELOG.md](CHANGELOG.md) for implementation details, validation results,
and earlier release changes.

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
is `forensicpack_state.db` under that root. Each destination has a metadata
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
7-Zip is unavailable.

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
