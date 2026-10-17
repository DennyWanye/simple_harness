"""代码质量整治阶段 9（9I）：WebKit + 产物构建上逐元素比对计算样式（裁决条件 1）。

子命令（在仓库根目录运行，用装了 Playwright 与 WebKit 的 Python）：
  build   --ref <提交|WORKTREE> --out <目录>   构建样式画廊产物（提交用 git archive 导出副本，
                                               画廊文件 tests/browser/style-gallery* 用工作区版本覆盖）
  dump    --dist <目录> --out <json> [--scenes a,b] [--no-hover]
                                               预览服务器提供产物，WebKit 打开每个场景，深浅两套主题，
                                               取全部元素（含有内容的 ::before/::after）的 getComputedStyle，
                                               再逐个悬停可交互元素取其计算样式
  compare <前.json> <后.json>                   逐元素比对，全等才通过
  smoke   --dist <目录>                         每个场景能渲染、无报错，打印元素数

<style> 元素不计入逐元素比对（不渲染）；后一版运行时 <style> 只许 sh-theme。
动画在取样前暂停并归零；时间与随机数在画廊里固定（style-gallery/freezeTime.ts）。
只允许画廊自己的源（同一预览服务器）；其他请求一律中止，WebSocket 不连。
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import io
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "tauri-app"
THEMES = ("dark", "light")
VIEWPORT = {"width": 1280, "height": 860}
HOVER_LIMIT = 250
# 前端直接引入的 SDK 契约 JSON（tauri-app 之外），导出副本时一并带上。
SDK_ASSETS = ["sdk/simple-harness-sdk/src/agent_orchestrator/assurance/contracts", "sdk/simple-harness-sdk/src/agent_orchestrator/graph/schemas", "sdk/simple-harness-sdk/tests/orchestrator/acceptance_assets"]

COLLECT_JS = r"""
() => {
  for (const a of document.getAnimations()) { try { a.pause(); a.currentTime = 0; } catch (e) {} }
  const styles = [], index = new Map();
  const intern = (s) => { let i = index.get(s); if (i === undefined) { i = styles.length; styles.push(s); index.set(s, i); } return i; };
  const ser = (cs) => { const p = []; for (let i = 0; i < cs.length; i++) p.push(cs[i] + ':' + cs.getPropertyValue(cs[i])); return p.join(';'); };
  const elements = [];
  const all = [document.documentElement, ...document.documentElement.querySelectorAll('*')].filter((e) => e.tagName !== 'STYLE');
  all.forEach((el, i) => {
    const rec = { i, tag: el.tagName.toLowerCase(), s: intern(ser(getComputedStyle(el))) };
    for (const pseudo of ['::before', '::after']) {
      const cs = getComputedStyle(el, pseudo);
      const c = cs.getPropertyValue('content');
      if (c && c !== 'none' && c !== 'normal') rec[pseudo] = intern(ser(cs));
    }
    elements.push(rec);
  });
  const styleTags = [...document.querySelectorAll('style')].map((s) => s.id || '(无 id)');
  return { styles, elements, styleTags };
}
"""

HOVER_TARGETS_JS = r"""
(limit) => {
  const all = [document.documentElement, ...document.documentElement.querySelectorAll('*')].filter((e) => e.tagName !== 'STYLE');
  const sel = 'button, a, [role=button], [role=tab], [role=option], .sh-interactive, input, select, textarea, summary, label';
  const out = [];
  all.forEach((el, i) => { if (out.length < limit && el.matches(sel)) out.push(i); });
  return out;
}
"""

HOVER_ONE_JS = r"""
(i) => {
  const all = [document.documentElement, ...document.documentElement.querySelectorAll('*')].filter((e) => e.tagName !== 'STYLE');
  const el = all[i];
  if (!el) return null;
  el.scrollIntoView({ block: 'center', inline: 'center' });
  const r = el.getBoundingClientRect();
  if (r.width === 0 || r.height === 0) return { skip: true };
  return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
}
"""

HOVER_READ_JS = r"""
(i) => {
  for (const a of document.getAnimations()) { try { a.pause(); a.currentTime = 0; } catch (e) {} }
  const all = [document.documentElement, ...document.documentElement.querySelectorAll('*')].filter((e) => e.tagName !== 'STYLE');
  const el = all[i];
  const cs = getComputedStyle(el);
  const p = []; for (let k = 0; k < cs.length; k++) p.push(cs[k] + ':' + cs.getPropertyValue(cs[k]));
  return { tag: el.tagName.toLowerCase(), s: p.join(';') };
}
"""


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def build(ref, out):
    out = Path(out).resolve()
    if ref == "WORKTREE":
        app_dir = APP
        run(["node", str(APP / "tests/browser/style-gallery-build.mjs"), str(app_dir), str(out)])
        return
    tmp = Path(tempfile.mkdtemp(prefix="gallery-src-", dir=os.environ.get("TMPDIR")))
    data = subprocess.run(["git", "archive", ref, "tauri-app", *SDK_ASSETS], cwd=ROOT, check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(tmp, filter="data")
    (tmp / "tauri-app/node_modules").symlink_to(APP / "node_modules")
    # 画廊本身（夹具、场景、构建与预览脚本）一律用工作区当前版本覆盖进去：前后两边挂载同一套场景，
    # 差别只来自被比对的源码。
    gallery = APP / "tests/browser"
    for item in gallery.iterdir():
        if item.name.startswith("style-gallery"):
            dest = tmp / "tauri-app/tests/browser" / item.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["rm", "-rf", str(dest)], check=True)
            subprocess.run(["cp", "-R", str(item), str(dest)], check=True)
    try:
        run(["node", str(APP / "tests/browser/style-gallery-build.mjs"), str(tmp / "tauri-app"), str(out)])
    finally:
        subprocess.run(["rm", "-rf", str(tmp)], check=False)


class Server:
    def __init__(self, dist):
        self.proc = subprocess.Popen(["node", str(APP / "tests/browser/style-gallery-serve.mjs"), str(Path(dist).resolve())],
                                     cwd=APP, stdout=subprocess.PIPE, text=True)
        for line in self.proc.stdout:
            if line.startswith("GALLERY_URL="):
                self.url = line.strip().split("=", 1)[1]
                break
        else:
            raise RuntimeError("预览服务器没有启动")

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def open_page(browser, url):
    page = browser.new_page(viewport=VIEWPORT)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    origin = urlparse(url).netloc
    page.route("**/*", lambda route: route.continue_() if urlparse(route.request.url).netloc == origin else route.abort())
    page.route_web_socket("**/*", lambda _ws: None)
    return page, errors


def settle(page):
    page.wait_for_function("window.__gallery && window.__gallery.ready", timeout=20000)
    # DOM 连续 400ms 不变才算稳定（懒加载组件、微任务回包）
    page.evaluate("""() => new Promise((resolve) => {
      let t = setTimeout(done, 400); const obs = new MutationObserver(() => { clearTimeout(t); t = setTimeout(done, 400); });
      function done() { obs.disconnect(); resolve(); }
      obs.observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
      setTimeout(() => { obs.disconnect(); resolve(); }, 8000);
    })""")


def scene_names(browser, url):
    page, _ = open_page(browser, url)
    page.goto(url)
    page.wait_for_function("window.__gallery && window.__gallery.ready", timeout=20000)
    names = page.evaluate("window.__gallery.scenes")
    page.close()
    return names


def dump(dist, out, scenes=None, hover=True):
    from playwright.sync_api import sync_playwright
    server = Server(dist)
    result = {"scenes": {}}
    try:
        with sync_playwright() as p:
            browser = p.webkit.launch()
            result["engine"] = f"webkit {browser.version}"
            names = scenes or scene_names(browser, server.url)
            for name in names:
                for theme in THEMES:
                    page, errors = open_page(browser, server.url)
                    page.goto(f"{server.url}?scene={name}&theme={theme}")
                    settle(page)
                    data = page.evaluate(COLLECT_JS)
                    data["errors"] = errors + page.evaluate("window.__gallery.errors")
                    if hover:
                        hovers = []
                        for i in page.evaluate(HOVER_TARGETS_JS, HOVER_LIMIT):
                            pos = page.evaluate(HOVER_ONE_JS, i)
                            if not pos or pos.get("skip"):
                                continue
                            page.mouse.move(pos["x"], pos["y"])
                            hovers.append([i, page.evaluate(HOVER_READ_JS, i)])
                        page.mouse.move(0, 0)
                        data["hover"] = hovers
                    result["scenes"][f"{name}|{theme}"] = data
                    print(f"  {name}|{theme}: {len(data['elements'])} 个元素，{len(data.get('hover', []))} 次悬停，报错 {len(data['errors'])}", flush=True)
                    page.close()
            browser.close()
    finally:
        server.close()
    Path(out).write_text(json.dumps(result, ensure_ascii=False))


def parse(s):
    out = {}
    for part in s.split(";"):
        if part:
            k, _, v = part.partition(":")
            out[k] = v
    return out


def diff_styles(a, b):
    pa, pb = parse(a), parse(b)
    keys = sorted(set(pa) | set(pb))
    return [(k, pa.get(k), pb.get(k)) for k in keys if pa.get(k) != pb.get(k)]


def compare(before, after, limit=40):
    A = json.loads(Path(before).read_text())
    B = json.loads(Path(after).read_text())
    problems, notes = [], []
    for key in sorted(set(A["scenes"]) | set(B["scenes"])):
        a, b = A["scenes"].get(key), B["scenes"].get(key)
        if not a or not b:
            problems.append(f"{key}: 场景只在一边")
            continue
        for side, d in (("前", a), ("后", b)):
            if d["errors"]:
                problems.append(f"{key}: {side}有报错 {d['errors'][:3]}")
        if b["styleTags"] != ["sh-theme"]:
            problems.append(f"{key}: 后一版运行时 <style> 不只 sh-theme：{b['styleTags']}")
        if a["styleTags"] != b["styleTags"]:
            notes.append(f"{key}: 运行时 <style> 前{a['styleTags']} 后{b['styleTags']}")
        if len(a["elements"]) != len(b["elements"]):
            problems.append(f"{key}: 元素数不同 前 {len(a['elements'])} 后 {len(b['elements'])}")
            continue
        for ea, eb in zip(a["elements"], b["elements"]):
            if ea["tag"] != eb["tag"]:
                problems.append(f"{key}: 第 {ea['i']} 个元素标签不同 {ea['tag']} / {eb['tag']}")
                break
            for slot in ("s", "::before", "::after"):
                sa = a["styles"][ea[slot]] if slot in ea else None
                sb = b["styles"][eb[slot]] if slot in eb else None
                if sa != sb:
                    d = diff_styles(sa or "", sb or "")
                    problems.append(f"{key}: 第 {ea['i']} 个元素 <{ea['tag']}> {slot if slot != 's' else ''} 计算样式不同 {d[:6]}")
        ha, hb = a.get("hover", []), b.get("hover", [])
        if [i for i, _ in ha] != [i for i, _ in hb]:
            problems.append(f"{key}: 悬停目标不同")
        else:
            for (i, x), (_, y) in zip(ha, hb):
                if x != y:
                    problems.append(f"{key}: 悬停第 {i} 个元素 <{x['tag']}> 计算样式不同 {diff_styles(x['s'], y['s'])[:6]}")
    total = sum(len(d["elements"]) for d in A["scenes"].values())
    hovers = sum(len(d.get("hover", [])) for d in A["scenes"].values())
    print(f"引擎：前 {A.get('engine')}，后 {B.get('engine')}；场景×主题 {len(A['scenes'])}；元素 {total}；悬停 {hovers}")
    if notes:
        print(f"说明（不算差异）：{len(notes)} 个场景×主题的运行时 <style> 前后不同，例如 {notes[0]}")
    for p in problems[:limit]:
        print("  " + p)
    if len(problems) > limit:
        print(f"  ……另有 {len(problems) - limit} 条")
    print("计算样式比对：" + ("不通过" if problems else "通过"))
    return 1 if problems else 0


def smoke(dist):
    from playwright.sync_api import sync_playwright
    server = Server(dist)
    bad = 0
    try:
        with sync_playwright() as p:
            browser = p.webkit.launch()
            for name in scene_names(browser, server.url):
                page, errors = open_page(browser, server.url)
                page.goto(f"{server.url}?scene={name}&theme=dark")
                settle(page)
                errs = errors + page.evaluate("window.__gallery.errors")
                n = page.evaluate("document.querySelectorAll('*').length")
                print(f"  {name}: {n} 个元素" + (f"，报错 {errs[:3]}" if errs else ""))
                bad += bool(errs)
                page.close()
            browser.close()
    finally:
        server.close()
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--ref", required=True); b.add_argument("--out", required=True)
    d = sub.add_parser("dump"); d.add_argument("--dist", required=True); d.add_argument("--out", required=True)
    d.add_argument("--scenes"); d.add_argument("--no-hover", action="store_true")
    c = sub.add_parser("compare"); c.add_argument("before"); c.add_argument("after")
    s = sub.add_parser("smoke"); s.add_argument("--dist", required=True)
    a = ap.parse_args()
    if a.cmd == "build":
        build(a.ref, a.out)
    elif a.cmd == "dump":
        dump(a.dist, a.out, a.scenes.split(",") if a.scenes else None, not a.no_hover)
    elif a.cmd == "compare":
        sys.exit(compare(a.before, a.after))
    elif a.cmd == "smoke":
        sys.exit(smoke(a.dist))


if __name__ == "__main__":
    main()
