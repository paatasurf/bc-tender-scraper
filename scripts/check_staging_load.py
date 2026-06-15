import json
import re
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://integer-boys-declared-deaths.trycloudflare.com"


def get(path: str, timeout: int = 120):
    url = BASE + path
    t = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read(), round(time.time() - t, 1)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), round(time.time() - t, 1)


print("URL:", BASE)
status, html, dt = get("/")
text = html.decode("utf-8", errors="replace")
print(f"page {status} {dt}s bytes={len(html)}")
scripts = re.findall(r'src="(/_next/[^"]+)"', text)
print(f"script tags={len(scripts)}")
if scripts:
    ss, body, sdt = get(scripts[0], timeout=30)
    print(f"first script {ss} {sdt}s bytes={len(body)}")

for path in [
    "/api/permits?limit=500&offset=0",
    "/api/tenders?limit=500&offset=0",
    "/api/signals?limit=500&offset=0",
    "/api/contract-awards?limit=10&offset=0",
    "/api/companies/id/1735/opportunities?min_score=50&limit=3",
]:
    code, body, dt = get(path)
    print(f"{path} -> {code} {dt}s size={len(body)}")
