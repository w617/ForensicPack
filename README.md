<p align="center">
  <a href="./actions"><img alt="CI" src="https://img.shields.io/badge/CI-ready-0f172a?style=for-the-badge"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-2563eb?style=for-the-badge">
  <img alt="Platform" src="https://img.shields.io/badge/Platform-Windows-0ea5e9?style=for-the-badge">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-14b8a6?style=for-the-badge">
  <img alt="Tests" src="https://img.shields.io/badge/Tests-42%20passed-22c55e?style=for-the-badge">
</p>

<h1 align="center">ForensicPack</h1>

<p align="center">
  <strong>DFIR-focused bulk archiving, hashing, validation, and reporting for repeatable digital evidence workflows.</strong>
</p>

<p align="center">
  ForensicPack helps forensic labs package evidence collections into verifiable archive sets with manifest generation, integrity checks, and report-ready outputs through both GUI and CLI workflows.
</p>

---

ForensicPack is designed to feel like a real lab utility, not just a script bundle. The interface keeps the intake path, archive settings, hash selections, queue state, and live logging visible in one place so analysts can package and validate evidence with less context switching.

## Why ForensicPack

Digital evidence packaging is often pieced together with manual prep, separate hash tools, archive utilities, and inconsistent reporting. ForensicPack turns that into a cleaner, repeatable workflow:

- package evidence sets into investigator-friendly formats
- generate manifests during the same job
- verify output before treating the package as complete
- emit TXT, CSV, and optional JSON reports for handoff and documentation
- resume interrupted sessions from a SQLite-backed state store
- run the same underlying process through GUI or scripted CLI execution

## Feature highlights

| Capability | What it gives you |
|---|---|
| GUI + CLI parity | One shared engine for analyst use, repeatable SOPs, and automation |
| Multiple archive formats | `7z`, `ZIP`, `TAR.GZ`, and `TAR.BZ2` support |
| Manifest generation | Inventory and hash outputs captured during packaging |
| Archive verification | Post-build integrity validation before closeout |
| Resume support | SQLite-backed recovery for interrupted jobs |
| Dry-run mode | Inventory and planning without creating archive output |
| JSON reporting | Structured output for downstream review or automation |
| Windows EXE path | PyInstaller build support for lab deployment |

## Built for practical lab workflows

### Evidence packaging
Package folders, collections, or selected child items into consistent archive outputs without rebuilding the process from scratch every time.

### Integrity validation
Validate finished packages before handoff and document what was created, how it was hashed, and whether verification succeeded.

### Report-ready output
Produce outputs that are easier to reference in notes, transfer documentation, or internal lab reporting.

### Repeatability
Use the GUI for day-to-day analyst operations and the CLI when you want the same workflow embedded into a scripted process.

## Supported formats

| Format | Password support | Split archives | Verification method |
|---|---:|---:|---|
| `7z` | Yes | Yes | `7z t` |
| `ZIP` | No | No | `zipfile.testzip()` |
| `TAR.GZ` | No | No | Full member readback |
| `TAR.BZ2` | No | No | Full member readback |

## Quick start

### Launch the GUI

```powershell
cd src
python forensicpack.py
```

or:

```powershell
cd src
python forensicpack.py gui
```

### Package a collection from the CLI

```powershell
cd src
python forensicpack.py pack --source .\TestCases --output .\TestOutput --format zip --hash SHA256
```

### Verify output

```powershell
cd src
python forensicpack.py verify --input .\TestOutput --hash SHA256 --report-json
```

## Example packaging workflow

```powershell
python forensicpack.py pack ^
  --source .\Input ^
  --output .\Output ^
  --format 7z ^
  --compression "Normal (5)" ^
  --hash SHA256 ^
  --hash SHA512 ^
  --split ^
  --split-size 4 ^
  --resume ^
  --report-json ^
  --examiner "Examiner Name" ^
  --case-id "2026-001" ^
  --evidence-id "Item-1"
```

## What a normal run can produce

- finished archive output for each source item
- embedded manifest text file inside each archive
- `ForensicPack_Report_<timestamp>.txt`
- `ForensicPack_Report_<timestamp>.csv`
- `ForensicPack_Report_<timestamp>.json` when `--report-json` is enabled
- `forensicpack_state.db` when resume/state tracking is used

## Requirements

### Runtime

- Python 3.10+
- 7-Zip installed when using `7z` output or `7z` verification
- Windows + PowerShell for the packaged EXE workflow

Expected 7-Zip paths:

- `C:\Program Files\7-Zip\7z.exe`
- `C:\Program Files (x86)\7-Zip\7z.exe`

### Development

```powershell
cd src
python -m pip install -r requirements-dev.txt
```

## Repository layout

```text
.
├── .github/workflows/        # CI and release automation
├── checksums/                # SHA256 checksum artifacts
├── docs/                     # Distribution and operational notes
├── release/windows/          # Built Windows EXE package
└── src/                      # Python source, tests, scripts, assets
    ├── forensicpack.py
    ├── cli.py
    ├── core.py
    ├── gui.py
    ├── scripts/build_windows.ps1
    └── TestCases/
```

## Documentation

- [Distribution Notes](docs/README.txt)

## Testing

Run the suite with:

```powershell
cd src
pytest -q
```

This package revision currently passes:

```text
Run `cd src && pytest -q` to validate in your environment.
```

## Build the Windows EXE

```powershell
.\src\scripts\build_windows.ps1
```

Expected build output:

```text
src\dist\ForensicPack\ForensicPack.exe
```

## Roadmap

- improved handling for locked or inaccessible files
- richer report summaries and lab-facing output templates
- stronger GUI polish and workflow affordances
- drag-and-drop intake improvements
- more operational metadata presets for evidence packaging

## Operational notes

- Password protection is only supported for `7z` output.
- Split archives are only supported for `7z` output.
- The packaged EXE does **not** bundle `7z.exe`; the host still needs a local 7-Zip installation for `7z` operations.
- `gui_settings.json` stores UI preferences, but passwords are not persisted.

## Intended use

ForensicPack is intended for lawful DFIR, digital evidence handling, packaging, transfer, and verification workflows.

## License

Released under the MIT License. See [LICENSE.txt](LICENSE.txt).
