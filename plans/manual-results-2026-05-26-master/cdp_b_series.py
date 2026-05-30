"""B1/B2/B5 真生成测试 — close all dialogs first then send + auto-approve."""
import asyncio
import json
import os
import time
import urllib.request
from pathlib import Path

import websockets

OUT = Path(__file__).parent / "cdp_b_series_results.json"
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


async def close_all_dialogs(ws, mid_base=500):
    """Aggressively close any open dialogs/settings/modals."""
    js = r"""
(() => {
  let closed = 0;
  // ESC 多次（关嵌套）
  for (let i = 0; i < 3; i++) {
    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  }
  // 找所有 close button 点掉
  const closeTexts = ['关闭', '取消', 'Close', 'Cancel', '×', '✕'];
  Array.from(document.querySelectorAll('button')).forEach(b => {
    if (b.offsetParent === null) return;
    const t = (b.innerText || '').trim();
    if (closeTexts.includes(t)) { b.click(); closed++; }
  });
  // 看是否还有 dialog
  const dialogs = document.querySelectorAll('[role="dialog"]');
  return { closed, dialogs_remaining: dialogs.length };
})()
"""
    r = await evaluate(ws, js, mid_base)
    return r.get("result", {}).get("value", {})


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
    r = await evaluate(ws, js, mid)
    return r.get("result", {}).get("value", {})


async def send_chat(ws, text, mid):
    js = f"""
(() => {{
  const el = document.querySelector('[data-testid="chat-input"]');
  if (!el) return {{ ok: false, err: 'no input', visible_dialogs: document.querySelectorAll('[role="dialog"]').length }};
  if (el.offsetParent === null) return {{ ok: false, err: 'input hidden behind dialog' }};
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, {json.dumps(text)});
  el.dispatchEvent(new Event('input', {{ bubbles: true }}));
  const sb = document.querySelector('[data-testid="send-button"]');
  if (sb.disabled) return {{ ok: false, err: 'send disabled', value_set: el.value }};
  sb.click();
  return {{ ok: true, value_sent: el.value }};
}})()
"""
    r = await evaluate(ws, js, mid)
    return r.get("result", {}).get("value", {})


async def wait_artifact(pattern: str, since: float, timeout_s: int = 90):
    candidates = [
        Path("F:/DeskPetData/artifacts"),
        Path("F:/DeskPet/data/artifacts"),
        Path(os.path.expanduser("~/Documents/DeskPet")),
        Path("F:/DeskPetData"),
    ]
    end = time.time() + timeout_s
    while time.time() < end:
        for d in candidates:
            if not d.exists():
                continue
            for p in d.rglob(pattern):
                if p.stat().st_mtime > since:
                    return p
        await asyncio.sleep(2)
    return None


async def run_skill_test(ws, case_name, prompt, file_pattern, timeout_s, mid_start):
    """Send chat + wait for artifact file + auto-approve permission popups."""
    print(f"\n── {case_name} ──")
    # Close any leftover dialogs
    close_res = await close_all_dialogs(ws, mid_start)
    print(f"  close dialogs: {close_res}")
    await asyncio.sleep(0.8)

    send_res = await send_chat(ws, prompt, mid_start + 1)
    print(f"  send: {send_res}")
    if not send_res.get("ok"):
        record(case_name, "FAIL", f"send failed: {send_res}")
        return

    start = time.time()
    end = start + timeout_s
    auto_approved = []
    while time.time() < end:
        await asyncio.sleep(2)
        # 持续 auto-approve permission popups
        appr = await approve_permissions(ws, mid_start + 100 + int(time.time()))
        if appr.get("clicked"):
            auto_approved.append(appr["clicked"])
            print(f"  [auto-approved] {appr['clicked']}")

        # 找 artifact 文件
        f = await wait_artifact(file_pattern, start, timeout_s=2)
        if f:
            record(case_name, "PASS",
                   f"artifact: {f.name} size={f.stat().st_size}B path={f}; approvals={auto_approved}")
            return

        # Look for in-DOM artifact card or done signal
        r = await evaluate(ws, r"""
(() => {
  const txt = document.body.innerText;
  return {
    has_artifact_card: !!document.querySelector('[data-testid*="artifact"]'),
    has_path: /[A-Z]:[\\\/].*\.(xlsx|docx|pptx|pdf|png|jpg)/i.test(txt),
    has_open_btn: txt.includes('打开') && (txt.includes('xlsx') || txt.includes('docx') || txt.includes('pptx')),
    snippet: txt.slice(-300),
  };
})()
""", mid_start + 200 + int(time.time()))
        v = r.get("result", {}).get("value", {})
        if v.get("has_artifact_card") or v.get("has_path"):
            # Don't immediately PASS — try to find actual file
            await asyncio.sleep(2)
            f2 = await wait_artifact(file_pattern, start, timeout_s=4)
            if f2:
                record(case_name, "PASS", f"artifact found after card detected: {f2}")
                return
            record(case_name, "PARTIAL", f"artifact card in DOM but file not found in dirs; snippet={v.get('snippet','')[:120]}")
            return

    # Timeout
    r = await evaluate(ws, "document.body.innerText.slice(-300)", mid_start + 300)
    tail = r.get("result", {}).get("value", "")
    record(case_name, "FAIL", f"no artifact in {timeout_s}s; approvals={auto_approved}; tail={tail[:120]}")


async def main():
    target = find_main()
    ws_url = target["webSocketDebuggerUrl"]
    print(f"Connected: {target['url']}\n")

    async with websockets.connect(ws_url, max_size=4_000_000) as ws:
        await run_skill_test(
            ws, "B1 excel-generate",
            "用 excel_create 工具帮我做一个简单的开支统计 xlsx 文件，含日期、餐饮、交通、合计 4 列，3 行示例数据",
            "*.xlsx", timeout_s=90, mid_start=1000,
        )
        await run_skill_test(
            ws, "B2 doc-edit (新建)",
            "用 doc_create 工具帮我新建一份事假请假条 docx 文件，含标题、称呼、正文、落款",
            "*.docx", timeout_s=90, mid_start=2000,
        )
        await run_skill_test(
            ws, "B5 ppt-generate",
            "用 ppt_create 工具帮我生成 pptx 文件，主题'DeskPet R3-1 测试报告'，4 页足够",
            "*.pptx", timeout_s=120, mid_start=3000,
        )

        OUT.write_text(json.dumps(
            {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "results": RESULTS},
            ensure_ascii=False, indent=2,
        ), encoding="utf-8")
        passed = sum(1 for r in RESULTS if r["status"] == "PASS")
        failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
        partial = sum(1 for r in RESULTS if r["status"] == "PARTIAL")
        print(f"\n=== B-series Summary ===")
        print(f"PASS: {passed}  FAIL: {failed}  PARTIAL: {partial}")


if __name__ == "__main__":
    asyncio.run(main())
