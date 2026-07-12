#!/usr/bin/env bash
# Apply GitHub branch protection rules for master.
# Requires: gh CLI authenticated with admin access to paatasurf/bc-tender-scraper.
# Docs: docs/engineering/GITHUB_BRANCH_PROTECTION.md

set -euo pipefail

REPO="paatasurf/bc-tender-scraper"
BRANCH="master"

cat > /tmp/branch-protection.json <<'JSON'
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
JSON

echo "Applying branch protection to $REPO/$BRANCH ..."
gh api -X PUT "repos/$REPO/branches/$BRANCH/protection" \
  -H "Accept: application/vnd.github+json" \
  --input /tmp/branch-protection.json

echo "Branch protection applied."
echo ""
echo "Verification:"
echo "  - Open a PR without review -> merge blocked"
echo "  - Open a PR with failing Quality Gate -> merge blocked"
echo "  - Direct git push origin master -> rejected"
