# ForensicPack v2.2.1

ForensicPack v2.2.1 fixes Windows packaging failures when application metadata and the destination archive are stored on different drives.

## Fixed

- **Supports cross-drive destinations.** Checksum sidecars now use an absolute archive path when Windows cannot calculate a relative path between drives, preventing errors such as `path is on mount 'G:', start on mount 'C:'`.
- **Recognizes prior ForensicPack archives on rerun.** An existing archive is accepted only when its private application-data manifest identifies it as a prior ForensicPack output for the same case and archive format.
- **Preserves collision protection.** Existing unrelated archives remain protected from overwrite.

## Validation

- Added regression coverage for checksum paths spanning different Windows drives.
- Added regression coverage for rebuilding a recognized prior ForensicPack archive.
- The complete Python test suite passes with the expected platform-specific skips and expected failures.

## Upgrade notes

- Recommended for all v2.2.0 users whose destination drive differs from the Windows application-data drive.
- Existing ForensicPack packages remain compatible.
- No evidence-package migration or configuration conversion is required.
