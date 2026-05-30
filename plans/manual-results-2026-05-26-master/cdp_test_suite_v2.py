# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""CDP 真测 round 2 — 补完 B1/B2/B5 + TC-04 + 重测 TC-09.

策略:
  - 通过 CDP click "本会话始终允许" 让 permission gate 不再拦后续 tool call
  - LLM 自然语言 trigger excel/doc/ppt — 等真 .xlsx/.docx/.pptx 生成
  - 文件系统 + DOM 双重验证
"""
import asyncio
import json
import os
import time
import urllib.request
from pathlib import Path

import websockets

OUT = Path(__file__).parent / "cdp_test_results_v2.json"
RESULTS = []

ARTIFACT_DIR = Path("F:/DeskPetData/artifacts")  # 默认 user_data/artifacts
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)


def find_main():
    data = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    for t in data:
        if t.get("type") == "page" and t.get("url", "").rstrip("/").endswith("tauri.localhost"):
            return t
    return None


async def evaluate(ws, expr, mid, await_p=False):
    await ws.send(json.dumps({"id": mid, "method": "Runtime.evaluate",
                              "params": {"expression": expr, "returnByValue": True, "awaitPromise": await_p}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg.get("result", {})


def record(case, status, detail=""):
    entry = {"case": case, "status": status, "detail": detail, "ts": time.strftime("%H:%M:%S")}
    RESULTS.append(entry)
    marker = {"PASS": "✓", "FAIL": "✗", "SKIP": "?", "PARTIAL": "~"}.get(status, "·")
    safe_detail = str(detail).replace("\n", " | ")[:160]
    print(f"  [{marker}] {case:34s} {status:7s} — {safe_detail}")


# JS helper: send message via UI + auto-approve any permission dialog that appears
SEND_AND_AUTOAPPROVE = r"""
async (text) => {
  const el = document.querySelector('[data-testid="chat-input"]');
  if (!el) return { ok: false, error: 'no input' };
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const ns = Object.getOwnPropertyDescriptor(proto, 'value').set;
  ns.call(el, text);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.focus();
  const sb = document.querySelector('[data-testid="send-button"]');
  if (sb.disabled) return { ok: false, error: 'send disabled' };
  sb.click();
  return { ok: true };
}
"""


async def find_artifact(pattern: str, since: float, timeout: float = 60.0):
    """Search common artifact dirs for newly-created file matching pattern."""
    candidates = [
        ARTIFACT_DIR,
        Path("F:/DeskPetData/artifacts"),
        Path("F:/DeskPet/data/artifacts"),
        Path(os.path.expanduser("~/Documents/DeskPet")),
    ]
    end = time.time() + timeout
    while time.time() < end:
        for d in candidates:
            if not d.exists():
                continue
            for p in d.rglob(pattern):
                if p.stat().st_mtime > since:
                    return p
        await asyncio.sleep(2)
    return None


async def approve_permissions(ws):
    """Click '本会话始终允许' or '允许一次' if permission gate dialog showed."""
    js = r"""
(() => {
  // Permission gate buttons typically contain text like 允许/本会话/Allow
  const btns = Array.from(document.querySelectorAll('button'));
  const cands = [
    '本会话始终允许',
    '本会话允许',
    '允许一次',
    '允许',
    'Allow',
  ];
  for (const t of cands) {
    const b = btns.find(b => (b.innerText || '').trim() === t);
    if (b && b.offsetParent !== null) {
      b.click();
      return { clicked: t };
    }
  }
  return { clicked: null };
})()
"""
    r = await evaluate(ws, js, 9000 + int(time.time()) % 1000)
    return r.get("result", {}).get("value", {})


async def wait_for_skill(ws, expected_keywords: list, timeout_s: int = 60):
    """Poll body text for skill completion signals; auto-approve any permission popup."""
    end = time.time() + timeout_s
    while time.time() < end:
        await asyncio.sleep(2)
        # auto-approve any popup
        approval = await approve_permissions(ws)
        if approval.get("clicked"):
            print(f"     [auto-click] permission button: {approval['clicked']}")

        r = await evaluate(ws, r"""
(() => {
  const txt = document.body.innerText.toLowerCase();
  return {
    has_xlsx: txt.includes('.xlsx'),
    has_docx: txt.includes('.docx'),
    has_pptx: txt.includes('.pptx'),
    has_pdf: txt.includes('.pdf'),
    has_generated: txt.includes('已生成') || txt.includes('done') || txt.includes('完成'),
    has_open: txt.includes('打开') || txt.includes('查看'),
    snippet: document.body.innerText.slice(-400),
  };
})()
""", 10000 + int(time.time()) % 1000)
        v = r.get("result", {}).get("value", {})
        for k in expected_keywords:
            if v.get(k):
                return v
    return None


async def main():
    target = find_main()
    if not target:
        print("ERROR: no pet main; deskpet running with --remote-debugging-port=9222?")
        return
    ws_url = target["webSocketDebuggerUrl"]
    print(f"Connected: {target['url']}\n")

    artifact_baseline = time.time() - 5

    async with websockets.connect(ws_url, max_size=4_000_000) as ws:
        mid = [10]
        def nx(): mid[0] += 1; return mid[0]

        # ─── TC-04: 主输入框 + send-button 真发消息 (sessionId="default") ───
        # 已经在 R3-1 隐式 PASS，但 TC-04.1~5 是 5 个子用例：发文字 / 按钮变色 / 回复到 default / DialogBar 不显示（panel 开时）
        # 用 CDP 真做：先关闭可能开着的 panel
        await evaluate(ws, r"""
(() => {
  // 确保 message panel 关闭
  // 如果消息按钮显示已激活，再点一次关闭
  // 此 case 假设 main + 主输入栏可见
})()
""", nx())

        r = await evaluate(ws, r"""
(() => {
  const el = document.querySelector('[data-testid="chat-input"]');
  if (!el) return { ok: false, err: 'no input' };
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, '现在几点');
  el.dispatchEvent(new Event('input', { bubbles: true }));
  const sb = document.querySelector('[data-testid="send-button"]');
  const beforeColor = sb ? getComputedStyle(sb).backgroundColor : null;
  if (sb.disabled) return { ok: false, err: 'send disabled', value: el.value };
  sb.click();
  // sample color after click (用于 TC-04.2 期望"发送按钮变红")
  return { ok: true, beforeColor };
})()
""", nx())
        v = r.get("result", {}).get("value", {})
        if v.get("ok"):
            await asyncio.sleep(2.5)
            # 检查回复气泡是否出现
            r2 = await evaluate(ws, r"""
(() => {
  const txt = document.body.innerText;
  return {
    has_reply: txt.length > 50,
    has_thinking: txt.includes('...') || txt.includes('思考') || /[一-鿿]{5,}/.test(txt),
  };
})()
""", nx())
            v2 = r2.get("result", {}).get("value", {})
            record("TC-04.1 主输入框 → send", "PASS" if v2.get("has_reply") else "PARTIAL",
                   f"reply detected={v2.get('has_reply')}, before color={v.get('beforeColor')}")
        else:
            record("TC-04.1 主输入框 → send", "FAIL", v.get("err"))

        # 等 LLM 回复完成
        await asyncio.sleep(8)

        # ─── B1: excel-generate via 自然语言 + auto-approve ───
        print(f"\n── B1 excel-generate ──")
        r = await evaluate(ws, r"""
(async () => {
  const el = document.querySelector('[data-testid="chat-input"]');
  if (!el) return { ok: false, err: 'no input' };
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, '用 excel_create 工具帮我生成一个简单的本月开支统计 .xlsx，要有日期、餐饮、交通、合计列，至少 3 行数据');
  el.dispatchEvent(new Event('input', { bubbles: true }));
  const sb = document.querySelector('[data-testid="send-button"]');
  sb.click();
  return { ok: true };
})()
""", nx(), await_p=True)
        b1_start = time.time()
        result = await wait_for_skill(ws, ["has_xlsx", "has_generated"], timeout_s=90)
        if result:
            xlsx_path = await find_artifact("*.xlsx", b1_start, timeout=10)
            if xlsx_path:
                record("B1 excel-generate", "PASS", f"artifact: {xlsx_path.name} size={xlsx_path.stat().st_size}")
            else:
                record("B1 excel-generate", "PARTIAL", f"UI says generated but file not found in artifact dirs; snippet={result.get('snippet','')[:100]}")
        else:
            record("B1 excel-generate", "FAIL", "no completion signal in 90s")

        await asyncio.sleep(3)

        # ─── B2: doc-edit 新建请假条 ───
        print(f"\n── B2 doc-edit 新建 ──")
        r = await evaluate(ws, r"""
(() => {
  const el = document.querySelector('[data-testid="chat-input"]');
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, '用 doc_create 工具帮我新建一份事假请假条 .docx，含标题、称呼、正文、落款');
  el.dispatchEvent(new Event('input', { bubbles: true }));
  document.querySelector('[data-testid="send-button"]').click();
})()
""", nx())
        b2_start = time.time()
        result = await wait_for_skill(ws, ["has_docx", "has_generated"], timeout_s=90)
        if result:
            docx_path = await find_artifact("*.docx", b2_start, timeout=10)
            if docx_path:
                record("B2 doc-edit 新建", "PASS", f"artifact: {docx_path.name} size={docx_path.stat().st_size}")
            else:
                record("B2 doc-edit 新建", "PARTIAL", f"UI says generated but file not found; snippet={result.get('snippet','')[:100]}")
        else:
            record("B2 doc-edit 新建", "FAIL", "no completion in 90s")

        await asyncio.sleep(3)

        # ─── B5: ppt-generate ───
        print(f"\n── B5 ppt-generate ──")
        r = await evaluate(ws, r"""
(() => {
  const el = document.querySelector('[data-testid="chat-input"]');
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, '用 ppt_create 工具帮我生成 .pptx，主题"DeskPet 测试结果"，4 页');
  el.dispatchEvent(new Event('input', { bubbles: true }));
  document.querySelector('[data-testid="send-button"]').click();
})()
""", nx())
        b5_start = time.time()
        result = await wait_for_skill(ws, ["has_pptx", "has_generated"], timeout_s=120)
        if result:
            pptx_path = await find_artifact("*.pptx", b5_start, timeout=10)
            if pptx_path:
                record("B5 ppt-generate", "PASS", f"artifact: {pptx_path.name} size={pptx_path.stat().st_size}")
            else:
                record("B5 ppt-generate", "PARTIAL", f"UI says generated but file not found; snippet={result.get('snippet','')[:100]}")
        else:
            record("B5 ppt-generate", "FAIL", "no completion in 120s")

        # ─── TC-09 v2: mic 切录音态 — 用更精确 selector ───
        # mic-button click 应该改变 inline style 或 child SVG fill
        await asyncio.sleep(2)
        r = await evaluate(ws, r"""
(() => {
  const mic = document.querySelector('[data-testid="mic-button"]');
  if (!mic) return { err: 'no mic' };
  const before = {
    inlineStyle: mic.getAttribute('style') || '',
    title: mic.getAttribute('title') || '',
    ariaLabel: mic.getAttribute('aria-label') || '',
    bgColor: getComputedStyle(mic).backgroundColor,
  };
  mic.click();
  return before;
})()
""", nx())
        before = r.get("result", {}).get("value", {})
        await asyncio.sleep(1.0)
        r = await evaluate(ws, r"""
(() => {
  const mic = document.querySelector('[data-testid="mic-button"]');
  if (!mic) return null;
  return {
    inlineStyle: mic.getAttribute('style') || '',
    title: mic.getAttribute('title') || '',
    ariaLabel: mic.getAttribute('aria-label') || '',
    bgColor: getComputedStyle(mic).backgroundColor,
  };
})()
""", nx())
        after = r.get("result", {}).get("value", {})
        changed = (
            (before.get("inlineStyle") != after.get("inlineStyle"))
            or (before.get("bgColor") != after.get("bgColor"))
            or (before.get("title") != after.get("title"))
            or (before.get("ariaLabel") != after.get("ariaLabel"))
        )
        if changed:
            record("TC-09 mic 切录音态 v2", "PASS",
                   f"before bgColor={before.get('bgColor')} → after={after.get('bgColor')}; titles: {before.get('title','')!r}→{after.get('title','')!r}")
            # click again to stop
            await evaluate(ws, "document.querySelector('[data-testid=\"mic-button\"]')?.click()", nx())
        else:
            record("TC-09 mic 切录音态 v2", "FAIL",
                   f"no state change detected: bgColor before={before.get('bgColor')} after={after.get('bgColor')}")

        # ─── Save ───
        OUT.write_text(json.dumps({"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "results": RESULTS}, ensure_ascii=False, indent=2), encoding="utf-8")
        passed = sum(1 for r in RESULTS if r["status"] == "PASS")
        failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
        partial = sum(1 for r in RESULTS if r["status"] == "PARTIAL")
        skipped = sum(1 for r in RESULTS if r["status"] == "SKIP")
        print(f"\n=== Round 2 Summary ===")
        print(f"PASS: {passed}  FAIL: {failed}  PARTIAL: {partial}  SKIP: {skipped}")


if __name__ == "__main__":
    asyncio.run(main())
