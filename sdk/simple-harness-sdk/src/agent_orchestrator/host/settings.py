# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""宿主入口的配置：模型（``ModelConfig``）、运行参数（``HostSettings``）与隔离区（``SandboxOptions``）。

编排的每个执行池都要一个经过认证的用量计数器：DeepSeek V4.1 用官方分词器精确计数；别的
OpenAI 协议模型（Luna / Sol 这类，2026-10-21 用户决定放开）用按字节的上界计数器（可以多算
不可以少算），不开思考池。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..runtime.connectors import LEVELS

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

NO_COUNTER_REASON = "当前模型没有可用的用量计数器"


@dataclass(frozen=True)
class ModelConfig:
    """一个 OpenAI 兼容端点上的模型（DeepSeek 精确计数；其余按上界计数）。密钥只进 SDK 的
    ``Secret``，不进任何日志与回执。"""

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
    #: 首选模型传输层失败时换用的备用模型（同一端点、同一密钥）；每请求最多换一次，同一运行黏住。
    fallback_model: str | None = None
    #: 非 DeepSeek 模型的线路前言边际起点（token）；按报告用量只升不降。
    relay_margin_floor_tokens: int = 8192

    def __post_init__(self) -> None:
        if not self.base_url or not self.model or not self.api_key:
            raise ValueError("ModelConfig 需要 base_url、model 与 api_key")
        if self.thinking not in ("enabled", "disabled"):
            raise ValueError("thinking 只能是 'enabled' 或 'disabled'")
        if self.fallback_model is not None and (
            not isinstance(self.fallback_model, str)
            or not self.fallback_model.strip()
            or self.fallback_model == self.model
        ):
            raise ValueError("fallback_model 必须是另一个模型名")
        if type(self.relay_margin_floor_tokens) is not int or self.relay_margin_floor_tokens < 0:
            raise ValueError("relay_margin_floor_tokens 必须是非负整数")

    def public(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "thinking": self.thinking,
            "fallback_model": self.fallback_model,
        }


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
    #: 外部操作的最高风险等级（L0～L3）。注册了应用连接器时必须写明；没写时只有文件发布，按 L2。
    max_action_level: str | None = None
    #: 一个任务里步骤中途可提议的动作上限（持久，跨尝试与重启累计）。
    max_step_actions_per_mission: int = 20
    #: 连接器一次执行 / 查询 / 整理参数的时限（秒）；超时记"结果不明"，不自动重做。
    connector_timeout_seconds: float = 30.0
    #: 快速聊天一回合的上限：模型调用次数、工具次数、时限（秒）。到了上限这一回合按失败返回。
    chat_max_model_calls: int = 8
    chat_max_tool_calls: int = 8
    chat_turn_seconds: float = 180.0
    continuation_enabled: bool = True
    continuation_merge_window_seconds: float = 5.0
    continuation_max_turns_per_session: int = 50
    continuation_voice_max_wait_seconds: float = 60.0
    tool_search: bool = True
    main_session_sandbox_run: bool = False

    def __post_init__(self) -> None:
        for name in ("tool_search", "main_session_sandbox_run", "continuation_enabled"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be bool")
        for name in ("continuation_merge_window_seconds", "continuation_voice_max_wait_seconds"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if (
            type(self.continuation_max_turns_per_session) is not int
            or self.continuation_max_turns_per_session < 0
        ):
            raise ValueError("continuation_max_turns_per_session must be nonnegative")
        if self.context_input_tokens not in CONTEXT_INPUT_CHOICES:
            raise ValueError(f"context_input_tokens 只能是 {CONTEXT_INPUT_CHOICES} 之一")
        for name in ("max_concurrency", "max_concurrent_model_calls"):
            value = getattr(self, name)
            if not 1 <= int(value) <= 4:
                raise ValueError(f"{name} 必须在 1～4 之间")
        if self.global_monthly_max_tokens < self.default_mission_max_tokens:
            raise ValueError("月配额不能小于单个任务的默认上限")
        if self.max_action_level is not None and self.max_action_level not in LEVELS:
            raise ValueError(f"max_action_level 只能是 {LEVELS} 之一")
        if (
            type(self.max_step_actions_per_mission) is not int
            or self.max_step_actions_per_mission < 1
        ):
            raise ValueError("max_step_actions_per_mission 必须是正整数")
        if not float(self.connector_timeout_seconds) > 0:
            raise ValueError("connector_timeout_seconds 必须是正数")
        for name in ("chat_max_model_calls", "chat_max_tool_calls"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} 必须是正整数")
        if not float(self.chat_turn_seconds) > 0:
            raise ValueError("chat_turn_seconds 必须是正数")


@dataclass(frozen=True)
class SandboxOptions:
    """模型写的代码（``run_tests``、``pytest:`` 要求、核验代码）在哪个隔离区里跑。

    默认按系统自动选：macOS 用系统自带的 Seatbelt，Linux 用 bubblewrap（要先装 ``bwrap``）。
    每次启动都实际检查一遍隔离（不能联网、读不到 HOME 和别处的文件、写不出工作目录、碰不到
    宿主进程、脱离进程树的后台进程也会被收掉）；没通过就不跑模型写的代码，原因写在
    ``status()["sandbox"]``。
    """

    #: 跑模型代码的 Python；默认是宿主自己的解释器。要用另一个（例如装了测试依赖的）就填它。
    interpreter: str | None = None
    #: 额外的只读路径（例如另一个依赖目录）。不要把 HOME 或数据目录放进来——检查会判不通过。
    extra_read_paths: tuple[str, ...] = ()
    #: Linux：``bwrap`` 的路径；默认在 ``PATH`` 里找。
    bwrap: str | None = None
    #: 应用自带的隔离执行器（实现 ``agent_orchestrator.runtime.sandbox.SandboxExecutorPort``，
    #: ``isolated=True``）。SDK 照样对它做同一套启动检查，通过了才用；它的重启后回收归应用负责。
    executor: Any = None


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
        tool_search=raw.get("tool_search")
        if type(raw.get("tool_search")) is bool
        else defaults.tool_search,
        main_session_sandbox_run=raw.get("main_session_sandbox_run")
        if type(raw.get("main_session_sandbox_run")) is bool
        else defaults.main_session_sandbox_run,
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
    "NO_COUNTER_REASON",
    "THINKING_EFFORT",
    "HostSettings",
    "ModelConfig",
    "host_settings_from_mapping",
    "split_csv",
)
