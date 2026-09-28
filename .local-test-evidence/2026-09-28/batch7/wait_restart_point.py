"""等到"至少一步完成、另一步正在执行（执行意图已认领/建好 Agent）"再退出，作为强制重启的时机；10 分钟无新事件也退出。只读。"""
import sqlite3, sys, time
DB = "/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-25/opt/ui-full/userdata/data/agent-orchestrator/orchestrator.db"
MID = sys.argv[1]
last_seq, changed = None, time.time()
while True:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    try:
        tasks = dict(c.execute("select task_id, status from tasks where mission_id=?", (MID,)).fetchall())
        intents = c.execute("select kind, state, subject_id from dispatch_intents where mission_id=? and kind='attempt'", (MID,)).fetchall()
        seq = c.execute("select max(seq) from events where mission_id=?", (MID,)).fetchone()[0]
        status = c.execute("select status from missions where mission_id=?", (MID,)).fetchone()[0]
    finally:
        c.close()
    if seq != last_seq:
        last_seq, changed = seq, time.time()
    done = [t for t, s in tasks.items() if s == "COMPLETED"]
    running = [i for i in intents if i[1] in ("CLAIMED", "AGENT_CREATED", "SUBSCRIBED") and not any(i[2].startswith(d) for d in done)]
    if done and running:
        print("RESTART_POINT", {"done": done, "running": running, "seq": seq}, flush=True); break
    if status in ("COMPLETED", "FAILED", "CANCELLED") or time.time() - changed > 600:
        print("EXIT", status, tasks, intents, seq, flush=True); break
    time.sleep(10)
