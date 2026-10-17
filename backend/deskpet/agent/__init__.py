# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Host 侧给 Agent 运行配套的部件。

包里主要有：

- ``assembler/`` — ContextAssembler（TaskClassifier + ComponentRegistry +
  BudgetAllocator + ContextBundle）。
- ``run_presenter.py`` — 把运行事件投影成产品前端帧。
- ``tokens.py``、``context_budget.py``、``context_usage.py`` — token 计数、上下文预算与用量。
- ``turn_preparer.py`` — 产品轮次输入与 Host 共用的产品路由工具名。

本 ``__init__`` 不导出任何名字。
"""
