"""Markdown summary of matrix-grok46-256k-v1 from stored result.json files (no model calls)."""
import json, sys
from collections import defaultdict
from pathlib import Path
OUT = Path(__file__).resolve().parent / "matrix-grok46-256k-v2"
rows = [json.loads(p.read_text()) for p in sorted((OUT / "episodes").glob("*/result.json"))]
reps = set(int(a) for a in sys.argv[1:]) or None
if reps is not None:
    rows = [r for r in rows if r["repetition"] in reps]
def usage(r, k): return (r.get("known_usage_lower_bound") or {}).get(k) or 0
print(f"episodes: {len(rows)}  (reps {sorted(set(r['repetition'] for r in rows))})\n")
print("| 臂 | 局数 | 官方通过 | 有效成功 | 错误 | 未知用量局 | 调用 | 输入 token | 输出 token | 总 token | 每有效成功 token | 平均秒 | 知识复用事件 | 停止原因 |")
print("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|")
for arm in "SRDF":
    a = [r for r in rows if r["arm"] == arm]
    if not a: continue
    off = sum(r.get("official_utility") is True for r in a); val = sum(bool(r.get("valid_success")) for r in a)
    err = sum("error_type" in r for r in a); unk = sum(bool(r.get("unknown_usage_calls")) for r in a)
    calls = sum(usage(r, "calls") for r in a); ti = sum(usage(r, "input_tokens") for r in a)
    to = sum(usage(r, "output_tokens") for r in a); tt = sum(usage(r, "total_tokens") for r in a)
    per = f"{tt // val}" if val else "无成功"
    secs = sum(r.get("elapsed_seconds", 0) for r in a) / len(a)
    kre = sum((r.get("knowledge_reuse_events") or 0) if isinstance(r.get("knowledge_reuse_events"), int) else len(r.get("knowledge_reuse_events") or []) for r in a)
    stops = defaultdict(int)
    for r in a: stops[r.get("error_type") or ("ok" if r.get("official_utility") else "official_false")] += 1
    print(f"| {arm} | {len(a)} | {off} | {val} | {err} | {unk} | {calls} | {ti} | {to} | {tt} | {per} | {secs:.0f} | {kre} | {dict(stops)} |")
tt = sum(usage(r, "total_tokens") for r in rows); print(f"\n合计 {sum(usage(r,'calls') for r in rows)} 调用 / {tt} tokens / 未知用量局 {sum(bool(r.get('unknown_usage_calls')) for r in rows)}")
print("\n| 题 | " + " | ".join("SRDF") + " |"); print("|---|" + "---|" * 4)
by = defaultdict(dict)
for r in rows:
    by[r["task_id"]][(r["arm"], r["repetition"])] = ("✅" if r.get("official_utility") is True else ("❌" if r.get("official_utility") is False else "⚠")) + (f"e:{r['error_type'][:12]}" if r.get("error_type") else "")
for t in sorted(by):
    cells = []
    for arm in "SRDF":
        cells.append(" ".join(f"r{rep}:{by[t].get((arm, rep), '·')}" for rep in sorted(set(k[1] for k in by[t]))))
    print(f"| {t} | " + " | ".join(cells) + " |")
