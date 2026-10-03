# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""通用档案与 code-v2 提示词（纯函数）。

HTN 补齐阶段 A′：K01–K03、工具引用、冻结档案、已验收核验不可改这些原来建在已退役的
``two_leaf_service`` 上（手工建尝试、手记带声明的结果），改在产品主循环里钉：
``step04/test_claims_knowledge_main_loop.py::test_a_passing_check_supports_but_never_verifies_a_workers_claim``。
"工具引用解析到本尝试成功的调用才算依据"的正向那半条删除：产品不把工具调用编号告诉执行者，
执行者引用不到真实的调用编号（悬空引用不支撑仍在主循环里钉着）。"""

from __future__ import annotations


def test_no_domain_is_the_current_general_profile_and_round_trips():
    from agent_orchestrator.governance.domains import (
        CODE_PROFILE,
        DomainProfileV1,
        resolve_domain,
    )

    general = resolve_domain(None)
    assert general is CODE_PROFILE
    assert DomainProfileV1.from_json(general.to_json()) == general


def test_code_v2_prompts_describe_scoped_observations():
    from agent_orchestrator.governance.domains import CODE_PROFILE
    from agent_orchestrator.runtime.role_templates import ROLES, template_for_domain

    for role in CODE_PROFILE.role_templates:
        if role not in ROLES:  # the profile still names removed Worker variants
            continue
        template = template_for_domain(ROLES[role], CODE_PROFILE, {})
        assert "test_observation" in template.instructions
        assert "才可能被判 VERIFIED" not in template.instructions
