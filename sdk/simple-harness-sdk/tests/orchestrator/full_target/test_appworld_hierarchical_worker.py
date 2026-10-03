# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3d / defect D1: an AppWorld Mission in the hierarchical mode keeps its domain.

Every one of the Grok acceptance run's 20 L1 episodes reported the same thing from
inside the Worker:

    tools_and_permissions.allowed_tools includes ``appworld_execute``, but this
    attempt's exposed tool schema only contains workspace_list / workspace_read_file /
    workspace_write_file

``_hierarchical_worker_template`` returned the constant ``WORKER_HIERARCHICAL`` for
every prompt version outside a one-element frozenset, which threw away the
``worker-appworld-v3`` template the line above had selected.  ``WORKER_HIERARCHICAL``
is ``_revise(WORKER, …)`` and ``_revise`` copies ``tool_names``, so the role carried
the code domain's four tools; ``effective_tools`` iterates ``role_tools`` and
intersects, so ``appworld_execute`` — present in the Mission, Task and deployment sets
— was dropped for not being in the role's.  The prompt went with it: the Worker was
told to run pytest with ``run_tests`` while operating a simulated world.

The invariant below is the one the diagnosis called "the only one that can stop this".
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from h1i_seed import CONFIG, run_until  # noqa: E402

from agent_orchestrator.governance.domains import APPWORLD_DOMAIN  # noqa: E402
from agent_orchestrator.runtime.appworld_templates import (  # noqa: E402
    WORKER_APPWORLD_HIERARCHICAL_VERSION,
)
from agent_orchestrator.runtime.role_templates import (  # noqa: E402
    WORKER_HIERARCHICAL_VERSION,
)
from agent_orchestrator.testing.product_world import product_world  # noqa: E402
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider  # noqa: E402

APPWORLD_TOOLS = (
    "workspace_read_file",
    "workspace_write_file",
    "workspace_list",
    "appworld_execute",
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _attempt_intents(tmp_path, *, domain: str | None, tools):
    """产品同形部署：主循环真跑到第一版计划提交、第一个叶子派发出去（执行者那次调用被扣住），
    返回它建的执行者意图。任务冻结 ``domain``；工具集是部署的（``tools``，建任务请求不能另给）。"""

    async def case():
        provider = LayeredScriptedProvider()
        provider.held.add("worker")
        try:
            # The episode environment an AppWorld deployment binds (a stand-in: no call reaches it).
            episode = {} if domain is None else {"appworld_execute": lambda code: {"stdout": ""}}
            async with product_world(Path(tmp_path) / "root", provider, allowed_tools=tuple(tools),
                                     **CONFIG, **episode) as world:
                request = {"goal": "写一份 NOTES.md，列出三条要点。", "success_criteria": ["file:NOTES.md"],
                           "idempotency_key": f"p23d-worker-{domain}"}
                if domain is not None:
                    request["domain"] = domain
                mission_id = world.create(request)["mission_id"]
                await run_until(world, provider.entered.is_set)
                return [
                    item
                    for item in world.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED")
                    if item.mission_id == mission_id and item.kind == "attempt"
                ]
        finally:
            provider.release.set()

    return asyncio.run(case())


def test_an_appworld_hierarchical_attempt_exposes_appworld_execute(tmp_path) -> None:
    """The one invariant that stops defect D1.

    **Mutation**: put back the unconditional ``return WORKER_HIERARCHICAL`` and the
    tool disappears from the intent, exactly as it did in the evidence pack.
    """

    intents = _attempt_intents(tmp_path, domain=APPWORLD_DOMAIN, tools=APPWORLD_TOOLS)
    assert intents, "a demanded leaf reaches the allocator and gets an intent"
    allowed = tuple(intents[0].config["allowed_tools"])
    assert "appworld_execute" in allowed, allowed


def test_the_prompt_is_the_appworld_hierarchical_one(tmp_path) -> None:
    """Restoring the tool without the words would make the L1 numbers meaningless."""

    intents = _attempt_intents(tmp_path, domain=APPWORLD_DOMAIN, tools=APPWORLD_TOOLS)
    assert intents[0].config["prompt_version"] == WORKER_APPWORLD_HIERARCHICAL_VERSION
    message = intents[0].config.get("message")
    content = str(message.get("content", "") if isinstance(message, dict) else message or "")
    assert "appworld_execute" in content
    # 2026-10-03 A′：上下文的 tools_and_permissions 段列的是任务的整个工具集（部署默认那份，含
    # run_tests 等没开给这一步的工具），不是这一步真拿到的工具——疑似产品缺陷，已报告；这里只看
    # 这一段以外的文字（提示词本身）有没有叫它跑 pytest。
    head, _, rest = content.partition("## tools_and_permissions")
    instructions = head + (rest[rest.find("\n## "):] if "\n## " in rest else "")
    assert "run_tests" not in instructions, (
        "an AppWorld Worker must not be told to run pytest; that is the code domain's "
        "prompt, and reading it while operating a simulated world is defect D1's "
        "collateral damage"
    )
    assert "declared_output_ports" in content, (
        "it is still a hierarchical Worker: decision 4's outputs field is the whole "
        "reason a hierarchical version exists"
    )


def test_a_code_domain_mission_still_gets_the_code_hierarchical_worker(tmp_path) -> None:
    """The other half: the fallback is per domain, not switched off."""

    intents = _attempt_intents(tmp_path, domain=None, tools=APPWORLD_TOOLS)
    assert intents[0].config["prompt_version"] == WORKER_HIERARCHICAL_VERSION


def test_the_appworld_domain_names_a_registered_hierarchical_worker() -> None:
    """Every version the domain table names is one this build actually registers."""

    from agent_orchestrator.governance.domains import (
        APPWORLD_PROFILE,
        HIERARCHICAL_WORKER_TEMPLATES,
    )
    from agent_orchestrator.runtime.role_templates import (
        hierarchical_worker_for_domain,
        hierarchical_worker_versions,
    )

    assert set(HIERARCHICAL_WORKER_TEMPLATES.values()) <= hierarchical_worker_versions()
    named = HIERARCHICAL_WORKER_TEMPLATES[APPWORLD_DOMAIN]
    assert hierarchical_worker_for_domain(APPWORLD_PROFILE).prompt_version == named


def test_a_domain_naming_an_unregistered_version_is_refused(monkeypatch) -> None:
    """A deployment error is refused, not answered with the code-domain prompt."""

    from types import MappingProxyType

    from agent_orchestrator.contracts import ContractError
    from agent_orchestrator.governance import domains
    from agent_orchestrator.runtime.role_templates import hierarchical_worker_for_domain

    monkeypatch.setattr(
        domains,
        "HIERARCHICAL_WORKER_TEMPLATES",
        MappingProxyType({APPWORLD_DOMAIN: "worker-appworld-hierarchical-v99"}),
    )
    with pytest.raises(ContractError, match="unavailable domain prompt"):
        hierarchical_worker_for_domain(domains.APPWORLD_PROFILE)
