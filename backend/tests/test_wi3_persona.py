from __future__ import annotations

from deskpet.agent.assembler.components.persona import _resolve_persona


def test_legacy_code_mode_config_cannot_select_a_persona() -> None:
    enabled = _resolve_persona(
        {
            "llm": {"model": "test-model", "base_url": "http://example.test"},
            "code_mode": {
                "enabled": True,
                "project_root": "F:/projects/deskpet",
            },
        }
    )
    disabled = _resolve_persona(
        {
            "llm": {"model": "test-model", "base_url": "http://example.test"},
            "code_mode": {
                "enabled": False,
                "project_root": "F:/projects/deskpet",
            },
        }
    )

    assert enabled == disabled
    assert "test-model" in enabled


def test_general_persona_honors_explicit_user_override() -> None:
    persona = _resolve_persona(
        {
            "llm": {"model": "test-model", "base_url": "http://example.test"},
            "agent": {"persona": "A calm general-purpose desktop agent."},
        }
    )

    assert persona.startswith("A calm general-purpose desktop agent.")
    assert "Harness/Kernel" in persona
