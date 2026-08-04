from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from deskpet.companion.reminder_tools import (
    build_companion_reminder_specs,
    cutover_companion_reminder_tools,
    ensure_companion_reminder_tools,
    register_legacy_reminder_tool,
    restore_durable_companion_reminder_tools,
)
from deskpet.tools.build_identity import (
    authority_handler_ids,
    load_core_handler_authorities,
    validate_core_registry_handler_set,
)
from deskpet.tools.registry import (
    ToolCatalogMutationError,
    ToolRegistry,
)


class _LegacyReminder:
    async def invoke(self, **_kwargs: object) -> str:
        return ""


def _handler(_args: object) -> str:
    return "{}"


def _complete_legacy_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for authority in load_core_handler_authorities():
        if authority.planned or authority.authority_phase not in {"both", "legacy"}:
            continue
        if authority.handler_id == "legacy.list_reminders.v1":
            register_legacy_reminder_tool(registry, _LegacyReminder())
            continue
        registry.register(
            authority.tool_name,
            "core",
            {
                "name": authority.tool_name,
                "description": "manifest fixture",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            _handler,
            source="builtin",
            stable_handler_id=authority.handler_id,
            effect_metadata=authority.effect,
            execution_build_identity=authority.build,
        )
    validate_core_registry_handler_set(
        registry.all_specs(),
        phase="legacy",
        include_planned=False,
    )
    return registry


def _cutover(registry: ToolRegistry):
    return cutover_companion_reminder_tools(
        registry,
        expected_revision=registry.catalog_snapshot().revision,
        service=object(),
        authority_selector=object(),
    )


def test_cutover_retires_legacy_and_publishes_exact_v2_set_once() -> None:
    registry = _complete_legacy_registry()
    before = registry.catalog_snapshot()
    before_names = {spec.name for spec in before.specs}
    assert "list_reminders" in before_names
    assert {
        "reminder_create",
        "reminder_list",
        "reminder_cancel",
    }.isdisjoint(before_names)

    after = _cutover(registry)

    assert registry.authority_phase == "companion"
    assert after.revision == before.revision + 1
    after_names = {spec.name for spec in after.specs}
    assert "list_reminders" not in after_names
    assert {
        "reminder_create",
        "reminder_list",
        "reminder_cancel",
    }.issubset(after_names)
    assert {
        spec.stable_handler_id
        for spec in after.specs
        if spec.source == "builtin" and spec.stable_handler_id
    } == authority_handler_ids(phase="companion", include_planned=False)
    validate_core_registry_handler_set(
        after.specs,
        phase="companion",
        include_planned=False,
    )
    with pytest.raises(ToolCatalogMutationError, match="phase is companion"):
        _cutover(registry)


def test_restart_reconciler_is_idempotent_after_durable_cutover() -> None:
    registry = _complete_legacy_registry()

    first = ensure_companion_reminder_tools(
        registry,
        service=object(),
        authority_selector=object(),
    )
    revision = registry.catalog_snapshot().revision
    second = ensure_companion_reminder_tools(
        registry,
        service=object(),
        authority_selector=object(),
    )

    assert first == second == {
        "authority_phase": "companion",
        "handlers": [
            "reminder_cancel",
            "reminder_create",
            "reminder_list",
        ],
    }
    assert registry.catalog_snapshot().revision == revision


def test_restart_restores_durable_companion_phase_before_harness_catalog() -> None:
    registry = _complete_legacy_registry()
    before = registry.catalog_snapshot()

    restored = restore_durable_companion_reminder_tools(
        registry,
        service=object(),
        authority_selector=object(),
    )

    assert registry.authority_phase == "companion"
    assert restored.revision == before.revision + 1
    assert "list_reminders" not in {spec.name for spec in restored.specs}
    assert {
        "reminder_create",
        "reminder_list",
        "reminder_cancel",
    }.issubset({spec.name for spec in restored.specs})
    validate_core_registry_handler_set(
        restored.specs,
        phase="companion",
        include_planned=False,
    )
    assert ensure_companion_reminder_tools(
        registry,
        service=object(),
        authority_selector=object(),
    ) == {
        "authority_phase": "companion",
        "handlers": [
            "reminder_cancel",
            "reminder_create",
            "reminder_list",
        ],
    }


def test_durable_restore_rejects_non_builtin_replacement_before_mutation() -> None:
    registry = _complete_legacy_registry()
    before = registry.catalog_snapshot()
    replacements = list(build_companion_reminder_specs(object(), object()))
    replacements[0] = replace(replacements[0], source="plugin:spoofed")

    with pytest.raises(
        ToolCatalogMutationError,
        match="replacement metadata mismatch",
    ):
        registry._restore_durable_authority_phase(
            expected_revision=before.revision,
            target_phase="companion",
            replacements=replacements,
        )

    after = registry.catalog_snapshot()
    assert registry.authority_phase == "legacy"
    assert after.revision == before.revision
    assert {spec.name for spec in after.specs} == {
        spec.name for spec in before.specs
    }


def test_cutover_is_atomic_to_concurrent_catalog_readers() -> None:
    registry = _complete_legacy_registry()

    def observe() -> tuple[bool, bool]:
        names = {spec.name for spec in registry.catalog_snapshot().specs}
        legacy = "list_reminders" in names
        companion = {
            "reminder_create",
            "reminder_list",
            "reminder_cancel",
        }.issubset(names)
        return legacy, companion

    observations: list[tuple[bool, bool]] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(
            lambda: [observe() for _ in range(500)]
        )
        _cutover(registry)
        observations.extend(reader.result())
    observations.extend([observe()])
    assert observations
    assert set(observations).issubset({(True, False), (False, True)})
    assert observations[-1] == (False, True)


def test_incomplete_legacy_phase_fails_without_any_catalog_mutation() -> None:
    registry = _complete_legacy_registry()
    before = registry.catalog_snapshot()
    victim = next(
        spec.name
        for spec in before.specs
        if spec.stable_handler_id == "core.memory_recall.v1"
    )
    assert registry.unregister(victim)
    incomplete = registry.catalog_snapshot()

    with pytest.raises(
        ToolCatalogMutationError,
        match="current authority phase is incomplete",
    ):
        _cutover(registry)

    after = registry.catalog_snapshot()
    assert registry.authority_phase == "legacy"
    assert after.revision == incomplete.revision
    assert {
        spec.name for spec in after.specs
    } == {
        spec.name for spec in incomplete.specs
    }
    assert "list_reminders" in {spec.name for spec in after.specs}
    assert {
        "reminder_create",
        "reminder_list",
        "reminder_cancel",
    }.isdisjoint({spec.name for spec in after.specs})
