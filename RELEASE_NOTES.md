# ForensicPack v2.1.1

ForensicPack v2.1.1 is a maintenance and evidence-safety release for the v2.1 package workflow. It supersedes v2.1.0 for new installations and upgrades.

## Fixed

- **Protected planned sidecars from overwrite.** Preflight collision checks now cover the archive, retained text manifest, canonical JSON manifest, SHA-256 checksum file, audit log, optional signature, and optional certificate copy before processing begins.
- **Excluded every split 7-Zip volume on same-folder reruns.** When a prior `.7z.001` output is recognized, `.002` and all later sibling volumes are also excluded from the next source snapshot.
- **Kept no-hash reports consistent.** Explicit no-hash sessions are normalized before TXT, CSV, JSON, and PDF reports are written, so the saved report matches the returned result and CLI status.
- **Honored portable or custom 7-Zip paths during verification.** The configured 7-Zip path now remains active for archive creation, structural verification, and skip-existing verification.

## Validation and assurance

- Re-enabled the no-hash and split-archive regression tests that had been temporarily marked as expected failures.
- Added regression tests proving that unrelated sidecars are not overwritten, all split volumes are excluded, and the configured 7-Zip path is active during verification.
- Restored the complete Bandit medium/high security scan without the previous B108 exception.
- Retains the Windows and Linux Python test matrix, coverage reporting, dependency audit, CodeQL analysis, packaged Windows build, executable smoke test, SHA-256 release checksum, and CycloneDX SBOM.

## Upgrade notes

- This release is recommended for all v2.1.0 users, especially when source and output are the same folder, split 7-Zip archives are used, or a portable 7-Zip executable is configured.
- Existing v2.1.0 packages remain compatible. No data migration or configuration conversion is required.
- The release includes a Windows ZIP package, SHA-256 checksum sidecar, and CycloneDX SBOM.
