# -*- coding: utf-8 -*-
"""教育现状 PPT image-2 版: 全幅 AI 配图(断点续跑 manifest,不重复扣费)。
Phase A 逐张生图落 manifest; Phase B 组装(零生图)。"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, r"G:\projects\deskpet\backend")
sys.path.insert(0, r"G:\projects\deskpet\plans\2026-06-09-ppt-beauty-mainline")
import os
os.environ.setdefault("DESKPET_USER_DATA_DIR", r"G:\projects\deskpet\.tmp\fp345-userdata")
os.environ["DESKPET_PPT_PREVIEW_RENDER"] = "0"

from edu_outline import IMAGE_OUTLINE  # noqa: E402

MANIFEST = Path(r"G:\projects\deskpet\.tmp\edu-image-manifest.json")
OUT = sys.argv[1] if len(sys.argv) > 1 else r"G:\projects\deskpet\.tmp\edu-image-v1.pptx"


def main() -> int:
    from deskpet.tools.image_tools import generate_images
    from deskpet.tools.ppt_tools import ppt_create, _crop_image_to_169

    manifest = {}
    if MANIFEST.is_file():
        try:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}

    for i, page in enumerate(IMAGE_OUTLINE):
        key = f"p{i}"
        if manifest.get(key) and Path(manifest[key]).is_file():
            print(f"[skip] {key}", flush=True)
            continue
        print(f"[gen ] {key} {page['title']} ...", flush=True)
        t0 = time.time()
        r = generate_images([page["image_prompt"]], size="1536x1024")[0]
        if not r.get("path"):
            print(f"[FAIL] {key} {time.time()-t0:.0f}s {r.get('error')}", flush=True)
            return 1
        manifest[key] = r["path"]
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        print(f"[ok  ] {key} {time.time()-t0:.0f}s", flush=True)

    outline = []
    for i, page in enumerate(IMAGE_OUTLINE):
        so = dict(page)
        so.pop("image_prompt", None)
        so["image_path"] = _crop_image_to_169(manifest[f"p{i}"])
        outline.append(so)

    res = ppt_create(outline, theme="dark", output_path=OUT, title="中国教育现状全景")
    print("ok =", res.get("ok"), " path =", res.get("path"), flush=True)
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
