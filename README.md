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
| Resume support | SQLite-backed interrupted-session recovery |
| Verified transfer | Copies a package and confirms destination hashes |
| Release assurance | Matrix tests, Bandit, dependency audit, CodeQL, Windows build smoke tests, checksums, and CycloneDX SBOM |

## Clean destination behavior

A normal package destination now contains only the deliverable:

```text
Case Files.zip
```

Generated application metadata is stored separately.

### Windows

```text
%LOCALAPPDATA%\ForensicPack\
├── forensicpack_state.db
└── Cases\
    └── <destination-name>-<identifier>\
        ├── Case Files.audit.jsonl
        ├── Case Files.manifest.json
        ├── Case Files.manifest.txt
        ├── Case Files.sha256
        └── ForensicPack_Report_<timestamp>.*
```

The workspace identifier is derived from the resolved destination path. Repeated work against the same destination reuses the correct metadata workspace without exposing the full path in the folder name.

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
| Resume | Reuse completed state from the application-data SQLite database |
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
- Legacy `_ForensicPack_Metadata` folders are not deleted automatically; they remain excluded from later source scans.

## Intended use

ForensicPack is intended for lawful DFIR, digital-evidence handling, packaging, transfer, and integrity-verification workflows.

## License

Released under the MIT License. See `LICENSE.txt`.
