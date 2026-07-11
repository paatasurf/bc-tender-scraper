# GitHub Branch Protection — master

**Status:** Required for Phase 1 production integration  
**Repo:** `paatasurf/bc-tender-scraper`  
**Protected branch:** `master`

## Required settings

| Setting | Value |
|---------|--------|
| Require a pull request before merging | **Yes** (prevents direct pushes that bypass review) |
| Require status checks to pass | **Yes** |
| Required checks | **Quality Gate** only (job name from `.github/workflows/quality-gate.yml`) |
| Require branches to be up to date | Recommended: Yes |
| Include administrators | **Yes** (no admin bypass in production discipline) |
| Allow force pushes | **No** |
| Allow deletions | **No** |

## Apply via GitHub UI

1. Repo → **Settings** → **Branches** → **Add branch ruleset** or **Branch protection rule**  
2. Branch name pattern: `master`  
3. Enable **Require status checks to pass before merging**  
4. Search and select **Quality Gate** (appears after the workflow has run at least once)  
5. Enable **Require a pull request before merging**  
6. Disable force pushes / deletions  
7. Save

## Apply via API (admin)

```bash
gh api -X PUT repos/paatasurf/bc-tender-scraper/branches/master/protection \
  -H "Accept: application/vnd.github+json" \
  -f required_status_checks='{"strict":true,"contexts":["Quality Gate"]}' \
  -F enforce_admins=true \
  -F required_pull_request_reviews='{"required_approving_review_count":0}' \
  -F restrictions= \
  -F allow_force_pushes=false \
  -F allow_deletions=false
```

Note: GitHub’s REST shape for nested objects may require `--input` JSON file. Prefer UI if API returns 422.

## Verification

- Open a PR with a failing test → merge button blocked / status red  
- Direct `git push origin master` → rejected  
