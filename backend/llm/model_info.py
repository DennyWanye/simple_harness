# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Phase 1.1 — per-model 上下文窗口 + compaction 阈值的内置表 + 三层 override 解析。

Why
---
deskpet 的智能上下文系统设计于 32K–200K context 时代，所有阈值是写死的
绝对值。`deepseek-v4-pro` 现在是 1M context，硬编码导致：拿到 1M 的车按
200K 限速跑（浪费 80% 容量），切到 claude-sonnet（200K）又会爆（全局单
值）。这个模块是新的"上下文预算大脑"——把"每个模型的窗口 + compaction
触发点"做成 per-model 矩阵，并支持用户/项目两级 override。

抄 Codex `codex-rs/models-manager/src/model_info.rs` 的思路：内置 dataclass
表打底，用户/项目 TOML 深合并覆盖（后者只覆盖出现的字段）。

三层解析链（design.md D1）::

    内置 BUILTIN
      ← %APPDATA%/deskpet/model_overrides.toml   (全局用户层)
      ← <project_root>/.deskpet/context.toml      (项目层，仅 code mode)

- `resolve()` 是纯函数、无副作用、可单测（全局层路径由
  `paths.user_data_dir()` 决定，测试用 `DESKPET_USER_DATA_DIR` 钉到 tmp）
- 非 code mode：`project_root=None`，只走前两层
- 缺失 model → 退 `_default` 保守窗口（32K）
- 启动 + 每次 resolve 落 INFO 日志：
  `model_context_resolved model=%s window=%d source=%s`

为什么 TOML 不 JSON：与 config.toml 一致；用户手编友好；项目级
`.deskpet/context.toml` 可进项目 git 让团队共享。

为什么不进 config.toml 的 [agent] 段：config.toml 是 app 级单值，per-model
是矩阵，混在一起会逼用户在 app 配置里写一堆模型。独立文件 + 独立 UI 卡片
更清晰。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

import tomllib

import paths as _paths

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelContextInfo:
    """单个模型的上下文预算画像（不可变值对象）。

    字段语义：
      * ``context_window``     —— 模型名义上下文窗口（token）
      * ``effective_pct``      —— 有效利用率上限（留给输出 + 安全余量），
                                  budget 计算的分母用 window × effective_pct
      * ``compact_at_pct``     —— 触发 compaction / cycle restart 的水位线
                                  （window 比例）。DeepSeek-TUI 论文：context
                                  越大召回越差，所以 1M 模型只用到 0.75。
      * ``recall_sweet_tokens``—— 召回甜点区（retrieval 应该把有效上下文
                                  控制在此线附近，Phase 2+ 用）。
      * ``model``              —— 解析时用户实际请求的 model 名（即使缺失
                                  走 _default，也保留原名便于日志/UI）。
      * ``source``             —— 解析来源链尾：builtin / global / project。
    """

    model: str
    context_window: int
    effective_pct: float
    compact_at_pct: float
    recall_sweet_tokens: int
    source: str = "builtin"
    # 该型号支持的上下文档位(token)。UI 据此渲染「可选档位」下拉,用户选择
    # 写入全局 override(context_window)。空 tuple = 只有 context_window
    # 一档(不可调)。这是型号属性,不在 _OVERRIDABLE_FIELDS,TOML 不可覆盖。
    supported_windows: tuple = ()
    # ── 输入 token 估算校准(Incident N, 2026-09-08)────────────────────────
    # Host 只能按自己发出的 messages/tools 文本估 token。有些 provider(尤其
    # 中转站后面的 thinking 模型)会把 Host 看不见的内容重新注入 prompt——
    # 实测 deepseek-v4-pro 每轮把上一轮的 reasoning_content 加回 prompt,
    # 306 组 (request_json, usage_json) 证据里这部分最高占到 provider
    # input_tokens 的 40%,任何基于文本的估算都不可能看见它。
    #
    # 校准量 = min(base + per_turn * provider_turn_ordinal, max):
    #   * base —— 单轮的 wire 结构性余量
    #   * per_turn —— 每多一个 provider turn 就多一块隐藏 reasoning
    #   * max —— 深轮次的上限,避免深 Run 直接被估爆而 fail-close
    # 默认 1.0/0.0/1.0 = 恒等(未校准型号保持既有口径,不多 fail-close 任何 Run)。
    # 取值来源: plans/2026-09-08-hm-to-a6/DECISION-TOKEN-ESTIMATOR.md §3。
    input_estimate_ratio: float = 1.0
    input_estimate_ratio_per_turn: float = 0.0
    input_estimate_ratio_max: float = 1.0
    # ── tools 数组单独计价(Incident P, 2026-09-09)───────────────────────────
    # 上面的三元组是拟合「Host 看不见的隐藏注入」的,而隐藏注入(relay 回灌的
    # reasoning_content)只加在 **messages** 上——tools 数组是 Host 自己写的、
    # 每轮逐字节相同的定长负载,它唯一的残差是 tokenizer 密度(JSON 比散文密)。
    # 把一个我们几乎量得准的东西乘以为未知质量拟合出来的倍率是量纲错误:
    # pro 在深轮次要乘到 7.1,3.4K 的 schema 会被记成 24K。
    # 0.0 = 未单独配置,tools 沿用 messages 的 ratio(ordinal),即旧行为;
    # 未校准型号与 deepseek-v4-flash 都走这条,行为逐 token 不变。
    input_estimate_schema_ratio: float = 0.0


# ─────────────────────────── 内置表（design.md D1）───────────────────────────
#
# recall_sweet_tokens 取值依据：DeepSeek-TUI 引论文 Figure 9——deepseek-v4
# 在 256K 召回 0.76 / 1M 仅 0.59，所以 1M 模型把甜点区压在 ~384K（≈0.38），
# 200K 模型甜点区取 window 一半，32K 小模型甜点区 ≈ window×0.5。
BUILTIN: dict[str, ModelContextInfo] = {
    # 2026-06-12: 主力模型 gpt-5.5 之前不在表里 → 落 _default 32K 兜底,
    # 26k prompt 就触发压缩(真机 400 事故链一环)。默认 400K(中转站目录
    # 口径),支持选到 1M(用户在「模型与参数」面板选,写全局 override)。
    "gpt-5.5": ModelContextInfo(
        model="gpt-5.5",
        context_window=400_000,
        effective_pct=0.95,
        compact_at_pct=0.80,
        recall_sweet_tokens=160_000,
        supported_windows=(128_000, 400_000, 1_000_000),
    ),
    # 2026-09-08 Incident N: HM-TO-A6 attempt 4 的 306 组 request/usage 实测,
    # 当时取 min(1.35+0.11*ordinal, 2.5),在那 306 组上零低估。
    #
    # 2026-09-09 Incident P(F-TOK-6)重新拟合。把 attempt 5(run5,117 组)并进来后
    # 合池 **423 组**(run5 117 + run4 124 + b3682fe1 182,68 个 Run),旧三元组
    # **低估 25 组**,最差 `估算 22267 对真实 54683`(0.407×);其中 **5 组**真实
    # 超 effective_input_budget(26752)却被判为「装得下」——即真的把超窗请求发了出去,
    # 方向与 Incident O 的 fail-close 相反、也更危险。
    # 根因:旧的 0.11/turn 是在「累计 reasoning 最多 ~14.7K」的证据上拟的,而 run5 有
    # 一条 Run 累计 reasoning 到 **57 423**(单轮最高 19 579),隐藏注入的量级翻了 4 倍。
    #
    # 取值方法(与 flash 同一口径:对每个 ordinal 的实测上界留 ≥10% 工程余量):
    #   * schema 单独按 **1.30** 计价(≈4/3.1,即 JSON 相对散文的 tokenizer 密度)。
    #     relay 回灌的是上一轮 assistant 的 reasoning_content,只落在 messages 上;
    #     tools 数组是 Host 自己写的定长负载,`tool_schema_tokens` 实测只比真正发出的
    #     wire 文本低 7.2%,没有理由陪着 messages 一起乘到 7.1。
    #   * messages 的 per-ordinal 实测上界(合池 423 组,schema 已按 1.30 扣除):
    #       ord0 1.235  ord1 1.751  ord2 3.453  ord3 4.586  ord4 5.827
    #       ord5 6.081  ord6 6.109  ord7 5.937  ord8 6.335  … ord21 6.139  ord22 6.158
    #     两条最深的 Run(6154747d49 / d34ea7dbb5)彼此独立,却都在 **6.1~6.3** 处走平:
    #     隐藏 reasoning 随轮次线性增长,但 wire 本身也在增长,比值收敛而不发散。
    #     所以「饱和曲线」不是权宜,是实测形态;取 max=7.10 ≈ 观测平台 ×1.12。
    #   → min(1.50 + 1.25*ordinal, 7.10),schema 1.30。
    # 合池 423 组效果:低估 0(最紧一条余量 +10.4%),估算/实测 中位 2.72×、p95 4.50×、
    # 最大 4.94×;65 条真实超预算的请求全部被判为超预算(旧口径漏判 5 条)。
    # **代价说清楚**:中位多估 2.72×(旧 1.35×)。生产窗口 1M 时 effective≈895K,
    # 20K 的 wire 乘 7.1 也只有 142K,没有实际影响;只有把窗口钉到 32000 做实验时
    # 才会明显更早分页/裁史——而 Incident O 之后那是有序降级,不再打死 Run。
    # 真正的解仍是 F-TOK-1(用上一轮真实 usage.input_tokens 做 floor,实测中位
    # 比值 0.993),它能把这 2.72× 压回 ~1.05×;本轮先把「不低估」这条守住。
    "deepseek-v4-pro": ModelContextInfo(
        model="deepseek-v4-pro",
        context_window=1_000_000,
        effective_pct=0.95,
        compact_at_pct=0.75,
        recall_sweet_tokens=384_000,
        supported_windows=(128_000, 400_000, 1_000_000),
        input_estimate_ratio=1.50,
        input_estimate_ratio_per_turn=1.25,
        input_estimate_ratio_max=7.10,
        input_estimate_schema_ratio=1.30,
    ),
    # 2026-09-09 Incident O: flash 之前直接沿用 pro 的三元组,但两者的残差成分
    # 完全不同。pro 是 thinking 模型,残差主体是中转站把上一轮 reasoning_content
    # 加回 prompt(run5 实测单轮 reasoning 最高 19579、累计 57423),所以它的
    # ratio 必须随轮次一路长到 2.5;flash 几乎不产 reasoning(run6 实测单轮最高
    # 1813、累计仅 1922),残差只剩「JSON 工具回执比散文密」这一块,而那一块随
    # JSON 占比升高后会**饱和**,不会随轮次继续长。
    # 沿用 pro 的 0.11/turn 的后果就是本次事故:第 7 次装配时 ordinal=6 →
    # ratio=2.01,把真实 19491 的 prompt 估成 28519,超 26752 直接 fail-close。
    #
    # 取值依据: .local-test-evidence/2026-09-09/native-a6-run6/ 的 16 组
    # (request_json, usage_json) 真机配对(全部 deepseek-v4-flash、窗口钉 32000、
    # 12 个工具)。usage.input_tokens 即 provider 的 prompt_tokens
    # (cache_tokens 是它的子集,见 sdk_adapters/provider.py::_sdk_provider_usage),
    # 逐条 provider_input ÷ Host wire 估算的**按轮次最大值**:
    #   ordinal 0: 1.118 (n=6)   1: 1.436 (n=5)   2: 1.503 (n=2)
    #   ordinal 3: 1.449 (n=2)   4: 1.422 (n=1)   → 2 轮后饱和在 ~1.50
    # 三元组按「对每个 ordinal 的实测上界留约 10% 余量」定(与 safety_margin
    # 同一个数量级的工程余量,而不是把 16 个点拟合到零余量):
    #   ordinal 0 → 1.25 (+11.8%)  1 → 1.60 (+11.4%)  2+ → 1.65 (+9.8%)
    # 16 组上零低估,估算/实测中位 1.162、最大 1.433(沿用 pro 三元组时中位
    # 1.224,且在 ordinal ≥ 6 上白白多估 34%)。
    # 密度这一块与 pro 同源:pro 在 cum_reason==0 的 23 个 ordinal-0 请求上
    # 实测比值中位 1.022 / 最高 1.148,与 flash 的 1.072 / 1.118 同一档,
    # 说明 tokenizer 密度确实共享——所以 base 用两边的 ordinal-0 证据一起看,
    # 而随轮次增长的那一块**不能**共享,pro 的高轮次样本不并入 flash。
    "deepseek-v4-flash": ModelContextInfo(
        model="deepseek-v4-flash",
        context_window=1_000_000,
        effective_pct=0.95,
        compact_at_pct=0.75,
        recall_sweet_tokens=384_000,
        supported_windows=(128_000, 400_000, 1_000_000),
        input_estimate_ratio=1.25,
        input_estimate_ratio_per_turn=0.35,
        input_estimate_ratio_max=1.65,
    ),
    # 2026-07-19 temporary capability pin: the relay currently exposes
    # zai-org/GLM-5.2 as ``sf-glm-5.2`` but does not propagate a usable
    # context_window into the runtime resolver.  Keep both ids aligned at
    # the model's published 1M window until provider metadata becomes the
    # authoritative source.
    "sf-glm-5.2": ModelContextInfo(
        model="sf-glm-5.2",
        context_window=1_000_000,
        effective_pct=0.95,
        compact_at_pct=0.75,
        recall_sweet_tokens=384_000,
        supported_windows=(128_000, 400_000, 1_000_000),
    ),
    "zai-org/GLM-5.2": ModelContextInfo(
        model="zai-org/GLM-5.2",
        context_window=1_000_000,
        effective_pct=0.95,
        compact_at_pct=0.75,
        recall_sweet_tokens=384_000,
        supported_windows=(128_000, 400_000, 1_000_000),
    ),
    "claude-sonnet-4-5": ModelContextInfo(
        model="claude-sonnet-4-5",
        context_window=200_000,
        effective_pct=0.95,
        compact_at_pct=0.83,
        recall_sweet_tokens=100_000,
        supported_windows=(100_000, 200_000),
    ),
    "claude-opus-4-5": ModelContextInfo(
        model="claude-opus-4-5",
        context_window=200_000,
        effective_pct=0.95,
        compact_at_pct=0.83,
        recall_sweet_tokens=100_000,
        supported_windows=(100_000, 200_000),
    ),
    "gpt-5-pro": ModelContextInfo(
        model="gpt-5-pro",
        context_window=1_000_000,
        effective_pct=0.95,
        compact_at_pct=0.80,
        recall_sweet_tokens=384_000,
        supported_windows=(128_000, 400_000, 1_000_000),
    ),
    "gemini-2.5-pro": ModelContextInfo(
        model="gemini-2.5-pro",
        context_window=1_000_000,
        effective_pct=0.95,
        compact_at_pct=0.80,
        recall_sweet_tokens=384_000,
        supported_windows=(128_000, 400_000, 1_000_000),
    ),
    # 缺失 model 的保守兜底：本地小模型 / 未知 endpoint。32K 是 ollama
    # gemma/qwen 一类常见上限，宁可保守也别假设大窗口爆 context。
    "_default": ModelContextInfo(
        model="_default",
        context_window=32_000,
        effective_pct=0.90,
        compact_at_pct=0.80,
        recall_sweet_tokens=16_000,
    ),
}

# resolve() 允许 override 修改的字段白名单。model/source 是解析过程算出来
# 的，用户不该（也不能）通过 TOML 覆盖。
_OVERRIDABLE_FIELDS = frozenset(
    {
        "context_window",
        "effective_pct",
        "compact_at_pct",
        "recall_sweet_tokens",
        # 中转站/自建 endpoint 的隐藏注入行为各不相同,允许用户按实际
        # usage.input_tokens 反馈调这几个校准量(见 Incident N / P 备忘录)。
        "input_estimate_ratio",
        "input_estimate_ratio_per_turn",
        "input_estimate_ratio_max",
        "input_estimate_schema_ratio",
    }
)


def _read_toml(path: Path) -> dict[str, Any]:
    """读一个 TOML 文件 → dict。缺文件 / 解析失败 → {}（绝不抛，解析必须健壮）。"""
    try:
        if not path.is_file():
            return {}
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning(
            "model_info_override_parse_failed path=%s err=%s",
            path,
            str(exc)[:200],
        )
        return {}


def load_global_overrides() -> dict[str, Any]:
    """读全局用户层 ``%APPDATA%/deskpet/model_overrides.toml``。

    路径由 `paths.user_data_dir()` 决定（portable / classic / 测试 env
    override 都自动兼容）。缺文件返回 ``{}``。
    """
    return _read_toml(_paths.user_data_dir() / "model_overrides.toml")


def load_project_overrides(project_root: Optional[Path]) -> dict[str, Any]:
    """读项目层 ``<project_root>/.deskpet/context.toml``。

    ``project_root=None``（非 code mode）→ ``{}``，项目层被跳过。
    """
    if project_root is None:
        return {}
    return _read_toml(Path(project_root) / ".deskpet" / "context.toml")


def _model_section(overrides: dict[str, Any], model: str) -> dict[str, Any]:
    """从一个 override dict 里抽出 ``[models."<model>"]`` 段（缺失 → {}）。"""
    models = overrides.get("models")
    if not isinstance(models, dict):
        return {}
    section = models.get(model)
    return section if isinstance(section, dict) else {}


def _apply_override(info: ModelContextInfo, section: dict[str, Any]) -> ModelContextInfo:
    """把一个 model 段的字段深合并进 info —— 只覆盖出现且在白名单内的字段。"""
    patch: dict[str, Any] = {}
    for key, value in section.items():
        if key not in _OVERRIDABLE_FIELDS:
            logger.warning(
                "model_info_override_ignored_unknown_field model=%s field=%s",
                info.model,
                key,
            )
            continue
        patch[key] = value
    if not patch:
        return info
    return replace(info, **patch)


def resolve(model: str, project_root: Optional[Path] = None) -> ModelContextInfo:
    """三层解析某个 model 的 ModelContextInfo（纯函数 + 落日志）。

    解析链：BUILTIN[model] (缺失 → BUILTIN["_default"])
            ← global ``model_overrides.toml``
            ← project ``<root>/.deskpet/context.toml``（仅 project_root 非 None）

    深合并：后层只覆盖**出现**的字段，未出现字段保留前层值。``source``
    记为解析链中最后一个真正改动了字段的层（project > global > builtin）。
    """
    base = BUILTIN.get(model)
    if base is None:
        # 缺失 model：拿 _default 的参数，但 model 名仍记用户实际请求的，
        # 便于日志/UI 显示"你用的是 some-local-7b，按 32K 兜底"。
        base = replace(BUILTIN["_default"], model=model)

    info = replace(base, source="builtin")

    # 全局层
    global_section = _model_section(load_global_overrides(), model)
    if global_section:
        merged = _apply_override(info, global_section)
        if merged != info:
            info = replace(merged, source="global")
        else:
            info = merged

    # 项目层（仅 code mode；project_root=None 时 loader 返回 {} 自动跳过）
    project_section = _model_section(load_project_overrides(project_root), model)
    if project_section:
        merged = _apply_override(info, project_section)
        if merged != info:
            info = replace(merged, source="project")
        else:
            info = merged

    logger.info(
        "model_context_resolved model=%s window=%d source=%s",
        info.model,
        info.context_window,
        info.source,
    )
    return info


def supported_windows_for(model: str) -> list[int]:
    """该型号可选的上下文档位(给 UI 下拉)。

    BUILTIN 有档位表 → 用之;没有 → 单档 [当前解析 window](不可调)。
    当前解析值(含用户 override)若不在表里也并入,保证下拉始终含当前值。
    """
    info = resolve(model)
    wins = list(BUILTIN[model].supported_windows) if model in BUILTIN else []
    if not wins:
        wins = [info.context_window]
    if info.context_window not in wins:
        wins.append(info.context_window)
    return sorted(set(int(w) for w in wins))


# Temporary UI policy while relay /models responses expose ids but no
# structured context capability metadata.  This is deliberately separate from
# BUILTIN model facts: users may choose one of these operational budget tiers,
# but we do not claim that every Provider/model natively supports every tier.
TEMPORARY_USER_CONTEXT_WINDOWS: tuple[int, ...] = (
    128_000,
    256_000,
    512_000,
    1_000_000,
)


def user_selectable_windows_for(model: str) -> list[int]:
    """Temporary user-controlled context budget tiers for a model id."""
    if not str(model or "").strip():
        return []
    return list(TEMPORARY_USER_CONTEXT_WINDOWS)


def save_global_window_override(model: str, context_window: int) -> bool:
    """把用户选择的上下文档位写入全局 ``model_overrides.toml``。

    读-改-写整个文件(结构只有 [models."<id>"] 段,手写序列化足够安全)。
    resolve() 的 global 层随即生效 —— 压缩阈值/预算/UI 全部跟着对齐。
    选择必须 ∈ user_selectable_windows_for(model)(调用方校验,这里再守一遍)。
    成功 True;非法档位/IO 失败 False(不抛)。
    """
    try:
        window = int(context_window)
        if window not in user_selectable_windows_for(model):
            logger.warning(
                "model_window_override_rejected model=%s window=%d not in supported",
                model, window,
            )
            return False
        path = _paths.user_data_dir() / "model_overrides.toml"
        data = _read_toml(path)
        models = data.get("models")
        if not isinstance(models, dict):
            models = {}
        section = models.get(model)
        if not isinstance(section, dict):
            section = {}
        section["context_window"] = window
        models[model] = section
        data["models"] = models

        lines = [
            "# DeskPet 模型上下文用户覆盖 — 由「模型与参数」面板写入。",
            "# 字段白名单见 llm/model_info.py:_OVERRIDABLE_FIELDS。",
            "",
        ]
        for mid, sec in models.items():
            if not isinstance(sec, dict):
                continue
            lines.append(f'[models."{mid}"]')
            for k, v in sec.items():
                if isinstance(v, bool):
                    lines.append(f"{k} = {str(v).lower()}")
                elif isinstance(v, (int, float)):
                    lines.append(f"{k} = {v}")
                else:
                    lines.append(f'{k} = "{v}"')
            lines.append("")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(
            "model_window_override_saved model=%s window=%d path=%s",
            model, window, path,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("model_window_override_save_failed err=%s", str(exc)[:200])
        return False
