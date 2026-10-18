# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""[voice] 段加载：只剩 enabled 一个开关（旧 /ws/audio 通道已删除）。"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from config import AppConfig, VoiceConfig, load_config


def test_voice_config_defaults_to_disabled() -> None:
    cfg = VoiceConfig()
    assert cfg.enabled is False


def test_app_config_has_voice_field() -> None:
    cfg = AppConfig()
    assert isinstance(cfg.voice, VoiceConfig)


def test_load_config_without_voice_section_uses_defaults(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        dedent(
            """
            schema_version = 1

            [vad]
            threshold = 0.5
            min_speech_ms = 250
            min_silence_ms = 500
            """
        ).strip()
    )
    cfg = load_config(cfg_path)
    assert cfg.voice.enabled is False


def test_load_config_voice_section_overrides_defaults(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        dedent(
            """
            schema_version = 1

            [voice]
            enabled = true
            """
        ).strip()
    )
    cfg = load_config(cfg_path)
    assert cfg.voice.enabled is True


def test_load_config_voice_ignores_unknown_keys(tmp_path: Path) -> None:
    """旧 config.toml 里已删的键（如 tts_cooldown_ms）不影响启动。"""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        dedent(
            """
            [voice]
            enabled = true
            tts_cooldown_ms = 250
            future_experimental_knob = "value"
            """
        ).strip()
    )
    cfg = load_config(cfg_path)
    assert cfg.voice.enabled is True
    assert not hasattr(cfg.voice, "tts_cooldown_ms")
