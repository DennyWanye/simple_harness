# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""不联网跑通接线用的脚本模型与测试计数器（公开入口）。

应用在接入初期、CI 里、或写例子时，用它们替代真实模型证明"宿主起得来、任务走得完、我的工具被调到"：

    host = await OrchestrationHost.open(root, principal=..., provider=ScriptedProvider(),
                                        counter=FixtureWordCounter(), require_acceptance=False)

- ``ScriptedProvider(worker=...)``：按角色现算回复；``worker`` 可换成自己的函数（先调你的工具，
  再交给 ``worker_reply`` 写文件、交结果）；``package_of(request)`` 读出这次请求的工作包（任务合同、
  可用动作、之前动作的结果）。
- ``FixtureWordCounter``：按词数计的测试用量计数器，**不是**认证计数器，不能用于生产。

生产用 ``model=ModelConfig(...)``，不要用这里的任何东西。
"""

from __future__ import annotations

from ..testing.fixtures import package_of
from ..testing.scripted_replies import LayeredScriptedProvider as ScriptedProvider
from ..testing.scripted_replies import worker_reply
from ..testing.word_counter import FixtureWordCounter

__all__ = ("FixtureWordCounter", "ScriptedProvider", "package_of", "worker_reply")
