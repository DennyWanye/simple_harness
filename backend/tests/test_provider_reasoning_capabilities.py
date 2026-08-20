from llm.provider_capabilities import (
    UNKNOWN_REASONING_CAPABILITY,
    declared_reasoning_capability,
    reasoning_wire_fields,
)


def test_unknown_model_emits_no_private_reasoning_fields() -> None:
    capability = declared_reasoning_capability("relay-mystery-model")
    assert capability is UNKNOWN_REASONING_CAPABILITY
    assert reasoning_wire_fields(
        capability, {"reasoning_mode": "thinking", "effort": "max"}
    ) == {}


def test_deepseek_toggleable_modes_are_explicit() -> None:
    capability = declared_reasoning_capability("deepseek-v4-pro")
    assert reasoning_wire_fields(capability, {"reasoning_mode": "thinking"}) == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }
    assert reasoning_wire_fields(capability, {"reasoning_mode": "fast"}) == {
        "thinking": {"type": "disabled"},
    }


def test_kimi_always_on_fast_lowers_effort_without_disabling() -> None:
    capability = declared_reasoning_capability("kimi-k3")
    assert reasoning_wire_fields(capability, {"reasoning_mode": "fast"}) == {
        "reasoning_effort": "low"
    }


def test_default_never_forces_provider_private_fields() -> None:
    capability = declared_reasoning_capability("kimi-k2.6")
    assert reasoning_wire_fields(capability, {}) == {}
    assert reasoning_wire_fields(capability, {"reasoning_mode": "default"}) == {}
