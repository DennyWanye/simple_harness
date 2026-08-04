# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Legacy voice runtime boundary.

The current VAD -> ASR -> Harness -> TTS path is intentionally disabled while
the product prepares a Realtime-based replacement.  This module keeps every
heavy voice import behind the persistent ``voice.enabled`` switch so an
ordinary text-only startup does not import or construct voice providers.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


VOICE_DISABLED_CODE = "voice_temporarily_disabled"
VOICE_DISABLED_MESSAGE = "语音功能暂时关闭，后续将接入 Realtime。"


@dataclass(frozen=True)
class LegacyVoiceRuntime:
    enabled: bool
    mode: str
    reason: str
    _vad_type: type[Any] | None = None
    _config: Any | None = None

    def health_payload(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "reason": self.reason,
            "realtime": "pending",
        }

    def create_session_vad(self) -> Any:
        if not self.enabled or self._vad_type is None or self._config is None:
            raise RuntimeError(VOICE_DISABLED_CODE)
        return self._vad_type(
            threshold=self._config.vad.threshold,
            min_speech_ms=self._config.vad.min_speech_ms,
            min_silence_ms=self._config.vad.min_silence_ms,
        )


def configure_legacy_voice_runtime(
    *,
    config: Any,
    service_context: Any,
    permission_gate: Any | None,
    logger: Any,
) -> LegacyVoiceRuntime:
    """Configure the old local voice stack only when explicitly enabled."""

    if not bool(getattr(getattr(config, "voice", None), "enabled", False)):
        for name in ("vad_engine", "asr_engine", "tts_engine"):
            service_context.register(name, None)
        logger.info(
            "legacy_voice_disabled",
            reason=VOICE_DISABLED_CODE,
            realtime="pending",
        )
        return LegacyVoiceRuntime(
            enabled=False,
            mode="disabled",
            reason=VOICE_DISABLED_CODE,
        )

    # Frozen builds must register bundled CUDA DLLs *before* importing torch
    # or ctranslate2 through the provider modules.
    if getattr(sys, "frozen", False):
        try:
            ct2_dir = Path(sys._MEIPASS) / "ctranslate2"  # type: ignore[attr-defined]
            if ct2_dir.is_dir():
                os.add_dll_directory(str(ct2_dir))
                logger.info("cuda_dll_dir_registered", path=str(ct2_dir))
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("cuda_dll_dir_register_failed", error=str(exc))

    # Heavy dependencies stay below the switch.  In particular, importing
    # silero_vad and faster_whisper_asr can load torch / ctranslate2 DLLs.
    from observability.vram import recommend_asr_device
    from paths import resolve_model_dir
    from providers.cosyvoice_tts import CosyVoice2Provider
    from providers.edge_tts_provider import EdgeTTSProvider
    from providers.faster_whisper_asr import FasterWhisperASR
    from providers.silero_vad import SileroVAD

    vad = SileroVAD(
        threshold=config.vad.threshold,
        min_speech_ms=config.vad.min_speech_ms,
        min_silence_ms=config.vad.min_silence_ms,
    )
    if config.asr.device == "auto":
        asr_device, asr_compute = recommend_asr_device()
        logger.info(
            "asr_device_selected",
            device=asr_device,
            compute=asr_compute,
            source="auto",
        )
    else:
        asr_device, asr_compute = config.asr.device, config.asr.compute_type

    asr = FasterWhisperASR(
        model=config.asr.model,
        device=asr_device,
        compute_type=asr_compute,
        local_dir=str(resolve_model_dir(config.asr.model_dir)),
        hotwords=config.asr.hotwords,
    )
    if config.tts.provider == "cosyvoice2":
        tts = CosyVoice2Provider(
            model_dir=str(resolve_model_dir(config.tts.model_dir)),
            fallback_voice=config.tts.voice,
        )
    else:
        tts = EdgeTTSProvider(voice=config.tts.voice)

    service_context.register("vad_engine", vad)
    service_context.register("asr_engine", asr)
    service_context.register("tts_engine", tts)
    if permission_gate is not None:
        permission_gate.set_tts_engine(tts)

    logger.info("legacy_voice_enabled", mode="legacy_local")
    return LegacyVoiceRuntime(
        enabled=True,
        mode="legacy_local",
        reason="explicitly_enabled",
        _vad_type=SileroVAD,
        _config=config,
    )
