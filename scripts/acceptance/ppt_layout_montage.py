"""Render the six durable PPT full-page layouts for visual acceptance."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from deskpet.tools import ppt_tools


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "plans" / "manual-results-2026-07-11-ppt-layout-progress-ui"


def background(path: Path, index: int) -> Path:
    width, height = ppt_tools.FULL_PAGE_TARGET_SIZE
    palette = [
        ((11, 25, 47), (25, 122, 145)),
        ((18, 32, 55), (116, 72, 155)),
        ((12, 42, 40), (40, 151, 125)),
        ((46, 28, 22), (185, 101, 61)),
        ((29, 31, 52), (64, 107, 171)),
        ((21, 24, 36), (120, 74, 96)),
    ]
    start, end = palette[index]
    image = Image.new("RGB", (width, height), start)
    draw = ImageDraw.Draw(image)
    for x in range(width):
        t = x / max(1, width - 1)
        color = tuple(int(a + (b - a) * t) for a, b in zip(start, end))
        draw.line((x, 0, x, height), fill=color)
    draw.ellipse(
        (width * 0.62, height * 0.12, width * 0.94, height * 0.68),
        fill=tuple(min(255, value + 35) for value in end),
    )
    image.save(path)
    return path


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    specs = [
        ("cover_band", "DeskPet 智能工作流", "从等待结果，到看见任务真正推进"),
        ("text_left", "研究先于结论", "规划、检索、核验形成完整证据链"),
        ("text_right", "把复杂任务拆开", "每一步都有状态，也都有恢复点"),
        ("visual_top", "一条可理解的进度", "不刷屏；不泄露内部参数；失败有下一步"),
        ("floating_card", "页面需要节奏", "封面、重点、对比、结论不再共用一套模板"),
        ("quote_center", "让过程可见，让结果可信", "DeskPet 产品原则"),
    ]
    pages: list[Path] = []
    report: list[dict[str, object]] = []
    for index, (layout, title, subtitle) in enumerate(specs):
        source = background(OUT / f"source-{index + 1}.png", index)
        page = OUT / f"layout-{index + 1}-{layout}.png"
        slide = {
            "title": title,
            "subtitle": subtitle,
            "bullets": (
                ["清晰的层级", "稳定的安全区", "确定性的中文排版"]
                if layout in {"text_left", "text_right", "visual_top"}
                else []
            ),
            "quote": title if layout == "quote_center" else "",
            "cite": "DeskPet · 2026",
            "full_page_layout": layout,
        }
        ppt_tools._compose_full_page_image(str(source), slide, output_path=str(page))
        pages.append(page)
        report.append({"page": index + 1, "layout": layout, "path": str(page)})

    thumb_w, thumb_h = 640, 360
    montage = Image.new("RGB", (thumb_w * 2, thumb_h * 3), (10, 13, 20))
    for index, page in enumerate(pages):
        with Image.open(page) as opened:
            thumb = opened.convert("RGB").resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        montage.paste(thumb, ((index % 2) * thumb_w, (index // 2) * thumb_h))
    montage.save(OUT / "layout-montage.png")
    (OUT / "layout-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(OUT / "layout-montage.png")


if __name__ == "__main__":
    main()
