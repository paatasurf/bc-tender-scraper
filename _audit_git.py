import json
import subprocess
import urllib.parse
import urllib.request

repo = r"C:\Users\DAVIDSURF\Projects\bc-tender-scraper"

def git(*args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode

out = {}
head, _, _ = git("rev-parse", "HEAD")
out["local_head"] = head
log, _, _ = git("log", "-15", "--format=%H|%ci|%s")
out["local_log"] = [dict(zip(["sha", "date", "subject"], line.split("|", 2))) for line in log.splitlines() if line]

remote, _, _ = git("remote", "-v")
out["remotes"] = remote

branch, _, _ = git("branch", "-vv")
out["branches"] = branch

# Check if key files exist in HEAD
files = [
    "db/migrations/002_company_award_columns.sql",
    "pipeline/populate_companies_from_awards.py",
    "pipeline/refresh_company_award_stats.py",
    "run_populate_award_companies.py",
    "run_award_company_phase_c.py",
    "db/models.py",
]
for f in files:
    show, _, code = git("show", f"HEAD:{f}")
    out[f"file_{f.replace('/', '_')}"] = "exists" if code == 0 else "missing"

# Production API probes
BASE = "https://bc-tender-scraper-production.up.railway.app"

def fetch(path):
    req = urllib.request.Request(f"{BASE}{path}", headers={"User-Agent": "audit/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

try:
    health = fetch("/api/health")
    stats = fetch("/api/stats")
    company = fetch("/api/companies?search=" + urllib.parse.quote("BD Hall") + "&limit=1")
    row = company["data"][0] if company.get("data") else {}
    awards = fetch("/api/contract-awards?limit=1")
    award_row = awards["data"][0] if awards.get("data") else {}
    out["production"] = {
        "health_keys": sorted(health.keys()),
        "stats_keys": sorted(stats.keys()),
        "stats": stats,
        "company_keys_sample": sorted(row.keys()),
        "has_award_count": "award_count" in row,
        "award_count": row.get("award_count"),
        "data_sources": row.get("data_sources"),
        "contract_award_keys": sorted(award_row.keys()),
        "contract_award_sample": award_row,
    }
except Exception as e:
    out["production_error"] = str(e)

with open(r"C:\Users\DAVIDSURF\Projects\bc-tender-scraper\_audit_output.json", "w") as f:
    json.dump(out, f, indent=2)
print(json.dumps(out, indent=2))
