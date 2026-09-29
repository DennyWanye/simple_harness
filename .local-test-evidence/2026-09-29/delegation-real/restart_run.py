"""收口第 4 项重启验证：委派子运行跑到一半时重启应用。

send  <out.json>：发一条要求委派的消息，看到新的子运行启动就退出（不等结论）。
watch <out.json>：重启后只读跟随主运行与子运行，直到都结束（最长 20 分钟）。
执行库只读打开；主会话消息从副本读取。
"""
import asyncio, json, shutil, sqlite3, sys, time, uuid
from pathlib import Path

import websockets

MODE, OUT = sys.argv[1], sys.argv[2]
ROOT = Path("/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-25/opt/ui-full/userdata/data")
DB = ROOT / "simple-harness-sdk/execution-v6.sqlite3"
PROMPT = (
    "请直接调用 agent 工具，把这个任务委派给子助手："
    "“列出二分查找的三个常见错误，每个一句话”。拿到子助手结果后原样告诉我，并注明来自子助手。"
)


def rows(sql, args=()):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def load():
    return json.load(open(OUT)) if Path(OUT).exists() else {}


def save(out):
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)


async def send():
    session = f"{int(time.time())}-delegation-restart"
    before = {r[0] for r in rows("SELECT run_id FROM runs WHERE run_id LIKE 'delegate-%'")}
    out = {"session": session, "prompt": PROMPT, "sent_at": time.time()}
    save(out)
    async with websockets.connect(f"ws://127.0.0.1:8100/ws/control?secret=dev&session_id={session}",
                                  max_size=None) as ws:
        await ws.send(json.dumps({"type": "chat", "request_id": str(uuid.uuid4()), "payload": {"text": PROMPT}}))
        start = time.time()
        while time.time() - start < 600:
            new = [r for r in rows("SELECT run_id, parent_run_id, state FROM runs WHERE run_id LIKE 'delegate-%'")
                   if r[0] not in before]
            if new:
                out.update(child=new[0][0], parent=new[0][1], child_state_at_launch=new[0][2],
                           launched_after=round(time.time() - start, 1))
                save(out); print("child", new[0], flush=True)
                return
            try:
                await asyncio.wait_for(ws.recv(), 2)
            except asyncio.TimeoutError:
                pass
    print("no child launched", flush=True)


def watch():
    out = load()
    start, last = time.time(), None
    out["watch"] = []
    while time.time() - start < 1200:
        states = dict(rows("SELECT run_id, state FROM runs WHERE run_id IN (?, ?)", (out["child"], out["parent"])))
        if states != last:
            last = states
            out["watch"].append({"t": round(time.time() - start, 1), **states}); save(out); print(states, flush=True)
        if all(states.get(k) in ("completed", "failed", "cancelled") for k in (out["child"], out["parent"])):
            break
        time.sleep(5)
    copy = Path(OUT).with_suffix(".statecopy")
    copy.mkdir(exist_ok=True)
    for name in ("state.db", "state.db-wal", "state.db-shm"):
        if (ROOT / name).exists():
            shutil.copy(ROOT / name, copy / name)
    con = sqlite3.connect(copy / "state.db")
    out["session_messages"] = [
        {"role": role, "content": (content or "")[:400]}
        for role, content in con.execute("SELECT role, content FROM messages WHERE session_id=? ORDER BY id",
                                         (out["session"],))
    ]
    con.close()
    shutil.rmtree(copy)
    save(out)
    print(json.dumps(out["session_messages"][-2:], ensure_ascii=False)[:800])


if MODE == "send":
    asyncio.run(send())
else:
    watch()
