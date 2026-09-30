# ForensicPack v2.3.0

This release adds searchable job history, independent archive reverification,
portable verification records, and safer handling of large 7z verification jobs.
All v2.2.2 integrity fixes remain included.

## Changes

- **Exact 7z file selection:** archives contain the inventoried files; excluded and
  post-scan files are not added by a recursive filesystem traversal.
- **Verification temporary folder:** select a scratch drive in Advanced Settings or
  with `--verify-temp-dir`. Capacity checks and in-process reservations account for
  extracted bytes plus max(10%, 64 MiB). Extraction and hashing are cancellable.
- **Job History & Verification:** search/filter runs, view case/evidence IDs and
  original outcomes, open logs/reports, and import retained v2.2.2 run folders.
- **Reverify:** check retained metadata, volume hashes, structure, and member hashes
  at an archive's current location. Each check has a separate report and preserves
  the original result. Original sources are not re-examined or required.
- **Export Records:** checked ZIP bundles include selected metadata and reports,
  with a SHA-256 inventory, but no evidence archive bytes. Recipients can check
  bundles and optional archive-volume hashes without the sender's history database.
- **CLI:** added `history`, `reverify`, `export-records`, and `verify-records`.
- **Reliability:** quiet subprocess cancellation, process reaping, temporary cleanup,
  graceful GUI exit, and tests for disk-full/disconnected-drive failures, long paths,
  changed records, and a real 128 MiB split archive.

## Validation

- Local Python 3.12: 125 passed, 20 skipped, 6 existing expected failures;
  coverage 73.47%. Display-dependent tests skip locally; the opt-in 128 MiB native
  split-archive/reverification test passed separately.
- Ruff and configured Bandit checks pass. GitHub Actions runs the Windows/Linux
  Python 3.10–3.13 matrix, security gates, Windows GUI tests, build, and EXE launch.
- I/O failure injection tests application handling; physical-drive and power-loss
  tests remain a separate hardware-validation task.

## Upgrade and operational notes

- Extract the entire Windows ZIP to a new folder. Keep `_internal` with the EXE.
- Install 7-Zip separately for 7z operations. Existing evidence archives remain compatible.
- Run **Job History → Import Prior Runs** to index retained v2.2.2 records.
  Imports are marked not reverified. The history index is separate from resume state.
- Delivery destinations remain archive-only. Metadata and `history.db` remain in
  the user's ForensicPack application-data folder.
- Scratch reservations coordinate one application process; other disk consumers
  can still exhaust capacity. Forced termination can leave temporary extraction files.
- Exported session reports can describe other items processed in the same session.
  Review before sharing. Hashes establish consistency, not independent authenticity;
  included signature files are not automatically signature-validated.
- History cannot restore overwritten archive bytes. Use stable evidence sources
  and retain the archive volumes associated with each run.

## Downloads

- `ForensicPack-v2.3.0-windows.zip` — application and required runtime files.
- `ForensicPack-v2.3.0-windows.zip.sha256` — package checksum.
- `ForensicPack-v2.3.0-sbom.cdx.json` — CycloneDX software bill of materials.
