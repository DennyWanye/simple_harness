"""N7 paired analysis for matrix-grok46-256k-v2 (same task, same model, four arms). Reads result.json only; no model calls.

Outputs Markdown: per-arm metrics, per-task pairing, paired differences vs S with task-level bootstrap CIs,
false-completion rate, cost per success, knowledge-reuse counts. 12 tasks x 2 reps -> aggregate by task first.
"""
import json, random, statistics, sys
from collections import defaultdict
from pathlib import Path
OUT = Path(__file__).resolve().parent / "matrix-grok46-256k-v2"
ARMS = "SRDF"
random.seed(100)
rows = [json.loads(p.read_text()) for p in sorted((OUT / "episodes").glob("*/result.json"))]
def u(r, k): return (r.get("known_usage_lower_bound") or {}).get(k) or 0
def official(r): return r.get("official_utility") is True
def false_complete(r):
    rt = r.get("runtime") or {}
    if r["arm"] in "DF":
        return rt.get("mission_status") == "COMPLETED" and not official(r)
    return (r.get("official") or {}).get("declared_task_status") == "success" and not official(r)
def kre(r):
    v = (r.get("runtime") or {}).get("knowledge_reuse_events")
    return v if isinstance(v, int) else len(v or [])
by_task = defaultdict(lambda: defaultdict(list))
for r in rows: by_task[r["task_id"]][r["arm"]].append(r)
tasks = sorted(by_task)
print(f"# N7 配对分析（{OUT.name}）\n\n局数 {len(rows)}，题数 {len(tasks)}，重复 {sorted(set(r['repetition'] for r in rows))}\n")
print("## 1. 各臂汇总（全部局）\n")
print("| 臂 | 局 | 官方通过率 | 错误宣布完成 | 未知用量局 | 错误局 | 均调用/局 | 均 token/局 | 每官方成功 token | 均秒/局 | 知识复用事件 |")
print("|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
for a in ARMS:
    A = [r for r in rows if r["arm"] == a]
    if not A: continue
    ok = sum(map(official, A)); tt = sum(u(r, "total_tokens") for r in A)
    print(f"| {a} | {len(A)} | {ok}/{len(A)} = {ok/len(A):.0%} | {sum(map(false_complete, A))} | {sum(bool(r.get('unknown_usage_calls')) for r in A)} | "
          f"{sum('error_type' in r for r in A)} | {sum(u(r,'calls') for r in A)/len(A):.1f} | {tt/len(A):,.0f} | "
          f"{(tt/ok):,.0f}" if ok else f"| {a} | {len(A)} | 0 | {sum(map(false_complete, A))} | - | - | - | - | 无成功", end="")
    print(f" | {statistics.mean(r.get('elapsed_seconds',0) for r in A):.0f} | {sum(map(kre, A))} |")
print("\n## 2. 按题聚合（每格 = 该臂在该题的官方通过数/局数）\n")
print("| 题 | " + " | ".join(ARMS) + " | S token | F token |"); print("|---|" + "---|" * (len(ARMS) + 2))
task_rate = {a: {} for a in ARMS}; task_tok = {a: {} for a in ARMS}
for t in tasks:
    cells = []
    for a in ARMS:
        A = by_task[t].get(a, [])
        if A:
            task_rate[a][t] = sum(map(official, A)) / len(A); task_tok[a][t] = statistics.mean(u(r, "total_tokens") for r in A)
            cells.append(f"{sum(map(official, A))}/{len(A)}")
        else: cells.append("·")
    print(f"| {t} | " + " | ".join(cells) + f" | {task_tok['S'].get(t, 0):,.0f} | {task_tok['F'].get(t, 0):,.0f} |")
def boot(diffs, n=5000):
    if not diffs: return (float("nan"),) * 3
    means = []
    for _ in range(n):
        s = [random.choice(diffs) for _ in diffs]; means.append(sum(s) / len(s))
    means.sort(); return (sum(diffs) / len(diffs), means[int(0.025 * n)], means[int(0.975 * n)])
print("\n## 3. 与 S 的配对差（按题配对，task-level bootstrap 95% CI，5000 次）\n")
print("| 对比 | 配对题数 | 通过率差（臂 − S） | 95% CI | token 差/局（臂 − S） | 95% CI | 赢/平/输 |"); print("|---|--:|--:|---|--:|---|---|")
for a in "RDF":
    common = [t for t in tasks if t in task_rate[a] and t in task_rate["S"]]
    d_rate = [task_rate[a][t] - task_rate["S"][t] for t in common]
    d_tok = [task_tok[a][t] - task_tok["S"][t] for t in common]
    m, lo, hi = boot(d_rate); mt, lot, hit = boot(d_tok)
    w = sum(d > 0 for d in d_rate); l = sum(d < 0 for d in d_rate); e = len(d_rate) - w - l
    print(f"| {a} vs S | {len(common)} | {m:+.3f} | [{lo:+.3f}, {hi:+.3f}] | {mt:+,.0f} | [{lot:+,.0f}, {hit:+,.0f}] | {w}/{e}/{l} |")
print("\n## 4. 失败明细\n")
print("| 臂 | 题 | 重复 | 错误类型 | 错误宣布完成 | 未知用量 | Mission 状态 / 停止原因 | 官方失败标签 |"); print("|---|---|--:|---|---|--:|---|---|")
for r in sorted(rows, key=lambda r: (r["arm"], r["task_id"], r["repetition"])):
    if official(r) and "error_type" not in r and not r.get("unknown_usage_calls"): continue
    rt = r.get("runtime") or {}
    labels = ",".join(sorted({f.get("label", "?") for f in (r.get("official") or {}).get("failures", [])}))[:60]
    print(f"| {r['arm']} | {r['task_id']} | {r['repetition']} | {r.get('error_type','')} | {'是' if false_complete(r) else ''} | {r.get('unknown_usage_calls',0)} | {rt.get('mission_status','')} / {rt.get('stop_reason','')} | {labels} |")
print("\n## 5. 解读边界\n\n- 12 题各 2 次不是 24 道独立题；CI 按题 bootstrap，样本小，跨零的差异记为无定论。\n- 同模型同预算，差异可归因于臂（方案），不能归因于模型。\n- 知识复用事件为 0 只说明机制未触发。\n- 每成功 token = 该臂全部局 token / 官方成功局数（含失败局成本）。")
