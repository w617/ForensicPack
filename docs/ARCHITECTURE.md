# Architecture

## Top-Level Layout

- `.github/workflows/`: CI, smoke, and release automation
- `src/`: Python application source, tests, scripts, and assets
- `release/windows/`: packaged EXE output (vendored/runtime files)
- `checksums/`: SHA256 checksum artifacts for distributables
- `docs/`: operational and repository documentation

## Runtime Modules

- `src/forensicpack.py`: main entrypoint (GUI + CLI dispatch)
- `src/cli.py`: command-line parsing and command execution
- `src/gui.py` + `src/gui_components/`: desktop UI
- `src/core.py`: session orchestration and workflow engine
- `src/archivers.py`: archive create/verify adapters
- `src/hashing.py`: inventory and hashing pipeline
- `src/state_db.py`: SQLite resume/state persistence
- `src/reporting.py`: TXT/CSV/JSON output writers
- `src/models.py`: shared dataclasses/config/runtime models
- `src/utils.py`: shared utility helpers

## Execution Flow

1. User starts GUI or CLI entrypoint.
2. Input/config is normalized into `JobConfig`.
3. `core.run_session` scans source items and manages per-item workers.
4. For each item:
   - inventory is built
   - manifest is generated
   - archive is created
   - archive verification runs
   - optional archive hash and source deletion run
5. Result rows are written to reports and persisted in SQLite state.

## Testing Strategy

- Unit/integration tests: `src/test_*.py`
- Matrix CI: Ubuntu + Windows (Python 3.10–3.12)
- Smoke CLI workflow: quick `pack --dry-run` and `verify` checks

## Release Strategy

- Build automation is in `.github/workflows/release-windows.yml`.
- Tagged releases (`v*`) build and publish ZIP artifacts.
- Runtime bundle in `release/windows/` is marked vendored for language stats.
