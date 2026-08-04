# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import builtins
import ast
import sys
from types import ModuleType
from pathlib import Path
from types import SimpleNamespace

from deskpet.voice_runtime import (
    VOICE_DISABLED_CODE,
    configure_legacy_voice_runtime,
)


class _Services:
    def __init__(self) -> None:
        self.values: dict[str, object | None] = {}

    def register(self, name: str, value: object | None) -> None:
        self.values[name] = value


class _Logger:
    def info(self, *_args, **_kwargs) -> None:
        return None

    def warning(self, *_args, **_kwargs) -> None:
        return None


def test_disabled_runtime_does_not_import_heavy_voice_providers(monkeypatch) -> None:
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith("providers.") and any(
            part in name
            for part in ("silero_vad", "faster_whisper_asr", "edge_tts", "cosyvoice")
        ):
            raise AssertionError(f"disabled voice imported {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    services = _Services()
    runtime = configure_legacy_voice_runtime(
        config=SimpleNamespace(voice=SimpleNamespace(enabled=False)),
        service_context=services,
        permission_gate=None,
        logger=_Logger(),
    )

    assert runtime.enabled is False
    assert runtime.health_payload() == {
        "enabled": False,
        "mode": "disabled",
        "reason": VOICE_DISABLED_CODE,
        "realtime": "pending",
    }
    assert services.values == {
        "vad_engine": None,
        "asr_engine": None,
        "tts_engine": None,
    }


def test_main_has_no_eager_voice_provider_imports() -> None:
    main_path = Path(__file__).parents[1] / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not imported.intersection(
        {
            "providers.silero_vad",
            "providers.faster_whisper_asr",
            "providers.edge_tts_provider",
            "providers.cosyvoice_tts",
        }
    )


def test_explicit_enable_builds_legacy_runtime_through_lazy_boundary(
    monkeypatch,
) -> None:
    class FakeVAD:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class FakeASR:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    class FakeTTS:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    def provider_module(name: str, symbol: str, value: object) -> ModuleType:
        module = ModuleType(name)
        setattr(module, symbol, value)
        return module

    monkeypatch.setitem(
        sys.modules,
        "providers.silero_vad",
        provider_module("providers.silero_vad", "SileroVAD", FakeVAD),
    )
    monkeypatch.setitem(
        sys.modules,
        "providers.faster_whisper_asr",
        provider_module(
            "providers.faster_whisper_asr",
            "FasterWhisperASR",
            FakeASR,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "providers.edge_tts_provider",
        provider_module(
            "providers.edge_tts_provider",
            "EdgeTTSProvider",
            FakeTTS,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "providers.cosyvoice_tts",
        provider_module(
            "providers.cosyvoice_tts",
            "CosyVoice2Provider",
            FakeTTS,
        ),
    )

    config = SimpleNamespace(
        voice=SimpleNamespace(enabled=True),
        vad=SimpleNamespace(
            threshold=0.5,
            min_speech_ms=250,
            min_silence_ms=500,
        ),
        asr=SimpleNamespace(
            device="cpu",
            compute_type="int8",
            model="tiny",
            model_dir="asr-model",
            hotwords=[],
        ),
        tts=SimpleNamespace(
            provider="edge-tts",
            voice="test-voice",
            model_dir="tts-model",
        ),
    )
    services = _Services()

    class PermissionGate:
        tts = None

        def set_tts_engine(self, engine) -> None:
            self.tts = engine

    permission_gate = PermissionGate()
    runtime = configure_legacy_voice_runtime(
        config=config,
        service_context=services,
        permission_gate=permission_gate,
        logger=_Logger(),
    )

    assert runtime.enabled is True
    assert runtime.mode == "legacy_local"
    assert isinstance(services.values["vad_engine"], FakeVAD)
    assert isinstance(services.values["asr_engine"], FakeASR)
    assert isinstance(services.values["tts_engine"], FakeTTS)
    assert permission_gate.tts is services.values["tts_engine"]

    session_vad = runtime.create_session_vad()
    assert isinstance(session_vad, FakeVAD)
    assert session_vad is not services.values["vad_engine"]
    assert session_vad.kwargs == {
        "threshold": 0.5,
        "min_speech_ms": 250,
        "min_silence_ms": 500,
    }
