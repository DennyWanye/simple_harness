# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Voice pipeline: VAD → ASR → LLM → TTS, fully streaming."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np
import structlog
from fastapi import WebSocket

from deskpet.session.task_scope import task_session_manager
from deskpet.tools.public_projection import (
    project_public_tool_arguments,
    project_public_tool_result,
)
from observability.metrics import stage_timer
from pipeline.barge_in_filter import BargeInFilter

if TYPE_CHECKING:
    from agent.providers.base import AgentProvider
    from providers.silero_vad import SileroVAD
    from providers.faster_whisper_asr import FasterWhisperASR
    from providers.edge_tts_provider import EdgeTTSProvider

logger = structlog.get_logger()


class RunHandle(Protocol):
    """Structural handle returned by the shared Run API."""

    run_id: str
    events: AsyncIterator[object]

    async def signal(self, signal: Mapping[str, object]) -> object: ...

    async def cancel(self, reason: str) -> object: ...

    async def close(self) -> None: ...


class RunClient(Protocol):
    """Open one product-prepared Run session for the Voice transport."""

    async def start(
        self,
        request: Mapping[str, object],
        host: Mapping[str, object],
    ) -> RunHandle: ...


class RunHostFactory(Protocol):
    async def __call__(self, session_id: str) -> Mapping[str, object]: ...


_TRANSCRIPT_EVENT_KINDS = {"transcript", "assistant_transcript", "assistant_delta", "token"}
_FINAL_EVENT_KINDS = frozenset(
    {
        "final",
        "assistant_final",
        "run_final",
        "terminal",
        "completed",
        "run_completed",
        "failed",
        "failure",
        "run_failed",
        "cancelled",
        "run_cancelled",
    }
)
_SUCCESS_STATUSES = {"succeeded", "completed"}
_FAILURE_STATUSES = {"failed", "cancelled", "unknown"}


def _event_view(event: Any) -> dict[str, object]:
    """Serialize the immutable execution RunEvent without inferring outcome."""

    candidate = event.candidate
    status = getattr(candidate.status, "value", candidate.status)
    view: dict[str, object] = {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "root_run_id": event.root_run_id,
        "session_id": event.session_id,
        "kind": candidate.kind,
        "status": str(status),
        "driver_kind": candidate.driver_kind,
        "correlation": dict(candidate.correlation),
        "payload": dict(candidate.payload),
        "error": dict(candidate.error) if candidate.error is not None else None,
        "artifact_refs": list(candidate.artifact_refs),
    }
    if event.durable_seq is not None:
        view["durable_seq"] = event.durable_seq
    if event.live_cursor is not None:
        view["live_cursor"] = {
            "stream_epoch": event.live_cursor.stream_epoch,
            "live_seq": event.live_cursor.live_seq,
            "schema_version": event.live_cursor.schema_version,
        }
    return view


def _event_text(event: Mapping[str, object]) -> str:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return ""
    for key in ("text", "delta", "content", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _served_by_agent(agent: object) -> str | None:
    """Find the provider that recorded usage through nested agent wrappers."""

    probe = agent
    for _ in range(8):
        if hasattr(probe, "_cloud") or hasattr(probe, "_local"):
            for route in ("cloud", "local"):
                provider = getattr(probe, f"_{route}", None)
                if provider is not None and getattr(provider, "last_usage", None):
                    return route
            return None
        nested = getattr(probe, "_llm", None) or getattr(probe, "_base", None)
        if nested is None or nested is probe:
            return None
        probe = nested
    return None


class VoicePipeline:
    """
    Manages the voice processing flow for a single WebSocket session.

    Audio in → VAD detects speech segments → ASR transcribes →
    Agent generates reply (streaming) → TTS synthesizes → audio streamed back
    and lip-sync parameters sent to the control channel.

    Lifecycle: one instance per audio WebSocket connection.
    """

    def __init__(
        self,
        vad: SileroVAD,
        asr: FasterWhisperASR,
        agent: "AgentProvider",
        tts: EdgeTTSProvider,
        control_ws: WebSocket | None = None,
        session_id: str = "default",
        vad_threshold_during_tts: float = 0.65,
        min_speech_ms_during_tts: int = 400,
        tts_cooldown_ms: int = 300,
        # P4-S21 #13 fix: optional handles for the tool-use path. When all
        # three are present, _process_utterance routes through AgentLoop
        # instead of plain agent.chat_stream — meaning voice input can
        # actually trigger tools (e.g. "make me a todo.txt on the desktop"
        # really creates the file). Backwards-compatible: tests / dev
        # configs that pass nothing keep getting the legacy chat_stream
        # behaviour.
        service_context: object | None = None,
        tool_registry_v2: object | None = None,
        permission_gate_v2: object | None = None,
        local_llm: object | None = None,
        broadcast: object | None = None,
        # WI-11 readiness seam.  Production deliberately leaves this unset
        # until the atomic WI-12 owner cutover; an injected client makes Voice
        # a transport-only adapter over the shared Run API in isolated tests.
        run_client: RunClient | None = None,
        run_session: RunHandle | None = None,
    ):
        self.vad = vad
        self.asr = asr
        self.agent = agent
        self.tts = tts
        self.control_ws = control_ws
        self.session_id = session_id
        self._interrupted = False
        self._processing = False
        self._current_task: asyncio.Task | None = None
        self._barge_in_filter = BargeInFilter(
            cooldown_ms=tts_cooldown_ms,
            min_speech_during_tts_ms=min_speech_ms_during_tts,
        )
        # tool-use plumbing (all None == legacy chat_stream path)
        self._service_context = service_context
        self._tool_registry_v2 = tool_registry_v2
        self._permission_gate_v2 = permission_gate_v2
        self._local_llm = local_llm
        self._run_client = run_client
        self._next_run_session = run_session
        # A VoicePipeline owns at most one active utterance.  These fields are
        # a transport cursor, not a second run registry or lifecycle authority.
        self._current_run_id: str | None = None
        self._current_run_handle: RunHandle | None = None
        # VOICE-MSGPANEL-SYNC: 多窗口广播器（None == legacy / 单测，跳过 fan-out）。
        # 由 main.py audio_channel 注入 _broadcast_default_chat_peers，让语音对话
        # 也能像文字 chat_v2 一样同步到「消息·主线程」消息框窗口。
        self._broadcast = broadcast
        # P2-2-M3: stash the "normal" threshold at init time (from [vad])
        # so we can restore it after TTS / interrupt. The during_tts value
        # is the raised threshold we swap in while TTS is playing — keeps
        # speaker echo from re-triggering VAD.
        self._vad_threshold_normal = getattr(vad, "threshold", 0.5)
        self._vad_threshold_during_tts = vad_threshold_during_tts
        # P2-2-M3: speech_start fires once with duration=0, so we can't gate
        # barge-in on that single event. Instead, re-evaluate every frame
        # while in TTS. This flag ensures we fire tts_barge_in at most once
        # per continuous speech segment (reset on speech_start / speech_end).
        self._barge_in_fired_for_current_speech = False

    def interrupt(self) -> None:
        """User barge-in — stop current TTS generation."""
        self._interrupted = True
        task = self._current_task
        if task and not task.done():
            task.cancel()

    def bind_run_host_factory(self, factory: RunHostFactory) -> None:
        self._run_host_factory = factory

    async def signal_current_run(
        self,
        *,
        decision_id: str,
        response: object,
        nonce: str | None = None,
        version: int | None = None,
    ) -> object:
        """Forward a Voice approval/clarification response to the active Run.

        The RunClient owns authorization and durable decision state.  Voice
        supplies only its authenticated connection/session view and never
        rewrites ``PermissionGate.current_source``.
        """

        handle = self._current_run_handle
        if handle is None:
            raise RuntimeError("voice run is not active")
        decision = str(decision_id).strip()
        if not decision:
            raise ValueError("decision_id must be non-empty")
        signal: dict[str, object] = {
            "decision_id": decision,
            "response": response,
        }
        if nonce is not None:
            signal["nonce"] = nonce
        if version is not None:
            signal["version"] = version
        return await handle.signal(MappingProxyType(signal))

    async def _broadcast_chat_v2(
        self,
        msg_type: str,
        text: str,
        session_id: str | None = None,
    ) -> None:
        """VOICE-MSGPANEL-SYNC: 把一轮语音对话 fan-out 给**其它** control 通道。

        桌宠主窗口和「消息·主线程」消息框是两个独立 Tauri 窗口、各自独立的
        ws。语音原本只经 audio_ws point-to-point 回发起窗口，消息框看不到。
        这里复用文字同款的 broadcaster（_broadcast_default_chat_peers），把
        语音轮次包成 chat_v2_user_echo / chat_v2_final 广播出去。

        originator = self.control_ws（发起语音的主窗口的 control 通道）会被
        broadcaster skip —— 主窗口已通过自己的 audio_ws transcript 显示这轮，
        不重复；消息框窗口的 control 通道则收到 → 补上。

        守卫：未注入 broadcast / 无 control_ws / 非 default 会话时跳过。
        best-effort：广播失败只告警，绝不影响 TTS 主链路。
        """
        # 注：不再依赖 self.control_ws（audio 连接时的快照，backend respawn 后可能
        # None/失效）。originator 由注入的 _voice_broadcast 闭包在广播时实时解析。
        effective_sid = session_id or self.session_id
        if not self._broadcast:
            return
        try:
            await self._broadcast(self.control_ws, {
                "type": msg_type,
                "payload": {"session_id": effective_sid, "text": text},
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("voice_broadcast_failed", msg_type=msg_type, error=str(exc))

    async def process_audio_chunk(self, pcm_bytes: bytes, audio_ws: WebSocket) -> None:
        """
        Process one audio frame, drive the full pipeline.

        Flow:
        1. VAD detects speech_start / speech_end
        2. speech_end → ASR transcription
        3. Transcript → LLM streaming reply
        4. LLM reply → TTS streaming synthesis
        5. TTS audio sent back via audio_ws (binary)
        6. Lip-sync params sent via control_ws (JSON)
        """
        # Debug: log every ~30 chunks (~1 second) with max amplitude
        self._chunk_counter = getattr(self, "_chunk_counter", 0) + 1
        if self._chunk_counter % 30 == 1:
            audio_int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
            max_amp = int(np.abs(audio_int16).max()) if len(audio_int16) else 0
            logger.info(
                "audio_chunk",
                n=self._chunk_counter,
                bytes=len(pcm_bytes),
                max_amp=max_amp,
                silent=max_amp < 200,
            )

        events = self.vad.process_chunk(pcm_bytes)

        for event in events:
            if event["event"] == "speech_start":
                await audio_ws.send_json({
                    "type": "vad_event",
                    "payload": {"status": "speech_start"},
                })
                # New speech segment — rearm the barge-in-once guard.
                self._barge_in_fired_for_current_speech = False
                # NOTE: we deliberately do NOT try to barge-in here.
                # current_speech_duration_ms() is 0 at the speech_start frame
                # (VAD sets _speech_start_ms = _ms_counter before incrementing),
                # so the TTS-phase gate (min_speech_ms_during_tts=400) would
                # never open. The per-frame check below evaluates duration as
                # it grows across subsequent frames.

            elif event["event"] == "speech_end":
                speech_audio = event["audio"]
                await audio_ws.send_json({
                    "type": "vad_event",
                    "payload": {"status": "speech_end"},
                })
                # Speech segment finished — rearm the barge-in guard for the
                # next speech_start. (Also rearmed on speech_start, but cover
                # the edge case where speech_end arrives without a matching
                # speech_start in the same chunk.)
                self._barge_in_fired_for_current_speech = False
                # Cancel any prior in-flight utterance — only one should run
                # at a time. The new task will await the old one's teardown
                # before starting, preventing interleaved ASR/LLM/TTS output.
                prior = self._current_task
                if prior and not prior.done():
                    prior.cancel()
                self._current_task = asyncio.create_task(
                    self._process_utterance(speech_audio, audio_ws, prior)
                )

        # P2-2-M3: per-frame barge-in re-evaluation. speech_start fires only
        # once with duration=0, so min_speech_ms_during_tts would never gate
        # anything if we only checked at speech_start events. Here we check
        # every frame: if TTS is active AND VAD is currently in speech AND
        # we haven't already barged in for this segment, evaluate the filter.
        if self._processing and not self._barge_in_fired_for_current_speech:
            speech_ms = self.vad.current_speech_duration_ms()
            # speech_ms > 0 means the VAD is currently inside a speech segment
            # (it returns 0 outside speech).
            if speech_ms > 0 and self._barge_in_filter.should_allow(speech_ms):
                await audio_ws.send_json({
                    "type": "tts_barge_in",
                    "payload": {"reason": "vad_speech_detected"},
                })
                self._barge_in_fired_for_current_speech = True
                self._barge_in_filter.on_interrupted()
                self.interrupt()

    async def _process_utterance(
        self,
        audio_bytes: bytes,
        audio_ws: WebSocket,
        prior: asyncio.Task | None = None,
    ) -> None:
        """Process a complete speech segment: ASR → LLM → TTS.

        If `prior` is provided, wait for its cancellation to finish before
        starting — keeps only one utterance in flight.
        """
        if prior is not None:
            try:
                await prior
            except (asyncio.CancelledError, Exception):
                pass

        self._interrupted = False
        self._processing = True

        try:
            # Step 1: ASR
            async with stage_timer("asr", session_id=self.session_id):
                text = await self.asr.transcribe(audio_bytes)
            if not text.strip():
                return
            decision = task_session_manager.resolve(
                self.session_id,
                text,
                explicit_new=False,
                force_l2=(text or "").startswith("/continue"),
            )
            effective_sid = decision.effective_sid
            if decision.created:
                sdb = self._service_context.get("session_db") if self._service_context else None
                ensure_session = getattr(sdb, "ensure_session", None)
                if callable(ensure_session):
                    try:
                        await ensure_session(
                            effective_sid,
                            {
                                "origin": "voice_task",
                                "base_session_id": self.session_id,
                                "reason": decision.reason,
                            },
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("voice_session_ensure_failed sid=%s err=%s", effective_sid, exc)
                task_session_manager.remap_peer_group(self.session_id, effective_sid)
            text = decision.stripped_text

            logger.info("user_said", text=text)
            await audio_ws.send_json({
                "type": "transcript",
                "payload": {"text": text, "role": "user"},
            })
            # VOICE-MSGPANEL-SYNC: user 这句同步给其它窗口（消息框），与文字
            # chat_v2_user_echo 同构。
            await self._broadcast_chat_v2(
                "chat_v2_user_echo",
                text,
                session_id=effective_sid,
            )

            # Step 2: execution — injected Run API readiness path, otherwise
            # the sole production legacy owner.
            #
            # WI-11 does not register RunClient in main.py.  The first branch
            # is therefore reachable only from explicit test/bootstrap wiring;
            # existing production still exclusively uses AgentLoop or the
            # older chat_stream fallback until WI-12 atomically cuts ownership.
            response_text = ""
            served_by: str | None = None
            if self._run_client is None and self._next_run_session is None:
                raise RuntimeError("Voice requires the shared Harness Run API")
            response_text, served_by = await self._run_with_client(
                text,
                audio_ws,
                session_id=effective_sid,
            )

            if self._interrupted or not response_text.strip():
                return

            if served_by is None:
                served_by = _served_by_agent(self.agent)

            logger.info("llm_response", text=response_text[:100], served_by=served_by)
            transcript_payload: dict = {"text": response_text, "role": "assistant"}
            if served_by:
                transcript_payload["provider"] = served_by
            await audio_ws.send_json({
                "type": "transcript",
                "payload": transcript_payload,
            })
            # VOICE-MSGPANEL-SYNC: assistant 回复同步给其它窗口（chat_v2_final）。
            await self._broadcast_chat_v2(
                "chat_v2_final",
                response_text,
                session_id=effective_sid,
            )

            # Step 3: TTS (PCM16 24kHz stream via ffmpeg pipe — P2-2-M2)
            # Binary frame layout: 1-byte type header + audio data.
            # 0x01 = PCM16 mono 24kHz (M2 现行)；0x02 = MP3 (M1 历史兼容)。
            chunk_index = 0
            self._barge_in_filter.on_tts_start()
            # P2-2-M3: raise VAD threshold so speaker echo doesn't retrigger
            # speech_start. Restore in the outer finally below.
            try:
                self.vad.set_threshold(self._vad_threshold_during_tts)
            except AttributeError:
                pass  # tests may inject a stub VAD
            async with stage_timer("tts", session_id=self.session_id, chars=len(response_text)):
                async for pcm_chunk in self.tts.synthesize_pcm_stream(response_text):
                    if self._interrupted:
                        logger.info("tts_interrupted")
                        break
                    frame = b"\x01" + pcm_chunk
                    await audio_ws.send_bytes(frame)
                    # Lip-sync：直接读 PCM16 算 RMS，比 MP3 大小启发式精准
                    # 得多。RMS 到 amplitude 的尺度 (÷8000) 按经验调，既能
                    # 让正常语音打到 0.6-0.9，又不让轻声被吞。
                    if self.control_ws:
                        try:
                            pcm_arr = np.frombuffer(pcm_chunk, dtype=np.int16)
                            rms = float(
                                np.sqrt(np.mean(pcm_arr.astype(np.float32) ** 2))
                            )
                            amplitude = min(1.0, rms / 8000.0)
                            await self.control_ws.send_json({
                                "type": "lip_sync",
                                "payload": {
                                    "chunk_index": chunk_index,
                                    "amplitude": amplitude,
                                },
                            })
                        except Exception:
                            pass  # control channel may have disconnected
                    chunk_index += 1
            self._barge_in_filter.on_tts_end()

            # TTS end marker
            await audio_ws.send_json({
                "type": "tts_end",
                "payload": {},
            })
            return response_text

        except asyncio.CancelledError:
            logger.info("utterance_cancelled")
            raise
        except Exception as e:
            logger.error("pipeline_error", error=str(e))
            try:
                await audio_ws.send_json({
                    "type": "error",
                    "payload": {"message": str(e)},
                })
            except Exception:
                pass
        finally:
            # P2-2-M3: always restore the normal VAD threshold — regardless
            # of whether TTS finished naturally, got interrupted, or raised.
            try:
                self.vad.set_threshold(self._vad_threshold_normal)
            except AttributeError:
                pass
            self._processing = False
            if self._current_task is asyncio.current_task():
                self._current_task = None


    async def _run_with_client(
        self,
        text: str,
        audio_ws: WebSocket,
        *,
        session_id: str,
    ) -> tuple[str, str | None]:
        """Execute a transcribed utterance through the shared Run API.

        This adapter consumes typed Run events without reinterpreting tool
        envelopes.  Non-terminal accepted/progress/waiting/tool outcomes are
        forwarded to Voice WebSockets, while only a successful terminal event
        releases assistant text to the existing transcript/TTS transport.
        """

        handle = self._next_run_session
        self._next_run_session = None
        if handle is None:
            client = self._run_client
            if client is None:
                raise RuntimeError("RunClient is not configured")
            request = MappingProxyType({
                "text": text,
                "mode": "companion",
                "canonical_messages": [{"role": "user", "content": text}],
                "proposed_tools": [],
                "payload": {"loop": {"user_request": text, "is_sentinel_run": False}},
            })
            host_factory = getattr(self, "_run_host_factory", None)
            host = (
                await host_factory(session_id)
                if host_factory is not None
                else MappingProxyType({"session_id": session_id, "venue": "voice"})
            )
            handle = await client.start(request, host)
        return await self._consume_run_session(handle, audio_ws, session_id=session_id)

    async def _consume_run_session(
        self,
        handle: RunHandle,
        audio_ws: WebSocket,
        *,
        session_id: str,
    ) -> tuple[str, str | None]:
        """Consume the shared single-presenter session into Voice transport."""

        run_id = str(handle.run_id).strip()
        if not run_id:
            raise RuntimeError("RunClient.start returned a handle without run_id")

        self._current_run_id = run_id
        self._current_run_handle = handle
        transcript_chunks: list[str] = []
        terminal_seen = False
        final_text = ""
        served_by: str | None = None

        try:
            async for raw_event in handle.events:
                event = _event_view(raw_event)
                event_run_id = str(event.get("run_id") or "")
                event_session_id = str(event.get("session_id") or "")
                if event_run_id and event_run_id != run_id:
                    raise RuntimeError(
                        f"voice run event mismatch: expected {run_id}, got {event_run_id}"
                    )
                if event_session_id and event_session_id != session_id:
                    raise RuntimeError(
                        "voice run event crossed session boundary: "
                        f"expected {session_id}, got {event_session_id}"
                    )
                event["run_id"] = run_id
                event["session_id"] = session_id
                await self._emit_run_event(event, audio_ws)

                kind = str(event.get("kind") or "").lower()
                status = str(event.get("status") or "").lower()
                metadata = {**event["correlation"], **event["payload"]}
                served_by = served_by or next(
                    (
                        str(metadata[key]).strip()
                        for key in ("provider", "served_by", "provider_kind")
                        if metadata.get(key)
                    ),
                    None,
                )
                if kind in _TRANSCRIPT_EVENT_KINDS:
                    chunk = _event_text(event)
                    if chunk:
                        transcript_chunks.append(chunk)

                # accepted/waiting/cancel_requested are explicitly
                # non-terminal even if a producer uses a surprising kind.
                if status in {"accepted", "waiting", "cancel_requested"}:
                    continue
                if kind not in _FINAL_EVENT_KINDS and not kind.endswith(".final"):
                    # A failed tool outcome is public failure evidence, not a
                    # failed root Run; keep consuming until the terminal event.
                    continue

                terminal_seen = True
                if status in _SUCCESS_STATUSES:
                    terminal_text = _event_text(event)
                    streamed_text = "".join(transcript_chunks)
                    chunks = (
                        transcript_chunks
                        if streamed_text and terminal_text in {"", streamed_text}
                        else [terminal_text]
                    )
                    final_text = "".join(chunks)
                elif status in _FAILURE_STATUSES:
                    await self._emit_terminal_run_error(event, audio_ws)
                else:
                    await self._emit_terminal_run_error(
                        {
                            **event,
                            "error": {
                                "message": (
                                    "terminal Run event has unsupported status "
                                    f"{status or '<empty>'}"
                                )
                            },
                        },
                        audio_ws,
                    )
                break

            if not terminal_seen:
                await self._emit_terminal_run_error(
                    {
                        "run_id": run_id,
                        "session_id": session_id,
                        "error": {
                            "message": "Run event stream ended without a terminal event"
                        },
                    },
                    audio_ws,
                )
                return "", served_by
            return final_text, served_by
        except asyncio.CancelledError:
            reason = "voice_barge_in" if self._interrupted else "voice_superseded"
            await self._cancel_shared_run(handle, run_id, reason)
            raise
        except Exception:
            await self._cancel_shared_run(
                handle,
                run_id,
                "voice_transport_error",
            )
            raise
        finally:
            try:
                await handle.close()
            except Exception as exc:  # noqa: BLE001 - close is best-effort at transport edge
                logger.warning("voice_run_close_failed", run_id=run_id, error=str(exc))
            if self._current_run_id == run_id:
                self._current_run_id = None
                self._current_run_handle = None

    async def _cancel_shared_run(
        self,
        handle: RunHandle,
        run_id: str,
        reason: str,
    ) -> None:
        try:
            await handle.cancel(reason)
        except Exception as exc:  # noqa: BLE001 - preserve root transport error
            logger.warning(
                "voice_run_cancel_failed", run_id=run_id, reason=reason, error=str(exc)
            )

    async def _emit_run_event(
        self,
        event: Mapping[str, object],
        audio_ws: WebSocket,
    ) -> None:
        """Project one typed Run event to the active Voice transports."""

        frame = {"type": "run_event", "payload": dict(event)}
        await audio_ws.send_json(frame)
        if self.control_ws is not None:
            try:
                await self.control_ws.send_json(frame)
            except Exception:
                pass
        if self._broadcast is not None:
            try:
                await self._broadcast(self.control_ws, frame)
            except Exception as exc:  # noqa: BLE001 - best-effort peer sink
                logger.warning("voice_run_event_broadcast_failed", error=str(exc))

    @staticmethod
    async def _emit_terminal_run_error(
        event: Mapping[str, object],
        audio_ws: WebSocket,
    ) -> None:
        error = event.get("error")
        message = "Run failed"
        if isinstance(error, Mapping):
            candidate = error.get("message") or error.get("code")
            if candidate:
                message = str(candidate)
        await audio_ws.send_json({
            "type": "error",
            "payload": {
                "message": message,
                "run_id": str(event.get("run_id") or ""),
                "status": str(event.get("status") or "failed"),
            },
        })
