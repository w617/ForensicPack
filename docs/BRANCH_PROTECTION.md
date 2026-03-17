# Branch Protection Setup (main)

Apply these settings on GitHub for `main`:

- Require a pull request before merging
- Require status checks to pass before merging
  - `CI / Tests (ubuntu-latest / py3.10) (push)`
  - `CI / Tests (ubuntu-latest / py3.11) (push)`
  - `CI / Tests (ubuntu-latest / py3.12) (push)`
  - `CI / Tests (windows-latest / py3.10) (push)`
  - `CI / Tests (windows-latest / py3.11) (push)`
  - `CI / Tests (windows-latest / py3.12) (push)`
  - `Smoke CLI / smoke (push)`
- Require branches to be up to date before merging
- Restrict direct pushes to `main` (recommended)

## Manual Path

1. Open: `Settings -> Branches -> Add branch protection rule`
2. Branch name pattern: `main`
3. Enable options above and save.

## Optional CLI (GitHub CLI)

If `gh` is installed and authenticated:

```bash
gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  /repos/w617/ForensicPack/branches/main/protection \
  -f required_status_checks.strict=true \
  -f enforce_admins=true \
  -f required_pull_request_reviews.dismiss_stale_reviews=true \
  -f restrictions=
```

Then add required checks in the GitHub UI (recommended for reliability).
