"""只读盯梢：任务状态或任一步骤状态变化、或 10 分钟没有新事件就退出并打印现状。"""
import sqlite3, sys, time
DB = "/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-25/opt/ui-full/userdata/data/agent-orchestrator/orchestrator.db"
MID = sys.argv[1]

def snap():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    try:
        m = c.execute("select status from missions where mission_id=?", (MID,)).fetchone()[0]
        tasks = tuple(c.execute("select task_id, status from tasks where mission_id=? order by rowid", (MID,)).fetchall())
        seq, typ = c.execute("select seq, type from events where mission_id=? order by seq desc limit 1", (MID,)).fetchone()
        return m, tasks, seq, typ
    finally:
        c.close()

m0, t0, s0, typ = snap(); changed = time.time(); start = time.time()
while True:
    time.sleep(15)
    try:
        m, t, s, typ = snap()
    except Exception as e:
        print("read error", e, flush=True); continue
    if s != s0:
        s0, changed = s, time.time()
    if m != m0 or t != t0:
        print(f"CHANGE after {round(time.time()-start)}s mission={m} seq={s} last={typ}", flush=True)
        for tid, st in t: print(" ", tid[:26], st, flush=True)
        break
    if time.time() - changed > 600:
        print(f"STALLED mission={m} seq={s} last={typ}", flush=True); break
