import json
import subprocess

repo = r"C:\Users\DAVIDSURF\Projects\bc-tender-scraper"

def git(*args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return r.stdout, r.stderr, r.returncode

out = {}

# git status
status, _, _ = git("status", "--porcelain")
out["status_lines"] = status.splitlines()

# Check HEAD versions of key snippets
checks = {
    "models_award_count": ("grep", "-n", "award_count", "db/models.py"),
    "models_contract_award": ("grep", "-n", "class ContractAward", "db/models.py"),
    "main_contract_awards_endpoint": ("grep", "-n", "list_contract_awards", "api/main.py"),
    "main_scheduler": ("grep", "-n", "start_scheduler", "api/main.py"),
    "main_stats_contract": ("grep", "-n", "contract_awards", "api/main.py"),
}
for name, args in checks.items():
    stdout, _, code = git("show", f"HEAD:{args[-1]}")
    if code != 0:
        out[name] = "file missing in HEAD"
        continue
    import re
    pattern = args[-1].split("/")[-1]
    if "grep" in name:
        needle = args[2] if len(args) > 2 else ""
        lines = [l for l in stdout.splitlines() if needle in l]
        out[name] = lines[:5] if lines else "NOT FOUND"
    else:
        out[name] = stdout[:200]

# Working tree same checks
for name, path, needle in [
    ("wt_models_award_count", "db/models.py", "award_count"),
    ("wt_contract_award_class", "db/models.py", "class ContractAward"),
    ("wt_populate_awards", "pipeline/populate_companies_from_awards.py", "populate_companies_from_awards"),
    ("wt_refresh_stats", "pipeline/refresh_company_award_stats.py", "refresh_company_award_stats"),
]:
    try:
        with open(f"{repo}/{path}", encoding="utf-8") as f:
            content = f.read()
        out[name] = "FOUND" if needle in content else "NOT FOUND"
    except FileNotFoundError:
        out[name] = "FILE MISSING"

# Find commit that added contract awards if any
log, _, _ = git("log", "--all", "--oneline", "--", "db/models.py", "api/main.py")
out["recent_model_main_commits"] = log.splitlines()[:20]

# origin/master sha
sha, _, _ = git("rev-parse", "origin/master")
out["origin_master"] = sha.strip()

# diff stat uncommitted
diff, _, _ = git("diff", "--stat", "HEAD")
out["uncommitted_diff_stat"] = diff

with open(r"C:\Users\DAVIDSURF\Projects\bc-tender-scraper\_audit_output2.json", "w") as f:
    json.dump(out, f, indent=2)
print(json.dumps(out, indent=2))
