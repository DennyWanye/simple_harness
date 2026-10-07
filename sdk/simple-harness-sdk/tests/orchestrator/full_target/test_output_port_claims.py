# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""FULL-TARGET P2.3c part 2d, decision 4: the Worker declares its output ports.

Which of an Attempt's files is the ``repository_facts`` a downstream step asked for
is a **local key** — TG design §3.2 gives the model local keys, business goals and
parameters and binds everything else (Mission identity, accounts, scope, execution
generation) itself.  So the port↔artifact pairing is *declared* by the producer and
*checked* here, and the two old fallbacks — "the port name appears somewhere in the
path" and "one port, one artifact, so they go together" — are gone: §10.2 forbids
settling "which file is this" by name or by position.

The tests below are the parser's four structural refusals plus the template
property: the prompt asks the model for the local key and for nothing the system
binds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.runtime.output_blocks import (
    REPAIR_HINTS,
    BlockError,
    PortClaim,
    parse_port_claims,
    repair_hint,
)
from agent_orchestrator.runtime.role_templates import (
    HIERARCHICAL_WORKER_VERSIONS,
    WORKER,
    WORKER_HIERARCHICAL,
    WORKER_HIERARCHICAL_VERSION,
    registered_versions,
)

PORTS = ("repository_facts", "review_notes")
PATHS = ("out/facts.json", "out/notes.md", "out/debug.log")


def test_a_claim_names_a_declared_port_and_a_file_this_attempt_wrote() -> None:
    claims = parse_port_claims(
        {"repository_facts": "out/facts.json"},
        declared_ports=PORTS,
        attempt_paths=PATHS,
    )
    assert claims == (PortClaim(port_key="repository_facts", path="out/facts.json"),)


def test_an_absent_outputs_map_is_no_claim_at_all() -> None:
    """A leaf nothing consumes declares no port and says nothing about outputs."""

    assert parse_port_claims(None, declared_ports=(), attempt_paths=PATHS) == ()


def test_a_claim_for_a_port_the_plan_never_declared_is_refused() -> None:
    """Memo test 2.  A port nobody declared is a claim about a contract that does
    not exist — not a typo for the parser to correct."""

    with pytest.raises(BlockError) as refused:
        parse_port_claims({"summary": "out/facts.json"}, declared_ports=PORTS, attempt_paths=PATHS)
    assert refused.value.reason == "output_port_not_declared"
    assert "summary" in str(refused.value)


def test_a_claim_for_a_file_this_attempt_never_wrote_is_refused() -> None:
    """Memo test 3.  The producer may name which of *its* files went where; it may
    not name a file nobody saw it write."""

    with pytest.raises(BlockError) as refused:
        parse_port_claims(
            {"repository_facts": "out/imagined.json"},
            declared_ports=PORTS,
            attempt_paths=PATHS,
        )
    assert refused.value.reason == "output_path_not_produced"


def test_a_value_that_is_not_a_path_is_refused() -> None:
    with pytest.raises(BlockError) as refused:
        parse_port_claims(
            {"repository_facts": {"path": "out/facts.json", "schema": "x"}},
            declared_ports=PORTS,
            attempt_paths=PATHS,
        )
    assert refused.value.reason == "output_path_not_a_string"


def test_a_model_supplied_schema_ref_is_ignored() -> None:
    """Memo test 9 — decision 4's second mutation self-check.

    ``schema`` is a system-bound field: the schema of an edge comes from the
    ``DataRequirement`` that declares it, never from the producer, or a producer
    could relabel its own output and every downstream compatibility check would be
    made against the label rather than the contract.

    The parser only ever reads "port name → path", so a ``schema`` key can arrive in
    exactly two shapes and both are refused: as a *port* (not declared) or inside a
    value (not a string).

    **Mutation**: teach the codec to read it and hand it to
    ``AcceptedOutput.schema_ref``; ``check_against_ports``'s schema comparison in
    ``test_the_indexed_schema_is_the_edges_and_not_the_producers_claim`` goes red.
    """

    with pytest.raises(BlockError) as as_port:
        parse_port_claims({"schema": "out/facts.json"}, declared_ports=PORTS, attempt_paths=PATHS)
    assert as_port.value.reason == "output_port_not_declared"
    assert PortClaim.__slots__ == ("port_key", "path"), "two fields, and no third"


def test_a_single_valued_port_takes_one_file() -> None:
    """§24.1 decision 3: a single-valued port has exactly one binding.

    A JSON object cannot repeat a key, so the duplicate arrives as a list — which is
    refused for not being a path, with the same effect and a clearer message.
    """

    with pytest.raises(BlockError) as refused:
        parse_port_claims(
            {"repository_facts": ["out/facts.json", "out/notes.md"]},
            declared_ports=PORTS,
            attempt_paths=PATHS,
            single_valued=("repository_facts",),
        )
    assert refused.value.reason == "output_path_not_a_string"


def test_outputs_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(BlockError) as refused:
        parse_port_claims(["out/facts.json"], declared_ports=PORTS, attempt_paths=PATHS)
    assert refused.value.reason == "outputs_not_an_object"


def test_every_refusal_has_a_repair_hint_the_model_can_act_on() -> None:
    """§18.5 C8: a malformed block is repaired on the same Attempt, not relaunched."""

    for reason in (
        "outputs_not_an_object",
        "output_port_not_declared",
        "output_port_claimed_twice",
        "output_path_not_a_string",
        "output_path_not_produced",
    ):
        assert reason in REPAIR_HINTS
        hint = repair_hint(BlockError(reason, "detail"), "result_envelope")
        assert "detail" in hint and hint != reason


# ================================================================== the role template
def test_the_worker_template_never_asks_the_model_for_a_system_bound_field() -> None:
    """Memo test 7.

    The model is asked for the local key and nothing else.  Every field an
    ``AcceptedOutput`` carries beyond that — the acceptance id, the content hash, the
    schema reference, the support revision, the producer result — is bound by the
    system (TG design §3.2, §18.5), and a prompt that asked for one would be inviting
    the model to admit its own work.
    """

    instructions = WORKER_HIERARCHICAL.instructions
    for bound in (
        "acceptance_id",
        "content_hash",
        "schema_ref",
        "support_revision",
        "producer_result_id",
        "producer_occurrence",
    ):
        assert bound not in instructions, bound
    assert '"outputs"' in instructions
    assert "declared_output_ports" in instructions


#: The sha256 of the prompts this slice depends on, as literals: a digest in the test
#: file cannot move with the tree, needs no ``git`` to check, and says exactly which
#: byte changed when it fails.  The DAG-mode templates are here because the
#: hierarchical Worker must not have touched them; the hierarchical Worker's own line
#: is regenerated whenever its (single) prompt is changed.
FROZEN_PROMPT_DIGESTS: dict[str, tuple[str, str]] = {
    # name: (prompt_version, sha256 of instructions)
    "WORKER": ("worker-v3", "c587ce55ff9a01e38ba5b362f8bb9de518b99404f712e63409f871d2d3f0d285"),
    # the hierarchical Worker: one prompt (HTN 精简 片 C); its digest moves with it
    "WORKER_HIERARCHICAL": (
        "worker-hierarchical-v7",
        "13d769418ad407bcd21c531d4f9e8b2aab40ba5e238a97eea2d6f3532064f37a",
    ),
}

#: P2.3d / defect D1: the same freeze for the versions a *domain module* registers.
#: They are not module attributes, so they are looked up in ``TEMPLATE_VERSIONS``.
FROZEN_REGISTERED_DIGESTS: dict[str, str] = {
    # 2026-10-07：基线随 10-03 升版（C3，提交 0f8d8d9f）重生成；v7 / v3 自发布起字节未变
    "worker-appworld-hierarchical-v3": (
        "db03975c5a59f832f7a8e1978a45231dd79594ef29513a8979cbf79c241cdb2e"
    ),
}


@pytest.mark.parametrize("version", sorted(FROZEN_REGISTERED_DIGESTS))
def test_a_registered_domain_prompt_keeps_its_bytes(version: str) -> None:
    import hashlib

    from agent_orchestrator.runtime.role_templates import TEMPLATE_VERSIONS

    template = TEMPLATE_VERSIONS["worker"][version]
    digest = hashlib.sha256(template.instructions.encode("utf-8")).hexdigest()
    assert digest == FROZEN_REGISTERED_DIGESTS[version], (
        f"{version} changed its bytes; an Attempt replays on the prompt it froze, so a "
        "new wording is a new version registered beside this one — never an edit of it"
    )


@pytest.mark.parametrize("name", sorted(FROZEN_PROMPT_DIGESTS))
def test_a_shipped_prompt_keeps_its_bytes(name: str) -> None:
    """Every shipped template still hashes to the digest frozen beside it.

    The hierarchical prompt is a *new version*, not an edit of the shipped ones, and
    this is what says so: each shipped template keeps the bytes it was frozen with.
    """

    import hashlib

    from agent_orchestrator.runtime import role_templates

    template = getattr(role_templates, name)
    version, digest = FROZEN_PROMPT_DIGESTS[name]
    assert template.prompt_version == version
    assert hashlib.sha256(template.instructions.encode("utf-8")).hexdigest() == digest, (
        f"{name} ({version}) changed its bytes; an Attempt replays on the prompt it froze, "
        "so a new wording is a new version registered beside this one — never an edit of it"
    )


def test_the_frozen_digests_cover_the_prompts_this_slice_depends_on() -> None:
    """The hierarchical worker is in the table; so are the DAG-mode templates it must
    not have touched."""

    # CRITIC（旧独立裁判模板）随旧审阅路径删除（删除批三），不再冻结。
    assert {"WORKER", "WORKER_HIERARCHICAL"} <= set(FROZEN_PROMPT_DIGESTS)
    assert FROZEN_PROMPT_DIGESTS["WORKER_HIERARCHICAL"][0] == WORKER_HIERARCHICAL_VERSION
    assert WORKER.prompt_version == "worker-v3"


def test_the_hierarchical_worker_versions_are_the_current_one_and_the_domain_ones() -> None:
    """``_hierarchical_worker_template`` treats "not in this set" as "replace with the
    domain's hierarchical prompt", so a domain's own hierarchical Worker has to be in
    it, and a DAG-mode version must never be."""

    versions = registered_versions()
    assert WORKER_HIERARCHICAL_VERSION in versions["worker"]
    assert HIERARCHICAL_WORKER_VERSIONS == frozenset(
        {
            WORKER_HIERARCHICAL_VERSION,
            "worker-appworld-hierarchical-v3",
            "worker-drone-sim-hierarchical-v3",
        }
    )
    assert "worker-v3" not in HIERARCHICAL_WORKER_VERSIONS, (
        "a DAG-mode pin must not be honoured in the hierarchical mode: worker-v3 "
        "never asks for outputs, and every leaf would then be refused as unclaimed"
    )


def test_a_claim_is_checked_against_the_files_this_envelope_declares() -> None:
    """Part 2d smoke, round 1 — a real defect, kept as a test.

    The first wiring checked the claimed path against ``store.list_artifacts(attempt)``.
    Those rows are written from ``envelope.artifacts`` **after** the result is accepted,
    so at parse time the list is empty and every honest claim was refused with
    ``produced: []``; the real model spent all three attempts being told its own file
    did not exist.  The paths a claim may name are the ones this envelope declares —
    and each of those is separately checked against the real workspace before it
    becomes an artifact, so nothing is taken on trust that was not.
    """

    declared_in_envelope = ("facts.json", "notes.md")
    claims = parse_port_claims(
        {"repository_facts": "facts.json"},
        declared_ports=("repository_facts",),
        attempt_paths=declared_in_envelope,
    )
    assert claims == (PortClaim(port_key="repository_facts", path="facts.json"),)
    with pytest.raises(BlockError) as refused:
        parse_port_claims(
            {"repository_facts": "facts.json"},
            declared_ports=("repository_facts",),
            attempt_paths=(),
        )
    assert refused.value.reason == "output_path_not_produced"


# ======================================================================================
# The two wiring steps decision 4 rests on (third-round review P1-B)
#
# The parsing side, the pairing side and the refusing side all had tests.  The two
# steps *between* them did not, and both mutants survived the whole suite: stop
# handing the model the port names, and stop swapping in the prompt that asks for
# them.  Either one on its own reproduces part 2c's round-9 blocker — every leaf
# refused with ``OUTPUT_PORT_UNCLAIMED`` and the Mission stopped with no dispatchable
# work — so both are asserted here against a real dispatch intent.
# ======================================================================================

import asyncio  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from h1i_seed import committed  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _leaf_intent(tmp_path, *, check=None):
    """产品同形部署：主循环真跑到第一版计划提交、叶子派发出去（执行者那次调用被扣住，模型不必
    回答——测的是接线而不是回复）。返回叶子的执行者意图、它的出现所声明的端口；``check(loop,
    mission)`` 在同一个世界里另做检查。"""

    async def case():
        async with committed(tmp_path, key="p23c-ports") as seed:
            loop, mission = seed.loop, seed.mission
            intents = [
                item
                for item in loop.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED", "SUBMITTED")
                if item.mission_id == mission.id and item.kind == "attempt"
            ]
            assert intents
            # the plan's own answer to "which ports exist" (the accept side checks the same set)
            ports = {item["port"] for item in seed.dispatch.declared_output_ports_for(
                mission.id, str(loop.store.get_attempt(intents[0].subject_id).task_id))}
            if check is not None:
                check(loop, mission)
            return intents, ports

    return asyncio.run(case())


def _content(intent) -> str:
    """The text the model is actually handed for this intent."""

    message = intent.config.get("message")
    if isinstance(message, dict):
        return str(message.get("content", ""))
    return str(message or "")


def _section(message: str, name: str) -> dict:
    """The typed context section the loop sealed into the model's message.

    The message is a rendered package, so the section is found by its heading and
    decoded from the JSON that follows it — which also proves the model is handed the
    ports as *data* with a heading, not as prose.
    """

    heading = f"## {name}\n"
    assert heading in message, name
    tail = message[message.index(heading) + len(heading) :]
    decoder = json.JSONDecoder()
    value, _ = decoder.raw_decode(tail.lstrip())
    return value


def test_the_leaf_is_told_which_output_ports_its_occurrence_declares(tmp_path) -> None:
    """Mutation M18: stop putting ``declared_output_ports`` in the typed context.

    Without it the model is never told a port name exists, so every honest envelope
    omits ``outputs`` and ``accept_review`` refuses the leaf with
    ``OUTPUT_PORT_UNCLAIMED``.  The set is the **plan's**, so it is compared against
    the dispatch's ``declared_output_ports_for`` (the same ``output_ports_in_revision``
    the accept side checks) rather than against a literal.  2026-10-03 A′：产品同形世界的
    一步做法里，收尾步的端口没有下游边、由准则链接声明（缺陷 D3 那一类）。
    """

    intents, ports = _leaf_intent(tmp_path)
    assert intents, "a demanded leaf reaches the allocator and gets an intent"
    message = _content(intents[0])
    assert "declared_output_ports" in message
    section = _section(message, "declared_output_ports")
    assert section["data_not_instruction"] is True
    assert {item["port"] for item in section["ports"]} == set(ports)
    assert set(ports), "the fixture's leaf really does declare a consumed port"


def test_a_dag_mode_pin_does_not_reach_a_hierarchical_leaf(tmp_path) -> None:
    """Mutation M19: stop swapping in the hierarchical Worker template.

    ``worker-v3`` never asks the model which port its files belong to.  A deployment
    that pins it is pinning the *other mode's* prompt, and honouring that pin here is
    the same round-9 blocker by another route.

    2026-10-03 A′：产品同形世界里叶子拿到的是分层执行者；部署钉的 DAG 模式执行者（``WORKER``，
    worker-v3）交给主循环选模板的那一步，换回本领域的分层执行者（不再往策略缓存里注入钉版）。
    """

    from agent_orchestrator.runtime import role_templates

    chosen: dict[str, str] = {}

    def check(loop, mission) -> None:
        chosen["pinned"] = loop._hierarchical_worker_template(role_templates.WORKER, mission.id).prompt_version

    intents, _ = _leaf_intent(tmp_path, check=check)
    assert intents
    assert intents[0].config["prompt_version"] == WORKER_HIERARCHICAL_VERSION
    assert role_templates.WORKER.prompt_version == "worker-v3"
    assert chosen["pinned"] == WORKER_HIERARCHICAL_VERSION


# ======================================================================================
# review P1-2: the prompt a Mission actually gets must agree with D3's rule
# ======================================================================================


def test_no_hierarchical_worker_prompt_makes_the_claim_conditional_on_a_consumer() -> None:
    """The words the Worker reads and the rule Acceptance applies are one rule.

    An earlier wording said a port must be claimed when it is ``required=true`` **and
    has a downstream consumer**.  The finalizer's port has no consumer by construction,
    is declared anyway, and a leaf that skips it is refused with
    ``OUTPUT_PORT_UNCLAIMED``.

    The context package carries no "has a consumer" field either
    (``declared_output_ports_for`` gives port/required/cardinality/schema), so the
    sentence asks the model to apply a test it cannot run.
    """

    from agent_orchestrator.governance.domains import DOMAINS
    from agent_orchestrator.runtime.role_templates import (
        WORKER_HIERARCHICAL,
        hierarchical_worker_for_domain,
    )

    selected = {WORKER_HIERARCHICAL.prompt_version: WORKER_HIERARCHICAL.instructions}
    for profile in DOMAINS.values():
        chosen = hierarchical_worker_for_domain(profile)
        selected[chosen.prompt_version] = chosen.instructions
    offenders = [
        version for version, text in selected.items() if "下游确有消费者" in text
    ]
    assert not offenders, (
        f"{offenders} still condition the claim on a downstream consumer, which D3 "
        "removed from the rule Acceptance applies"
    )
    for version, text in selected.items():
        # The AppWorld text is written without the spaces the code-domain one uses.
        assert "required=true的端口必须被认领" in text.replace(" ", ""), version
