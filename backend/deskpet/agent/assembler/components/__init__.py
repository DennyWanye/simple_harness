# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""ContextAssembler 的内置组件。

每个组件提供 ``async def provide(ctx) -> Slice``（协议见 ``base.py``）：

- ``memory.py``             — 占位组件，返回空 Slice（认知记忆系统已移除）。
- ``workspace_memory.py``   — 占位组件，返回空 Slice（同上）。
- ``tool.py``               — 按 ``policy.tools`` 白名单过滤工具结构。
- ``skill.py``              — 按 ``policy.prefer`` 自动挂载技能前导。
- ``persona.py``            — 冻结的人设 / 系统提示词段。
- ``time_component.py``     — 当前时间、日期、时区。
- ``workspace.py``          — %APPDATA%\\deskpet\\workspace\\ 摘要 / 当前目录。
- ``preference_profile.py`` — 用户偏好与画像事实。
- ``project_rules.py``      — 按路径加载的项目规则（代码 / 工作区上下文）。

上级包的 ``ComponentRegistry`` 用 ``asyncio.gather`` 并发调用这些组件，总耗时接近
最慢的组件，而不是串行相加。
"""
