# ForensicPack v2.1.2

ForensicPack v2.1.2 is a focused evidence-selection maintenance release.

## Fixed

- **Always excludes generated derivative sidecars by default.** Files matching `*.manifest.json`, `*.manifest.txt`, `*.audit.json`, `*.audit.jsonl`, and `*.sha256` are no longer restored as standalone evidence during source classification.
- **Covers exact derivative filenames.** Generic files named `manifest.json`, `manifest.txt`, `audit.json`, `audit.jsonl`, or `.sha256` are also excluded.
- **Keeps generated signature and certificate sidecars out of new packages.** `*.manifest.json.sig` and `*.certificate.pem` remain excluded while unrelated submitted `.sig` and `.pem` evidence files remain eligible.
- **Preserves the analyst override.** The existing include-generated-outputs option still allows these files to be intentionally packaged when required.

## Validation

- Added regression coverage for derivative exclusions with and without matching sibling evidence.
- Added coverage confirming unrelated JSON, PEM, and SIG evidence remains eligible.
- Added coverage confirming the explicit include-generated-outputs override still works.
- Retains the Windows and Linux Python test matrix, coverage reporting, dependency audit, Bandit, CodeQL, packaged Windows build, executable smoke test, SHA-256 release checksum, and CycloneDX SBOM.

## Upgrade notes

- Recommended for users who run ForensicPack repeatedly in the same evidence or output directory.
- Existing v2.1.0 and v2.1.1 packages remain compatible.
- No data migration or configuration conversion is required.
