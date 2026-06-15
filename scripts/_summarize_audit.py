import json
from collections import Counter
from pathlib import Path

data = json.loads(Path("bd-quality-audit.json").read_text())

print("ID|Category|Trade|Complete|Scanned|Shown|Top10Mix|Top1")
for d in data:
    p = d["profile"]
    s = d["statistics"]
    t = d["top_10_recommendations"]
    active = sum(1 for x in t if x["section"] == "Active Opportunities")
    pipe = sum(1 for x in t if x["section"] == "Market Pipeline")
    intel = sum(1 for x in t if x["section"] == "Competitive Intelligence")
    growth = sum(1 for x in t if x["section"] == "Growth Opportunities")
    top1 = t[0]["title"][:45] if t else "NONE"
    print(
        d["company_id"],
        d["category"][:14],
        p["primary_trade"][:14],
        p["profile_completeness"],
        s["scanned"],
        s["shown_total"],
        f"A{active}P{pipe}I{intel}G{growth}",
        top1,
        sep="|",
    )
