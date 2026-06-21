import sys, json
from collections import Counter
# Args: <metrics_file> <baseline_line>
path = sys.argv[1]; base = int(sys.argv[2])
rows = []
with open(path, encoding="utf-8") as f:
    for i, ln in enumerate(f, 1):
        if i <= base:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if d.get("event") != "subagent_progress":
            continue
        det = d.get("detail", {})
        if "run_id" not in det:
            continue  # only scheduler run_id-based events
        rows.append((d["ts"], det.get("task_id"), det.get("kind"), det.get("status"), det.get("duration_ms")))
if not rows:
    print("(no run_id-based subagent_progress rows yet)"); sys.exit()
t0 = min(r[0] for r in rows)
for ts, tid, kind, st, dur in sorted(rows):
    d = f"{dur}ms" if dur else ""
    print(f"+{ts-t0:6.2f}s  {str(kind):8} {str(tid):24} {str(st):10} {d}")
print("--- status counts ---")
print(dict(Counter(r[3] for r in rows)))
# Concurrency check: max simultaneously running
events = []
for ts, tid, kind, st, dur in rows:
    if st == "running":
        events.append((ts, +1))
    elif st in ("completed", "failed"):
        events.append((ts, -1))
events.sort()
cur = peak = 0
for ts, delta in events:
    cur += delta
    peak = max(peak, cur)
print(f"peak concurrent running = {peak}")
print(f"distinct task_ids = {len(set(r[1] for r in rows))}")
