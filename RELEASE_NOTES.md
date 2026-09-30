# ForensicPack v2.2.2

ForensicPack v2.2.2 strengthens evidence packaging, repairs 7-Zip execution, and preserves verification records across reruns. Recommended for users of v2.2.1 and earlier, especially when using Resume or Delete Source.

## Five integrity fixes

- **Safer source deletion.** Requires complete archived-member SHA-256 verification, a complete inventory, no unresolved packaging warnings, and a fresh source comparison. Incomplete scans, disabled checks, and changed source files retain the source with an explicit warning.
- **Reliable 7-Zip execution.** Removes recursive runner calls, passes passwords to structural verification, and embeds manifests during initial creation of encrypted and split archives.
- **Validated resume.** Checks the saved manifest digest, expected archive destination, every archive volume and hash, and the current source inventory and hashes before skipping a completed job. Missing, changed, corrupt, or legacy records rebuild. Unrelated-output collision protection remains enabled.
- **Retained audit history.** Each packaging attempt receives a unique `Runs/<run-id>` workspace. Reports point to original run records, legacy metadata is preserved in `History/<identifier>`, and existing audit logs cannot be truncated by reopening them.
- **Fail-closed member verification.** Invalid 7z signatures, incomplete source-hash inventories, and zero or partial member checks cannot produce a complete verification pass.

## Documentation and release maintenance

- Expanded README with significant changes, audit-log locations, verification semantics, source-deletion conditions, and resume upgrade notes.
- Added CHANGELOG.md with the fixes, operational implications, and validation details.
- Updated cryptography to 50.0.1 to resolve dependency-audit findings.
- Bundled and checked the application version file in Windows builds. Build scripts stop on failed dependency installation, tests, or packaging.

## Validation

- Local Python 3.12: 102 passed, 17 GUI/display skips, and 6 existing expected failures; coverage 67.19%.
- Native 7-Zip round trips cover plain, encrypted, and split archives, member hashes, and package checksums.
- Windows/Linux Python 3.10–3.13 tests, security checks, Windows packaging, and executable launch checks run through GitHub Actions.

## Upgrade notes

- Extract the entire Windows ZIP to a new application folder and run `ForensicPack.exe`. Keep its `_internal` folder with the EXE.
- Existing evidence archives remain compatible. Delivery folders still contain only archive files.
- SQLite migration is automatic. Old jobs without a saved manifest digest require rebuilding before safe resume.
- Resume now reads source and archive bytes for validation, which takes longer for large cases.
- Historical records retain prior documentation, not overwritten archive bytes. Their checksums may differ from a later archive rebuilt at the same path.
- Source revalidation is a point-in-time check, not a filesystem snapshot or lock; use stable evidence sources.
- 7-Zip remains a separate installation requirement for 7z operations.

## Downloads

- `ForensicPack-v2.2.2-windows.zip` — Windows application and required runtime files.
- `ForensicPack-v2.2.2-windows.zip.sha256` — package checksum.
- `ForensicPack-v2.2.2-sbom.cdx.json` — CycloneDX software bill of materials.
