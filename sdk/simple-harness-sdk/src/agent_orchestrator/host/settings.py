# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""宿主入口的两份配置：模型（``ModelConfig``）与运行参数（``HostSettings``）。

只支持 DeepSeek（这一版的用户决定，2026-10-19）：编排的每个执行池都要一个经过认证的用量计数器，
目前只有 DeepSeek V4.1 的官方分词器与请求渲染器做到了精确计数；别的模型没有计数器，就没有执行池。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: DeepSeek 官方端点；只有它（和 ``compatible_hosts`` 里声明的转发主机）才有认证计数器。
DEEPSEEK_OFFICIAL_HOSTS = frozenset({"api.deepseek.com"})
#: 官方端点把 flash 模型叫 ``deepseek-flash``；配置写成 ``deepseek-v4-flash`` 的按这张表改名。
MODEL_ALIASES = {"deepseek-v4-flash": "deepseek-flash"}
#: 认证计数器认的两个线路名字。
DEEPSEEK_COUNTER_MODELS = frozenset({"deepseek-flash", "deepseek-v4.1-flash"})
#: 思考池连同开关一起显式送出的努力档（2026-09-24 在转发线路上实测：只开开关 0/6 次出思考，
#: 开关加档位 11/11 次都出）；"high" 是 DeepSeek 文档的默认值，对官方端点不改变任何东西。
THINKING_EFFORT = "high"
#: 新任务上下文窗口可选的三档（token）。
CONTEXT_INPUT_CHOICES = (262_144, 524_288, 614_400)

ONLY_DEEPSEEK_REASON = "编排只支持 DeepSeek：当前模型没有经过认证的用量计数器，请改用 DeepSeek 模型"


@dataclass(frozen=True)
class ModelConfig:
    """一个 OpenAI 兼容端点上的 DeepSeek 模型。密钥只进 SDK 的 ``Secret``，不进任何日志与回执。"""

    base_url: str
    model: str
    api_key: str = field(repr=False)
    #: 声明为"原样转发到 DeepSeek 官方"的主机（日卡网关、本机转发），小写；它们用上界计数器。
    compatible_hosts: tuple[str, ...] = ()
    #: 转发线路回显这个模型时用的别名（如 ``deepseek-ai/DeepSeek-V4.1-Flash``）；只声明不猜。
    response_model_aliases: tuple[str, ...] = ()
    #: 新任务默认开不开思考模式：``enabled`` / ``disabled``。已建任务保持建立时冻结的池。
    thinking: str = "enabled"
    #: 普通调用的线路超时（秒）；思考调用另有 900 秒。
    timeout: float = 180.0
    #: 允许明文 http 的私网端点（本机转发）。
    allow_private_http: bool = False

    def __post_init__(self) -> None:
        if not self.base_url or not self.model or not self.api_key:
            raise ValueError("ModelConfig 需要 base_url、model 与 api_key")
        if self.thinking not in ("enabled", "disabled"):
            raise ValueError("thinking 只能是 'enabled' 或 'disabled'")

    def public(self) -> dict[str, Any]:
        return {"base_url": self.base_url, "model": self.model, "thinking": self.thinking}


@dataclass(frozen=True)
class HostSettings:
    """运行参数。默认值就是桌面产品在用的那一份（2026-09-25／09-26／10-10 用户决定）。"""

    max_concurrency: int = 2
    max_concurrent_model_calls: int = 2
    #: 驱动循环的节奏：有工作或模型调用在途 / 只等人 / 空闲。
    tick_active_seconds: float = 2.0
    tick_waiting_seconds: float = 20.0
    tick_idle_seconds: float = 30.0
    lease_seconds: float = 60.0
    backoff_max_seconds: float = 60.0
    rebuild_after_failures: int = 3
    degraded_after_failures: int = 5
    #: 没写预算的新任务：总上限 2000 万 token、12 次尝试；每个叶子步骤固定 1000 万。
    default_mission_max_tokens: int = 20_000_000
    task_max_tokens: int = 10_000_000
    default_mission_max_attempts: int = 12
    #: 本部署所有任务每个自然月合计的 token 上限（SDK 每月一个全局总账）。
    global_monthly_max_tokens: int = 2_000_000_000
    #: 新任务的上下文窗口：600K（默认）、512K 或 256K。
    context_input_tokens: int = 614_400
    #: 发布文件的目录（用户授权的那一个）；空字符串表示不发布，任务不能带发布要求。
    publish_dir: str = ""
    #: 没有沙箱时是否仍启动（只是不跑模型写的代码）。
    max_action_level: str = "L2"

    def __post_init__(self) -> None:
        if self.context_input_tokens not in CONTEXT_INPUT_CHOICES:
            raise ValueError(f"context_input_tokens 只能是 {CONTEXT_INPUT_CHOICES} 之一")
        for name in ("max_concurrency", "max_concurrent_model_calls"):
            value = getattr(self, name)
            if not 1 <= int(value) <= 4:
                raise ValueError(f"{name} 必须在 1～4 之间")
        if self.global_monthly_max_tokens < self.default_mission_max_tokens:
            raise ValueError("月配额不能小于单个任务的默认上限")


def host_settings_from_mapping(section: Mapping[str, Any] | None) -> HostSettings:
    """从一段配置（如 ``config.toml`` 的 ``[orchestration]``）读运行参数。

    未知键忽略，坏值用默认。"""

    raw = dict(section or {})
    defaults = HostSettings()

    def bounded(name: str, low: int, high: int) -> int:
        value = raw.get(name)
        default = getattr(defaults, name)
        if isinstance(value, bool) or not isinstance(value, int):
            return default
        return max(low, min(high, value))

    context = raw.get("context_input_tokens")
    publish = raw.get("publish_dir")
    return HostSettings(
        max_concurrency=bounded("max_concurrency", 1, 4),
        max_concurrent_model_calls=bounded("max_concurrent_model_calls", 1, 4),
        context_input_tokens=(
            context
            if type(context) is int and context in CONTEXT_INPUT_CHOICES
            else defaults.context_input_tokens
        ),
        publish_dir=str(publish).strip() if isinstance(publish, str) else "",
        global_monthly_max_tokens=bounded(
            "global_monthly_max_tokens", defaults.default_mission_max_tokens, 10**12
        ),
    )


def split_csv(value: Any, *, lower: bool = False) -> tuple[str, ...]:
    """逗号分隔或列表 → 去空去重的元组（主机名按小写）。"""

    items = (
        value.split(",") if isinstance(value, str) else (value if isinstance(value, list) else [])
    )
    out: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            text = item.strip()
            out.append(text.lower() if lower else text)
    return tuple(dict.fromkeys(out))


__all__ = (
    "CONTEXT_INPUT_CHOICES",
    "DEEPSEEK_COUNTER_MODELS",
    "DEEPSEEK_OFFICIAL_HOSTS",
    "MODEL_ALIASES",
    "ONLY_DEEPSEEK_REASON",
    "THINKING_EFFORT",
    "HostSettings",
    "ModelConfig",
    "host_settings_from_mapping",
    "split_csv",
)
