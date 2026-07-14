# ForensicPack v2.2.0

ForensicPack v2.2.0 introduces a cleaner examiner workflow and separates evidence delivery from application metadata.

## Storage changes

- **Keeps delivery folders archive-only.** Selected destination folders now receive only the archive or split archive volumes.
- **Moves generated metadata into private application data.** Reports, retained manifests, JSON manifests, audit logs, checksum files, signatures, certificate copies, and temporary processing data are stored under the current user's ForensicPack application-data folder.
- **Moves the resume database out of evidence destinations.** The default SQLite state database is now stored at `%LOCALAPPDATA%\ForensicPack\forensicpack_state.db` on Windows.
- **Creates a stable workspace per destination.** Metadata is grouped under `%LOCALAPPDATA%\ForensicPack\Cases\<destination-name>-<identifier>` so repeated work against the same destination reuses the correct reports and verification records.
- **Preserves custom database paths.** An explicitly selected resume database path still overrides the application-data default.
- **Preserves legacy cleanup.** Older `_ForensicPack_Metadata` folders remain recognized as generated output and are excluded from same-folder reruns.

## Interface improvements

- **Simplified the main workflow.** The left pane is organized as Evidence & Destination, Package Settings, Integrity, and Case Details.
- **Collapsed advanced controls by default.** Split-volume, concurrency, password, resume, dry-run, scan, report, and state-database controls remain available without crowding the normal intake workflow.
- **Moved destructive deletion into a separate Danger Zone.** Delete-source behavior is no longer presented as a normal intake option.
- **Added a clean-destination status banner.** The interface clearly explains that only archives are written to the selected destination.
- **Added direct metadata access.** Examiners can open the application-data root or the metadata workspace associated with the selected destination.
- **Reduced footer clutter.** Common actions remain visible while diagnostics, verbose logging, administrative relaunch, log management, and settings reset are consolidated under More Actions.

## Validation

- Added tests proving destination roots contain archives only.
- Added tests for deterministic per-destination metadata workspaces and the application-data state database.
- Added GUI smoke coverage for the refreshed layout, collapsed advanced controls, and metadata-folder resolution.
- Retains the Windows and Linux Python test matrix, coverage reporting, dependency audit, Bandit, CodeQL, Windows packaged build, executable smoke launch, SHA-256 release checksum, and CycloneDX SBOM.

## Upgrade notes

- Existing v2.1.x packages remain compatible.
- Existing `_ForensicPack_Metadata` folders are not deleted automatically; they remain excluded from future packaging and may be archived or removed according to agency policy.
- No evidence-package migration is required.
