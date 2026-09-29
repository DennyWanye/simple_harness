"""收口第 4 项真实验证：主对话里让模型用 agent 委派一个小任务，看子运行是否完成、主对话是否收到结论。

只走开发模式控制通道（与界面发消息同一个 chat 消息类型）；只读打开执行库核对子运行。
用法：python chat_run.py <out.json> [提示词]
"""
import asyncio, json, sqlite3, sys, time, uuid
import websockets

OUT = sys.argv[1] if len(sys.argv) > 1 else "chat_run.json"
PROMPT = sys.argv[2] if len(sys.argv) > 2 else (
    "请用 agent 工具（先用 tool_search 找到它并激活）把这个小任务委派给子助手去做："
    "“列出三种常见排序算法和它们的平均时间复杂度，每种一句话”。"
    "拿到子助手的结果后，原样告诉我它的结论，并说明是子助手给的。"
)
DB = ("/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-25/opt/ui-full/"
      "userdata/data/simple-harness-sdk/execution-v6.sqlite3")
SESSION = f"delegation-real-{int(time.time())}"


def children():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT run_id, parent_run_id, state, created_at FROM runs "
            "WHERE run_id LIKE 'delegate-%' ORDER BY created_at DESC LIMIT 10").fetchall()
        return [dict(zip(("run_id", "parent_run_id", "state", "created_at"), row)) for row in rows]
    finally:
        con.close()


async def main():
    out = {"session": SESSION, "prompt": PROMPT, "events": [], "children_before": children()}
    start = time.time()

    def note(kind, value):
        out["events"].append({"t": round(time.time() - start, 1), kind: value})
        print(kind, json.dumps(value, ensure_ascii=False)[:600], flush=True)
        json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)

    url = f"ws://127.0.0.1:8100/ws/control?secret=dev&session_id={SESSION}"
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "chat", "request_id": str(uuid.uuid4()),
                                  "payload": {"text": PROMPT}}))
        last = time.time()
        while time.time() - start < 1200:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 30))
            except asyncio.TimeoutError:
                if time.time() - last > 600:
                    note("watchdog", "no event for 10 minutes"); break
                continue
            last = time.time()
            kind = msg.get("type", "")
            payload = msg.get("payload") or {}
            if kind.startswith("chat_v2_") or "tool" in kind or kind in ("chat_response", "error"):
                slim = {k: payload.get(k) for k in ("text", "content", "tool_name", "name", "status",
                                                    "error", "message", "code", "state") if payload.get(k)}
                note(kind, slim)
            if kind in ("chat_v2_final", "chat_v2_error", "chat_v2_interrupted"):
                break
    out["children_after"] = children()
    note("children", out["children_after"][:5])
    json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=1)

asyncio.run(main())
