"""启动时记录硬件档位，并按配置装好旧版语音运行时。"""

from __future__ import annotations

from observability.vram import classify_tier

from host.runtime import config, logger, service_context

# S8 (R9): log the current hardware tier once so the dispatch decision is
# visible in the startup banner. The tier itself doesn't force provider
# swaps yet — that's Phase 2 work when we ship multiple LLM/TTS binaries.
_tier = classify_tier()
logger.info(
    "hardware_tier",
    tier=_tier.tier,
    recommended_llm=_tier.llm_model,
    recommended_tts=_tier.tts_model,
    recommended_asr=_tier.asr_model,
)

from deskpet.voice_runtime import configure_legacy_voice_runtime

_voice_runtime = configure_legacy_voice_runtime(
    config=config,
    service_context=service_context,
    permission_gate=globals().get("permission_gate_v2"),
    logger=logger,
)
