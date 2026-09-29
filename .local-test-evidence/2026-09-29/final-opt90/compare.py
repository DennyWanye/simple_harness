"""把本次每组日志里的失败/报错条目与 final-opt80 逐条对比：新增、消失、相同。"""
import re, sys
from pathlib import Path

NEW = Path(__file__).parent
OLD = NEW.parent / "final-opt80"
PAT = re.compile(r"^(FAILED|ERROR) (\S+)", re.M)


def items(path):
    return {f"{k} {n}" for k, n in PAT.findall(path.read_text(errors="replace"))} if path.exists() else None


def summary(path):
    if not path.exists():
        return "（无日志）"
    lines = [l for l in path.read_text(errors="replace").splitlines() if re.search(r"\d+ (passed|failed)|Tests +\d", l)]
    return lines[-1].strip() if lines else "（无汇总行）"


for log in sorted(NEW.glob("*.log")):
    old, new = items(OLD / log.name), items(log)
    if old is None:
        print(f"{log.stem}: 无基线 | {summary(log)}"); continue
    added, gone = sorted(new - old), sorted(old - new)
    print(f"{log.stem}: 相同 {len(new & old)}，新增 {len(added)}，消失 {len(gone)} | 本次 {summary(log)} | 基线 {summary(OLD / log.name)}")
    for a in added:
        print("   + " + a)
    for g in gone:
        print("   - " + g)
