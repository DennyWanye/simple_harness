"""B-2 惊艳整页生图 PPT — 真调 gpt-image-2。

Phase A: 逐张生图(1536x1024 横版),每张完成立即写 manifest(断点续跑,不重复扣费)。
Phase B: 图全齐 → 裁 16:9 → outline 带 image_path → ppt_create(零生图) → A-5 自动渲染预览。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, r"G:\projects\deskpet\backend")
import os
os.environ.setdefault("DESKPET_USER_DATA_DIR", r"G:\projects\deskpet\.tmp\fp345-userdata")

MANIFEST = Path(r"G:\projects\deskpet\.tmp\hero-ppt-manifest.json")
OUT_PPTX = r"G:\projects\deskpet\.tmp\hero-ai-deck.pptx"

STYLE = (
    " Cinematic lighting, deep navy and violet palette with warm amber accents,"
    " ultra-detailed modern digital illustration, dark elegant futuristic style,"
    " main subject in upper two thirds, bottom quarter dark simple and clean for"
    " text overlay, no text, no letters, no typography, no watermark."
)

PAGES = [
    {
        "key": "cover",
        "prompt": "Vast cosmic neural network: glowing synapses forming a brain-shaped constellation above a deep-space horizon, sense of awe and scale." + STYLE,
        "title": "智核纪元",
        "caption": "大语言模型驱动的下一代生产力",
    },
    {
        "key": "history",
        "prompt": "An ancient grand library where books dissolve into streams of glowing data particles flowing upward into a luminous digital sky." + STYLE,
        "title": "从符号到智能",
        "caption": "六十年人工智能演进之路",
    },
    {
        "key": "abilities",
        "prompt": "Four luminous energy orbs orbiting a radiant AI core, connected by elegant light threads, abstract representation of intelligence." + STYLE,
        "title": "四大核心能力",
        "caption": "理解 · 生成 · 推理 · 记忆",
    },
    {
        "key": "scenarios",
        "prompt": "Futuristic city skyline at dusk with translucent holographic interfaces floating above streets, people interacting with light panels." + STYLE,
        "title": "落地千行百业",
        "caption": "当智能融入每个工作流",
    },
    {
        "key": "ending",
        "prompt": "Sunrise over a calm digital ocean, a single glowing light path on the water leading toward the horizon, hopeful and inspiring." + STYLE,
        "title": "未来已来",
        "caption": "与 DeskPet 一起开启智能时代",
    },
]


def load_manifest() -> dict:
    if MANIFEST.is_file():
        try:
            return json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_manifest(m: dict) -> None:
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    from deskpet.tools.image_tools import generate_images

    manifest = load_manifest()

    # Phase A: 生图(断点续跑)
    for page in PAGES:
        key = page["key"]
        done = manifest.get(key)
        if done and Path(done).is_file():
            print(f"[skip] {key} already generated -> {done}", flush=True)
            continue
        print(f"[gen ] {key} ...", flush=True)
        t0 = time.time()
        res = generate_images([page["prompt"]], size="1536x1024")[0]
        dt = time.time() - t0
        if res.get("path"):
            manifest[key] = res["path"]
            save_manifest(manifest)
            print(f"[ok  ] {key} {dt:.0f}s -> {res['path']}", flush=True)
        else:
            print(f"[FAIL] {key} {dt:.0f}s error={res.get('error')}", flush=True)
            return 1

    # Phase B: 组装(零生图) + 16:9 裁切
    from deskpet.tools.ppt_tools import ppt_create, _crop_image_to_169

    outline = []
    for page in PAGES:
        outline.append({
            "layout": "image_full",
            "title": page["title"],
            "caption": page["caption"],
            "image_path": _crop_image_to_169(manifest[page["key"]]),
        })

    res = ppt_create(outline, theme="dark", output_path=OUT_PPTX, title="智核纪元")
    print(f"[pptx] ok={res.get('ok')} path={res.get('path')}", flush=True)
    imgs = [a for a in res.get("artifacts", []) if a.get("kind") == "image"]
    print(f"[prev] {len(imgs)} preview images", flush=True)
    for a in imgs:
        print("   ", a["path"], flush=True)
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
