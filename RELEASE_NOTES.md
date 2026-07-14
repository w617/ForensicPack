# ForensicPack v2.1.3

ForensicPack v2.1.3 is a destination-folder cleanup release. It supersedes v2.1.2 for users who want a clean delivery folder.

## Fixed

- **Moves generated metadata out of the destination root.** Per-item audit logs, retained text manifests, canonical JSON manifests, SHA-256 checksum files, signatures, certificate copies, the state database, and session reports are now written to `_ForensicPack_Metadata`.
- **Keeps the actual package visible.** The archive or split archive volumes remain directly in the selected destination folder for easy delivery.
- **Keeps same-folder reruns clean.** `_ForensicPack_Metadata` is treated as generated output and excluded from later source scans by default.
- **Preserves package verification.** Checksum sidecars stored in `_ForensicPack_Metadata` use relative paths to the archive in the destination root, and verification checks the metadata folder automatically.

## Validation

- Added regression coverage proving the destination root contains only the package archive and `_ForensicPack_Metadata` after a normal run.
- Added coverage proving loose `*.audit.jsonl`, `*.manifest.json`, `*.manifest.txt`, `*.sha256`, and `ForensicPack_Report_*` files no longer appear in the destination root.
- Added coverage verifying the checksum sidecar still validates the package from inside `_ForensicPack_Metadata`.
- Added coverage confirming `_ForensicPack_Metadata` is excluded on same-folder reruns.

## Upgrade notes

- Recommended for all users who want a clean delivery folder with no loose ForensicPack derivative files.
- Existing v2.1.0, v2.1.1, and v2.1.2 packages remain compatible.
- No data migration or configuration conversion is required.
