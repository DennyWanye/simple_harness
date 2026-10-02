# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Host support 0.9.8 · SA-1 / SA-2 / SA-3 / SA-6: a deployment can switch off local code
execution (Host plan 2026-09-11 §3.1, plan review P0-1).

With ``local_code_execution=False`` no model-written code runs on this machine, however
the Planner fills ``verification_policy``: the Graph Manager refuses ``code_test``
(``verification_policy_undeployed``), the system defaults leave it out, a Task that
already carries it from before the switch records the layer as ERROR (never PASS), a
Mission ``pytest:`` criterion is judged unmet without running, and ``run_tests`` is
refused.  The decisive oracle is behavioural: a spy around the real ``run_pytest`` is
never called and a test file whose import writes a marker leaves no marker.

删旧平面模式 第三刀：三条靠平面规划器/平面任务图驱动的测试（平面规划器模板、规划器要
``code_test`` 被拒后重规划、开关之前建的平面任务里 ``pytest:`` 判据判未满足）随平面删；
这里只剩部署开关本身的两条。
"""

from __future__ import annotations

import pytest

from agent_orchestrator.contracts import STEP2_IMPLEMENTED_LAYERS
from agent_orchestrator.governance.policies import DeploymentPolicy, deployed_layers

TOOLS3 = ("workspace_read_file", "workspace_write_file", "workspace_list")
TOOLS4 = (*TOOLS3, "run_tests")
OFF = DeploymentPolicy(allowed_tools=TOOLS3, local_code_execution=False)
ON = DeploymentPolicy()


# ------------------------------------------------------------------ SA-6
def test_the_default_deployment_is_unchanged():
    assert ON.local_code_execution is True
    assert deployed_layers(ON) == STEP2_IMPLEMENTED_LAYERS
    assert ON.to_json()["local_code_execution"] is True


def test_off_drops_code_test_and_refuses_run_tests_as_a_contradiction():
    assert deployed_layers(OFF) == STEP2_IMPLEMENTED_LAYERS - {"code_test"}
    assert OFF.to_json()["local_code_execution"] is False
    with pytest.raises(ValueError, match="run_tests"):
        DeploymentPolicy(allowed_tools=TOOLS4, local_code_execution=False)
