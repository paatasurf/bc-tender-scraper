# GitHub Branch Protection — master

**Status:** Required for true CI/CD pipeline  
**Repo:** `paatasurf/bc-tender-scraper`  
**Protected branch:** `master`

## Required settings

| Setting | Value |
|---------|--------|
| Require a pull request before merging | **Yes** (prevents direct pushes that bypass review) |
| Required approving reviews | **1** |
| Dismiss stale PR approvals | **Yes** |
| Require status checks to pass | **Yes** |
| Required checks | **Quality Gate** (aggregate gate job from `.github/workflows/quality-gate.yml`) |
| Require branches to be up to date | **Yes** |
| Include administrators | **Yes** (no admin bypass in production discipline) |
| Allow force pushes | **No** |
| Allow deletions | **No** |

## Why these settings

- **PR required + 1 review:** No direct pushes to `master`. Every change is reviewed.
- **Quality Gate required:** The aggregate gate fails if Ruff, Black, pytest, or OpenCode review fails. Railway deployment can only happen after this gate passes.
- **Up-to-date required:** PRs must be rebased on the latest `master` before merge, preventing broken merges.
- **Admins included:** Repository admins cannot bypass protection rules.

## Apply via GitHub UI

1. Repo → **Settings** → **Branches** → **Add branch ruleset** or **Branch protection rule**  
2. Branch name pattern: `master`  
3. Enable **Require a pull request before merging** → set **Required approving reviews** to `1`  
4. Enable **Require status checks to pass before merging**  
5. Search and select **Quality Gate** (the aggregate job from the workflow)  
6. Enable **Require branches to be up to date before merging**  
7. Check **Include administrators**  
8. Disable force pushes and deletions  
9. Save

## Apply via API (admin)

Create a JSON file `branch-protection.json`:

```json
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["Quality Gate"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
```

Then run:

```bash
gh api -X PUT repos/paatasurf/bc-tender-scraper/branches/master/protection \
  -H "Accept: application/vnd.github+json" \
  --input branch-protection.json
```

> Note: The exact required check name is the aggregate job named **Quality Gate** in `.github/workflows/quality-gate.yml`. If GitHub reports the check as `Quality Gate / Quality Gate` or a different string, update `contexts` accordingly after the first workflow run.

## Verification

- Open a PR with a failing test → merge button blocked / status red  
- Open a PR without an approving review → merge button blocked  
- Direct `git push origin master` → rejected  
- Merged PR → Quality Gate must be green before Railway auto-deploys  
