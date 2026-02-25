"""Quick diagnostic — run extract eval and print detailed results."""
from tuning.runner import run_agent_suite, _make_client
from tuning.config import MODEL

client = _make_client()
results = run_agent_suite("extract", client, MODEL)

for r in results:
    cid = r["case_id"]
    err = r["error"]
    if err:
        print(f"{cid}: CRASH - {err[:80]}")
        continue
    s = r["score"]
    pct = s.pct
    w = r["weight"]
    status = "PERFECT" if pct >= 99 else "PARTIAL" if pct >= 50 else "FAIL"
    print(f"{cid}: {s.score:.1f}/{s.max_score:.1f} ({pct:.0f}%) w={w} [{status}]")
    for f in s.failures:
        print(f"  FAIL: {f}")
    for p in s.passes:
        print(f"  PASS: {p}")
    print()

# Show aggregate
total_ws = sum(r["score"].score * r["weight"] for r in results if r["score"])
total_wm = sum(r["score"].max_score * r["weight"] for r in results if r["score"])
loss = 1.0 - (total_ws / total_wm) if total_wm > 0 else 1.0
print(f"TOTAL: loss={loss:.4f} accuracy={100*(1-loss):.1f}%")
