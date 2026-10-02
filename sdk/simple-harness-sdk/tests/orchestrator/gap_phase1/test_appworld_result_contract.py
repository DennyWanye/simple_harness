"""The AppWorld profile's prompt choices: one current profile, roles it names are real.

删旧平面模式第三刀第 4 步（2026-10-02）：AppWorld 只留一份当前档案；平面 AppWorld
执行者模板（v1～v3）与历史档案版本一起删了，档案里只剩审阅员覆盖，分层执行者由
``HIERARCHICAL_WORKER_TEMPLATES`` 指定。
"""

from agent_orchestrator.governance.domains import APPWORLD_PROFILE, resolve_domain
from agent_orchestrator.runtime.role_templates import ROLES, template_for_domain


def test_the_appworld_profile_has_one_current_version():
    assert resolve_domain("appworld-v1") is APPWORLD_PROFILE
    assert APPWORLD_PROFILE.version == "4"


def test_the_appworld_critic_override_resolves_to_a_registered_template():
    assert dict(APPWORLD_PROFILE.role_templates) == {"critic": "critic-appworld-v1"}
    critic = template_for_domain(ROLES["critic"], APPWORLD_PROFILE, {})
    assert critic.prompt_version == "critic-appworld-v1"
    # roles the profile does not override fall back to the system template
    worker = template_for_domain(ROLES["worker"], APPWORLD_PROFILE, {})
    assert worker == ROLES["worker"]


def test_the_hierarchical_worker_pointer_is_beside_the_profile_not_inside_it():
    """P2.3d / defect D1: the hierarchical Worker prompt AppWorld Missions get.

    It is **not** a ``role_templates`` entry: that mapping is read as "role name →
    prompt version" both by ``template_for_domain`` and by callers that iterate it, so
    a ``worker_hierarchical`` key there is a key that breaks both readings.
    """

    from agent_orchestrator.governance.domains import HIERARCHICAL_WORKER_TEMPLATES
    from agent_orchestrator.runtime.role_templates import hierarchical_worker_for_domain

    assert set(APPWORLD_PROFILE.role_templates) <= set(ROLES), (
        "every key of role_templates names a role; a non-role key breaks both readers"
    )
    assert HIERARCHICAL_WORKER_TEMPLATES["appworld-v1"] == "worker-appworld-hierarchical-v1"
    chosen = hierarchical_worker_for_domain(APPWORLD_PROFILE)
    assert chosen.prompt_version == "worker-appworld-hierarchical-v1"
    assert "appworld_execute" in chosen.tool_names
