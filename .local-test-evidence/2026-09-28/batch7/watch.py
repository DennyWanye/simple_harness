"""复杂真机任务看门狗：只读打开编排库；任务状态变化、结束、或 10 分钟没有新事件就退出（让主会话收到通知）。"""
import sqlite3, sys, time
DB = "/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-25/opt/ui-full/userdata/data/agent-orchestrator/orchestrator.db"
MID = sys.argv[1]
STALL = int(sys.argv[2]) if len(sys.argv) > 2 else 600

def snap():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    try:
        status = c.execute("select status from missions where mission_id=?", (MID,)).fetchone()[0]
        seq, typ = c.execute("select seq, type from events where mission_id=? order by seq desc limit 1", (MID,)).fetchone() or (0, "")
        return status, seq, typ
    finally:
        c.close()

start = time.time(); status0, seq0, typ = snap(); changed = time.time()
print(f"start status={status0} seq={seq0} last={typ}", flush=True)
while True:
    time.sleep(30)
    try:
        status, seq, typ = snap()
    except Exception as e:
        print("read error", e, flush=True); continue
    if seq != seq0:
        seq0, changed = seq, time.time()
    if status != status0:
        print(f"STATUS {status0} -> {status} after {round(time.time()-start)}s seq={seq} last={typ}", flush=True); break
    if time.time() - changed > STALL:
        print(f"STALLED status={status} seq={seq} last={typ} idle={round(time.time()-changed)}s", flush=True); break
