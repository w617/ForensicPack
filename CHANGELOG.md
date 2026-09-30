# Changelog

## Unreleased — 30-Sep-2026

### Fixed

1. **Retain source evidence when packaging is incomplete.** Source deletion now
   requires a complete inventory, archived-member SHA-256 verification covering
   every source file, and no unresolved packaging warnings. A fresh inventory
   and SHA-256 readback must also match before deletion is authorized. Disabled
   verification, scan omissions, and changed source files retain the source and
   produce an explicit warning. The audit records the deletion eligibility check.
2. **Remove recursive 7-Zip execution.** Archive creation and structural checking
   receive their runner explicitly rather than replacing the runner globally.
   Passwords are supplied to structural verification. Embedded manifests are
   included in the initial create operation so encrypted and split archives do
   not require a second archive update.
3. **Revalidate resume candidates.** Only completed, warning-free jobs qualify.
   Resume verifies the saved JSON manifest against its digest in SQLite, checks
   the expected archive destination and complete volume set, hashes every archive
   volume, and compares a fresh source inventory and SHA-256 values with the saved
   manifest. Missing, corrupt, changed, incomplete, or legacy records rebuild
   rather than being silently skipped. Resume no longer disables unrelated-output
   collision protection, and enabling Skip Existing cannot bypass resume checks.
4. **Preserve per-run audit history and metadata.** Each packaging attempt gets a
   unique `Runs/<run-id>` workspace containing its audit log and generated
   metadata. Reports and SQLite records point to the original run files. Latest
   successful metadata copies remain in the destination's application-data
   workspace for compatibility. Existing legacy metadata is copied into
   `History/<identifier>` before replacement. AuditLogger refuses an existing log
   path rather than truncating it, and report names include microseconds.
5. **Fail closed on invalid or incomplete member verification.** Invalid 7-Zip
   signatures no longer use a production test-fixture bypass. Missing source
   hashes, duplicate inventory names, and zero/partial member checks cannot be
   reported as a complete verification pass. Synthetic archive fixtures now
   substitute a verifier explicitly within their orchestration tests.

### Compatibility and operational notes

- Delivery folders continue to contain archives only. Run records remain under
  `%LOCALAPPDATA%\ForensicPack\Cases\<destination-name>-<identifier>` on Windows.
- Existing SQLite databases acquire two manifest-validation columns automatically.
  Old jobs without the saved manifest digest require a rebuild before safe resume.
- Resume now reads source and archive bytes for validation; large cases take longer
  than the previous database-only skip. Ordinary packaging adds this source
  readback only when source deletion is requested.
- Historical records preserve what was recorded at the time. They do not preserve
  overwritten archive bytes; a historical checksum can legitimately differ from
  a subsequently rebuilt archive at the same destination.
- Source validation is a point-in-time check, not a filesystem snapshot or lock.
  Packaging remains intended for stable evidence sources.

### Validation

- Added targeted regression coverage for incomplete scans; deletion with disabled
  verification; source additions and changes before cleanup; safe deletion after
  full verification; changed, missing, corrupt, and legacy resume inputs; split
  volume completeness and hashes; collision protection; retained audit history;
  legacy metadata preservation; invalid archive signatures; zero-member checks;
  and nonrecursive 7-Zip execution.
- Native 7-Zip integration coverage exercises plain, encrypted, and split archive
  creation, structural checking, member-hash comparison, and package checksums.
- Full local Python 3.12 suite: **102 passed, 17 skipped, 6 expected failures**.
  The skipped tests require a graphical Tk environment. Existing expected failures
  cover prior metadata-layout and symlink-policy expectations. Ruff checks pass.
- Windows executable validation is handled by the existing GitHub Actions build
  and smoke-launch jobs; no new executable is published by this change itself.

## 2.2.1 — 29-Jul-2026

- Fixed checksum paths when archives and application metadata are on different
  Windows drives.
- Recognized prior ForensicPack outputs during rebuild while retaining protection
  against overwriting unrelated archives.

## 2.2.0 — 14-Jul-2026

- Moved generated metadata and resume state to private application data.
- Kept delivery destinations archive-only and simplified the examiner interface.
