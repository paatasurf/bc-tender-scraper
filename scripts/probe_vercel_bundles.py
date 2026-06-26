"""Search deployed Vercel JS bundles for Feature 006 markers."""
import re
import urllib.request

CANDIDATES = [
    "https://v0-construction-dashboard.vercel.app",
    "https://construction-dashboard.vercel.app",
]

MARKERS = [
    "CompetitiveIntelligence",
    "competitive-intelligence",
    "Company Intelligence",
    "TenderScope",
    "bd-intelligence",
    "Benchmark strip",
]


def probe(base: str) -> None:
    print(f"=== {base} ===")
    html = urllib.request.urlopen(base, timeout=20).read().decode("utf-8", "replace")
    title = re.search(r"<title>(.*?)</title>", html)
    print("title:", title.group(1) if title else "none")
    chunks = re.findall(r"/_next/static/chunks/[^\"']+\\.js", html)
    print("chunk refs:", len(chunks))
    hits: list[str] = []
    for chunk in chunks[:12]:
        url = base + chunk
        try:
            js = urllib.request.urlopen(url, timeout=20).read().decode("utf-8", "replace")
        except Exception as exc:
            print("  skip", chunk, exc)
            continue
        found = [m for m in MARKERS if m in js]
        if found:
            hits.append(chunk)
            print("  HIT", chunk, found)
    if not hits:
        print("  no Feature 006 markers in first 12 chunks")
    for path in [
        "/api/companies/id/1921/competitive-intelligence?peer_limit=5",
        "/api/companies/id/1921/bd-intelligence?kind=construction",
    ]:
        try:
            urllib.request.urlopen(base + path, timeout=20)
            print(path, "-> 200")
        except Exception as exc:
            print(path, "->", exc)


def main() -> None:
    for base in CANDIDATES:
        try:
            probe(base)
        except Exception as exc:
            print(base, "FAIL", exc)
        print()


if __name__ == "__main__":
    main()
