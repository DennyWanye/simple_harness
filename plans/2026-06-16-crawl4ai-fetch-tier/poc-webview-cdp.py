# -*- coding: utf-8 -*-
"""WI-(-1) WebView POC (CDP 版): 用系统 Edge(=WebView2 引擎)无头渲染 JS 站,
经 CDP 取渲染后 DOM,对比原始 httpx HTML,验证"复用系统引擎渲染 JS、零下载"可行性。"""
import asyncio, json, subprocess, sys, time, tempfile, socket
import httpx, websockets
sys.path.insert(0, "G:/projects/deskpet/backend")

def w(s): sys.stdout.buffer.write((str(s)+"\n").encode("utf-8", "replace"))

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
URLS = [
    "https://quotes.toscrape.com/js/",   # 金标准: JS 渲染才有 quotes
    "https://quotes.toscrape.com/",       # 对照: 静态
    "https://book.douban.com/latest",     # 真实站(豆瓣,部分 JS)
]

def traf(html):
    try:
        import trafilatura
        return (trafilatura.extract(html, include_comments=False) or "").strip()
    except Exception as e:
        return ""

def raw_fetch(url):
    try:
        with httpx.Client(timeout=15, trust_env=False, follow_redirects=True,
                          headers={"User-Agent": _UA}) as c:
            return c.get(url).text
    except Exception:
        return ""

def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

async def edge_render(url, port):
    """启动 Edge 无头(调试端口) → CDP Runtime.evaluate 取渲染后 outerHTML + innerText。"""
    ud = tempfile.mkdtemp(prefix="cdp_")
    proc = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--no-sandbox",
         f"--remote-debugging-port={port}", f"--user-data-dir={ud}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    try:
        # 等调试端口 + 拿 page target 的 ws
        ws_url = None
        for _ in range(40):
            try:
                with httpx.Client(timeout=2) as c:
                    tabs = c.get(f"http://127.0.0.1:{port}/json").json()
                pages = [t for t in tabs if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]; break
            except Exception:
                pass
            await asyncio.sleep(0.25)
        if not ws_url:
            return "", "", time.time()-t0
        async with websockets.connect(ws_url, max_size=20_000_000) as ws:
            mid = [0]
            async def call(method, params=None):
                mid[0] += 1; i = mid[0]
                await ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == i:
                        return msg.get("result", {})
            await call("Runtime.enable")
            await call("Page.enable")
            await call("Page.navigate", {"url": url})   # 显式导航,不靠 launch arg
            # 1) 等 readyState=complete(最多 10s)
            for _ in range(40):
                r = await call("Runtime.evaluate", {
                    "expression": "document.readyState", "returnByValue": True})
                if r.get("result", {}).get("value") == "complete":
                    break
                await asyncio.sleep(0.25)
            # 2) load 后再给异步 JS(fetch/构建 DOM)固定 4s,然后等文字稳定
            await asyncio.sleep(4.0)
            last = -1
            for _ in range(12):
                r = await call("Runtime.evaluate", {
                    "expression": "document.body ? document.body.innerText.length : 0",
                    "returnByValue": True})
                n = r.get("result", {}).get("value", 0)
                if n == last:
                    break
                last = n; await asyncio.sleep(0.5)
            html = (await call("Runtime.evaluate", {
                "expression": "document.documentElement.outerHTML",
                "returnByValue": True})).get("result", {}).get("value", "")
            txt = (await call("Runtime.evaluate", {
                "expression": "document.body.innerText",
                "returnByValue": True})).get("result", {}).get("value", "")
            return html, txt, time.time()-t0
    finally:
        proc.terminate()

async def main():
    for url in URLS:
        w(f"\n===== {url} =====")
        raw = raw_fetch(url); raw_txt = traf(raw)
        w(f"  [原始 httpx]  HTML {len(raw)}B → trafilatura 正文 {len(raw_txt)} 字")
        html, innertext, dt = await edge_render(url, _free_port())
        ren_txt = traf(html)
        w(f"  [Edge 渲染]   DOM {len(html)}B → trafilatura 正文 {len(ren_txt)} 字 | body.innerText {len(innertext)} 字  ({dt:.1f}s)")
        gain = len(ren_txt) - len(raw_txt)
        w(f"  判定: {'✅ 渲染救回正文' if gain > 200 else '≈ 差不多'} (trafilatura 正文增量 {gain:+d})")
        w(f"  渲染 innerText 前100字: {innertext[:100].replace(chr(10),' ')}")

asyncio.run(main())
