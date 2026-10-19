# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""一个外部 Python 应用用 SDK 跑完一个编排任务（只装 simple-harness-sdk，不碰桌面端代码）。

两种跑法：

* 真实模型（默认）：从环境变量读 DeepSeek 端点::

      SH_BASEURL=https://api.deepseek.com/v1 SH_APIKEY=... SH_MODEL=deepseek-flash \\
      SH_TOKENIZER=./deepseek-v41-tokenizer.json python demo.py

  分词文件不存在时先按钉死的哈希下载到 ``SH_TOKENIZER``。
* 脚本模型（``--scripted``）：不联网，用 SDK 自带的脚本化模型回复与测试计数器，只证明接线。

任务：在工作区写一份 NOTES.md。跑完打印任务状态、产物内容与用量。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

from agent_orchestrator.host import (
    HostSettings,
    HostUnavailable,
    ModelConfig,
    OrchestrationHost,
    Principal,
)

GOAL = "写一份简短的中文说明，介绍三种常见的家庭省电办法，每种一句话，保存为 NOTES.md。"
CRITERIA = ["file:NOTES.md", "NOTES.md 列出三种省电办法，每种一句话"]


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or default


async def main(args: argparse.Namespace) -> int:
    root = Path(args.root or tempfile.mkdtemp(prefix="orchestration-host-"))
    options: dict = {
        "principal": Principal("demo-user", "演示用户"),
        "settings": HostSettings(),
        "auto_mode": True,  # 自动授权每轮规划、自动确认纯内容要求；人要把关就改成 False
    }
    if args.scripted:
        from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider
        from agent_orchestrator.testing.word_counter import FixtureWordCounter

        options.update(provider=LayeredScriptedProvider(), counter=FixtureWordCounter())
        options["require_acceptance"] = False
    else:
        base_url, api_key = _env("SH_BASEURL"), _env("SH_APIKEY")
        if not base_url or not api_key:
            print("缺 SH_BASEURL / SH_APIKEY（或用 --scripted 跑脚本模型）", file=sys.stderr)
            return 2
        from agent_orchestrator.host.tokenizer import ensure_deepseek_tokenizer

        tokenizer = ensure_deepseek_tokenizer(_env("SH_TOKENIZER", "deepseek-v41-tokenizer.json"))
        options.update(
            model=ModelConfig(
                base_url=base_url,
                model=_env("SH_MODEL", "deepseek-flash"),
                api_key=api_key,
                compatible_hosts=tuple(
                    h for h in (_env("SH_COMPATIBLE_HOSTS") or "").split(",") if h.strip()
                ),
                allow_private_http=base_url.startswith("http://"),
            ),
            tokenizer_path=tokenizer,
        )
    try:
        host = await OrchestrationHost.open(root, **options)
    except HostUnavailable as error:
        print(f"宿主没有起来：{error}", file=sys.stderr)
        return 1
    try:
        print(
            "状态：",
            {k: host.status()[k] for k in ("state", "default_context_profile_id", "sandbox")},
        )
        receipt = host.create_mission(
            {"goal": GOAL, "success_criteria": CRITERIA, "idempotency_key": "demo-notes-1"}
        )
        mission_id = receipt["mission_id"]
        print("任务：", mission_id)
        final = await host.run_until_settled(mission_id, timeout=args.timeout)
        mission = final["snapshot"]["mission"]
        print("结束：", mission["status"], "停止原因：", mission.get("stop_reason"))
        print("用量：", final["snapshot"].get("budget_usage"))
        for artifact in final["snapshot"].get("artifacts", []):
            if artifact.get("path", "").endswith("NOTES.md"):
                body = host.artifact_read(artifact["id"])
                print("---- NOTES.md ----")
                print(body.get("content"))
        return 0 if mission["status"] == "COMPLETED" else 1
    finally:
        await host.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scripted", action="store_true", help="不联网，用脚本化模型回复")
    parser.add_argument("--root", help="编排数据目录（默认临时目录）")
    parser.add_argument("--timeout", type=float, default=3600.0, help="最长等多少秒")
    sys.exit(asyncio.run(main(parser.parse_args())))
