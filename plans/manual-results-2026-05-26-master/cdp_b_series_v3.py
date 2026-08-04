# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""B1/B2/B5 v3 — 用更明确 prompt 让 LLM 直接 function-call office tools."""
import asyncio
import json
import time
import urllib.request
from pathlib import Path

import websockets

OUT = Path(__file__).parent / "cdp_b_v3_results.json"
RESULTS = []


def find_main():
    d = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    return next(t for t in d if t.get("type") == "page" and t.get("url", "").rstrip("/").endswith("tauri.localhost"))


async def evaluate(ws, expr, mid, await_p=False):
    await ws.send(json.dumps({
        "id": mid, "method": "Runtime.evaluate",
        "params": {"expression": expr, "returnByValue": True, "awaitPromise": await_p},
    }))
    while True:
        m = json.loads(await ws.recv())
        if m.get("id") == mid:
            return m.get("result", {})


def record(case, status, detail=""):
    entry = {"case": case, "status": status, "detail": str(detail)[:300], "ts": time.strftime("%H:%M:%S")}
    RESULTS.append(entry)
    marker = {"PASS": "✓", "FAIL": "✗", "SKIP": "?", "PARTIAL": "~"}.get(status, "·")
    print(f"  [{marker}] {case:34s} {status:7s} — {str(detail)[:140]}")


async def send_chat(ws, text, mid):
    js = f"""
(() => {{
  const el = document.querySelector('[data-testid="chat-input"]');
  if (!el) return {{ ok: false, err: 'no input' }};
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, {json.dumps(text)});
  el.dispatchEvent(new Event('input', {{ bubbles: true }}));
  const sb = document.querySelector('[data-testid="send-button"]');
  if (sb.disabled) return {{ ok: false, err: 'send disabled' }};
  sb.click();
  return {{ ok: true }};
}})()
"""
    return (await evaluate(ws, js, mid)).get("result", {}).get("value", {})


async def find_artifact(pattern: str, since: float, timeout_s: int = 90):
    """Search common artifact dirs."""
    candidates = [
        Path("F:/deskpet/userdata"),
        Path("F:/deskpet/userdata/artifacts"),
        Path("F:/DeskPetData"),
        Path("F:/DeskPetData/artifacts"),
        Path("F:/DeskPet/data"),
        Path("F:/DeskPet/data/artifacts"),
        Path(r"C:\Users\24378\AppData\Local\Temp"),
    ]
    end = time.time() + timeout_s
    while time.time() < end:
        for d in candidates:
            if not d.exists():
                continue
            try:
                for p in d.rglob(pattern):
                    try:
                        if p.stat().st_mtime > since:
                            return p
                    except OSError:
                        continue
            except (OSError, PermissionError):
                continue
        await asyncio.sleep(2)
    return None


async def approve_permissions(ws, mid):
    js = r"""
(() => {
  const btns = Array.from(document.querySelectorAll('button'));
  const order = ['本会话始终允许', '本会话允许', '允许一次', '允许', 'Allow'];
  for (const t of order) {
    const b = btns.find(b => (b.innerText || '').trim() === t && b.offsetParent !== null);
    if (b) { b.click(); return { clicked: t }; }
  }
  return { clicked: null };
})()
"""
    return (await evaluate(ws, js, mid)).get("result", {}).get("value", {})


async def run_case(ws, name, prompt, file_pattern, timeout_s, mid_start):
    print(f"\n── {name} ──")
    print(f"  prompt: {prompt[:80]}")
    send = await send_chat(ws, prompt, mid_start)
    print(f"  send: {send}")
    if not send.get("ok"):
        record(name, "FAIL", f"send failed: {send}")
        return

    start = time.time()
    end = start + timeout_s
    approvals = []
    while time.time() < end:
        await asyncio.sleep(2)
        # auto-approve
        ap = await approve_permissions(ws, mid_start + 100 + int(time.time()))
        if ap.get("clicked"):
            approvals.append(ap["clicked"])
            print(f"  [auto-approved] {ap['clicked']}")

        f = await find_artifact(file_pattern, start, timeout_s=2)
        if f:
            record(name, "PASS",
                   f"artifact: {f.name} size={f.stat().st_size} path={f}")
            return

    r = await evaluate(ws, "document.body.innerText.slice(-400)", mid_start + 300)
    tail = r.get("result", {}).get("value", "")
    record(name, "FAIL", f"no artifact in {timeout_s}s; approvals={approvals}; tail={tail[:120]}")


async def main():
    target = find_main()
    ws_url = target["webSocketDebuggerUrl"]
    print(f"Connected: {target['url']}\n")

    async with websockets.connect(ws_url, max_size=4_000_000) as ws:
        # 用 explicit function-call prompt（中文话术让 LLM 走 tool 直调而非 skill_invoke）
        await run_case(
            ws, "B1 excel-generate (direct tool)",
            "请直接调用 excel_create 函数（不要通过 skill_invoke）生成一个本月开支统计 xlsx，列：日期、餐饮、交通、合计，3 行示例数据。",
            "*.xlsx", 90, 1000,
        )
        await run_case(
            ws, "B2 doc-edit (direct tool)",
            "请直接调用 doc_create 函数（不要通过 skill_invoke）新建一份事假请假条 docx，含标题/称呼/正文/落款。",
            "*.docx", 90, 2000,
        )
        await run_case(
            ws, "B5 ppt-generate (direct tool)",
            "请直接调用 ppt_create 函数（不要通过 skill_invoke）生成 pptx，主题 DeskPet 测试报告，4 页。",
            "*.pptx", 120, 3000,
        )

        OUT.write_text(json.dumps(
            {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "results": RESULTS},
            ensure_ascii=False, indent=2,
        ), encoding="utf-8")
        passed = sum(1 for r in RESULTS if r["status"] == "PASS")
        failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
        print(f"\n=== Summary === PASS:{passed} FAIL:{failed}")


if __name__ == "__main__":
    asyncio.run(main())
