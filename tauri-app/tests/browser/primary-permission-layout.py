"""Actual CSS/Workbench regression; synthetic transport, never native/provider evidence.

Run with a Python environment containing Playwright and installed WebKit:
  python tauri-app/tests/browser/primary-permission-layout.py --output <ignored-dir>
"""
import argparse
import json
from pathlib import Path
import subprocess
import threading
from queue import Queue
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


def visible_actions(page, output, name):
    print(f"CHECK {name}", flush=True)
    dialog = page.get_by_role("dialog", name="写入文件")
    expect(dialog).to_have_count(1)
    # Wait for the real CSS entrance animation rather than counting AX/DOM nodes.
    dialog.evaluate("el => Promise.all(el.getAnimations({subtree:true}).map(a => a.finished.catch(() => {})))")
    page.screenshot(path=str(output / f"{name}.png"))
    for label in ("允许一次", "拒绝 Esc", "停止当前任务"):
        button = dialog.get_by_role("button", name=label, exact=True)
        expect(button).to_be_visible()
        geometry = button.evaluate("""el => {
          const r = el.getBoundingClientRect();
          const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
          return {rect: r.toJSON(), width: innerWidth, height: innerHeight,
            hit: !!hit && (hit === el || el.contains(hit))};
        }""")
        rect = geometry["rect"]
        assert rect["width"] > 0 and rect["height"] > 0, (name, label, geometry)
        assert 0 <= rect["x"] and rect["right"] <= geometry["width"], (name, label, geometry)
        assert 0 <= rect["y"] and rect["bottom"] <= geometry["height"], (name, label, geometry)
        assert geometry["hit"], (name, label, geometry)
        # Real browser hit testing/visibility checks; never force or DOM-click.
        button.click(trial=True, timeout=3000)


def exercise(browser, url, output, width, height):
    page = browser.new_page(viewport={"width": width, "height": height})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    # Only the fixture server can receive requests. No live Host websocket/HTTP.
    origin = urlparse(url).netloc
    page.route("**/*", lambda route: route.continue_() if urlparse(route.request.url).netloc == origin else route.abort())
    # An intercepted socket is never connected to a server unless explicitly forwarded.
    page.route_web_socket("**/*", lambda _ws: None)
    try:
        print("OPEN " + str((width, height)), flush=True)
        page.goto(url)
        expect(page.get_by_role("dialog")).to_have_count(1, timeout=30000)
        prefix = f"{width}x{height}"
        visible_actions(page, output, prefix + "-initial")
        page.get_by_text("查看详细参数", exact=True).click()
        visible_actions(page, output, prefix + "-expanded")
        page.get_by_label("主对话历史").evaluate("el => { el.scrollTop = el.scrollHeight; }")
        visible_actions(page, output, prefix + "-history-scrolled")
        page.get_by_role("dialog").evaluate("el => { el.parentElement.scrollTop = el.parentElement.scrollHeight; }")
        visible_actions(page, output, prefix + "-run-container-scrolled")
        # Containing-block stress control, not a claim that native has this transform.
        run_container = page.get_by_text("等待授权", exact=True).locator("..")
        run_container.evaluate("el => { el.style.transform = 'translateZ(0)'; }")
        visible_actions(page, output, prefix + "-composited-ancestor")
        run_container.evaluate("el => { el.style.transform = ''; }")
        page.get_by_text("查看详细参数", exact=True).click()
        page.evaluate("layoutFixture.refresh()")
        visible_actions(page, output, prefix + "-refreshed")
        page.evaluate("layoutFixture.remount()")
        visible_actions(page, output, prefix + "-remounted")
        page.evaluate("layoutFixture.disconnect()")
        expect(page.get_by_role("dialog")).to_have_count(0)
        page.evaluate("layoutFixture.reconnect()")
        visible_actions(page, output, prefix + "-reconnected")
        # A portalled dialog must not outlive the visible chat view or handle Esc there.
        page.evaluate("layoutFixture.hide()")
        expect(page.get_by_role("dialog")).to_have_count(0)
        page.keyboard.press("Escape")
        assert page.evaluate("layoutFixture.stats().replies.length") == 0, "Hidden authorization handled Escape"
        page.evaluate("layoutFixture.show()")
        visible_actions(page, output, prefix + "-shown-again")
        page.get_by_role("button", name="允许一次", exact=True).click()
        expect(page.get_by_text("操作 2：task_scope_update", exact=True)).to_be_visible()
        visible_actions(page, output, prefix + "-next-decision")
        assert page.evaluate("layoutFixture.stats().replies") == [{"decision_id": "layout-decision-1", "decision": "allow"}]
        page.get_by_text("查看详细参数", exact=True).click()
        visible_actions(page, output, prefix + "-next-expanded")
        page.get_by_role("button", name="拒绝 Esc", exact=True).click()
        assert page.evaluate("layoutFixture.stats().replies") == [
            {"decision_id": "layout-decision-1", "decision": "allow"},
            {"decision_id": "layout-decision-2", "decision": "deny"},
        ]
        assert not errors, errors
    except Exception:
        page.screenshot(path=str(output / f"{width}x{height}-failure.png"))
        # Only geometry/styles; no wire request, nonce, or real user content.
        diagnostic = page.locator('[role="dialog"]').evaluate_all("""els => els.map(el => {
          const ancestors = []; for (let p = el; p; p = p.parentElement) {
            const s = getComputedStyle(p); ancestors.push({tag:p.tagName, rect:p.getBoundingClientRect().toJSON(),
              overflow:s.overflow, transform:s.transform, filter:s.filter, backdrop:s.backdropFilter, display:s.display});
          } return ancestors;
        })""")
        (output / f"{width}x{height}-geometry.json").write_text(json.dumps({"ancestors": diagnostic, "errors": errors}, indent=2))
        raise
    finally:
        page.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engines", nargs="+", choices=["webkit", "chromium"], default=["webkit"])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    ready = Queue()
    with (args.output / "fixture-server.log").open("w") as log:
        process = subprocess.Popen(["node", str(Path(__file__).with_name("primary-permission-server.mjs"))], stdout=subprocess.PIPE, stderr=log, text=True)
        def read_ready():
            for line in process.stdout:
                if line.startswith("LAYOUT_URL="):
                    ready.put(line.strip().split("=", 1)[1])
        threading.Thread(target=read_ready, daemon=True).start()
        failures = []
        try:
            url = ready.get(timeout=30)
            with sync_playwright() as playwright:
                for engine in args.engines:
                    output = args.output / engine
                    output.mkdir(exist_ok=True)
                    print("ENGINE " + engine, flush=True)
                    browser = getattr(playwright, engine).launch()
                    try:
                        for width, height in [(1000, 700), (800, 560)]:
                            try:
                                exercise(browser, url, output, width, height)
                                print(f"PASS {engine} {width}x{height}", flush=True)
                            except Exception as error:
                                failures.append(f"{engine} {width}x{height}: {error}")
                                print(f"FAIL {failures[-1]}", flush=True)
                    finally:
                        browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if failures:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
