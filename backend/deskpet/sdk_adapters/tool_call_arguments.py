# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Host-side memo of the arguments the model issued with each provider tool call.

Why this exists (HM-TO-A6 incident K, 2026-09-08 native runs, DeepSeek
``deepseek-v4-pro``): ``_ProductOpenAICompatibleProvider._wire_messages``
rebuilds an assistant message's ``tool_calls`` from the ``tool`` results that
follow it, because SDK 0.7.1 forbids a durable provider assistant message from
carrying private metadata (``execution/provider_invocations.py``: "stored public
provider message metadata must be empty").  The rebuild recovered the *shape*
(``id`` / ``name``) but filled ``arguments`` with a literal ``"{}"``.

Measured on the durable evidence (``provider_invocations``): ``metadata
[provider_tool_calls]`` survives in **zero** stored requests, so 100% of the
assistant ``tool_calls`` ever put on the wire were the ``{}`` rebuild, and the
count grows monotonically with turn depth.  The model then imitates its own
corrupted transcript — pooled over three native runs, requests with 0 rebuilt
calls emitted 0/16 empty-argument tool calls, while requests with 20+ rebuilt
calls emitted 22/35 (62.9%), a strictly monotone dose-response.  Every such call
is rejected ``missing_required_argument`` until the Run dies with
``react_max_turns_exceeded``.

What this memo stores: exactly the object the Host already keeps in
``metadata[provider_tool_calls]`` for the same turn — the model's own tool-call
arguments *after* ``_extract_public_progress`` removed the Host-internal
narration field.  That is the same object the durable effect ledger records for
the call (``tools/executor.py`` thaws the very same ``ToolCall.arguments`` into
``execution_effects.arguments_json``; only the JSON escaping differs, since the
ledger uses the SDK's ``canonical_json`` and the wire keeps the Host's existing
``ensure_ascii`` serialisation).  Nothing that
is redacted out of a durable record is re-exposed: this content is model
authored, was already sent by the provider that will receive it again, and is
stored raw in the effect ledger anyway.

Process-local and advisory by design.  A miss degrades to the previous
``"{}"`` behaviour (the wire shape stays valid, the Run is not killed) and is
counted, never fixed up with an invention.  Nothing downstream trusts this memo
for authority: the EffectGate, the TaskExecutionEnvelope authority and the SDK
route barrier all still fail closed on their own.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict

#: 事件 AL (2026-09-09, HM-TO-A6 第 13 次整跑 T17): 一次**被拒**的大参数调用要在
#: 历史里付两次钱 —— 拒绝那一轮付一次, 之后每一轮 ``_wire_messages`` 把它原样补
#: 回再付一次。T17 的第一次 ``goal.set`` 逐字带了 26 KB 目标(6256 output token),
#: 因为引用了 route 回执 id 被拒; 重发那一轮 ``floor=27176 > effective=26752``,
#: 死在 ``sdk_provider_wire_input_budget_exceeded`` —— 撑爆窗口的正是那份**已经
#: 作废**的入参。超过这个阈值的被拒入参因此在下一次线上请求里压成短存根。
REJECTED_TOOL_CALL_ARGUMENTS_MAX_BYTES = 2 * 1024
#: 存根里每个字符串保留的前缀长度(字符, 不是字节 —— 中文一个字算一个)。
REJECTED_TOOL_CALL_ARGUMENT_PREFIX_CHARS = 200
#: 拼在被截断字符串后面的那句话。它同时是给模型的下一步: 这份入参**没有**留在
#: 历史里, 要用就得重发完整的。
REJECTED_TOOL_CALL_ARGUMENTS_TRUNCATION_SUFFIX = "…(truncated, resend in full)"
#: 被拒工具结果的 ``outcome``。SDK 的 ToolResult 只有这三种, 成功的入参一个字节
#: 都不动 —— 模型可能还要照着它继续做事。
REJECTED_TOOL_OUTCOMES = frozenset({"rejected", "failed"})

DEFAULT_TOOL_CALL_ARGUMENTS_MEMO_CAPACITY = 4096
DEFAULT_TOOL_CALL_ARGUMENTS_MEMO_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_TOOL_CALL_ARGUMENTS_ENTRY_MAX_BYTES = 256 * 1024
EMPTY_TOOL_CALL_ARGUMENTS_JSON = "{}"


def canonical_tool_arguments_json(arguments: object) -> str:
    """The one serialisation of wire ``function.arguments`` used by the Host.

    Identical to the live-metadata path in
    ``_ProductOpenAICompatibleProvider._message_payload`` (sorted keys, compact
    separators, ``ensure_ascii`` default), so a restored call is byte-identical
    to the same call built from same-process metadata.
    """

    if not isinstance(arguments, dict):
        return EMPTY_TOOL_CALL_ARGUMENTS_JSON
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"))


def rejected_tool_result_reason(content: object) -> str | None:
    """The stable reason code of a rejected/failed tool result, else ``None``.

    Reads only the Host's own tool-result envelope (``outcome`` / ``error_code``),
    which every product Tool result carries verbatim on the wire.  Anything it
    cannot parse is treated as "not a rejection": the arguments then stay
    byte-identical, which is the pre-event-AL behaviour.
    """

    text = _result_text(content)
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    outcome = parsed.get("outcome")
    if not isinstance(outcome, str) or outcome.strip().lower() not in REJECTED_TOOL_OUTCOMES:
        return None
    code = parsed.get("error_code")
    if isinstance(code, str) and code.strip():
        return code.strip()
    return outcome.strip().lower()


def stub_rejected_tool_call_arguments(arguments_json: object) -> str | None:
    """Shrink an oversized **rejected** call's arguments, or ``None`` to keep them.

    Structure is preserved verbatim — every key, every nesting level, every
    short scalar, so ``kind`` / ``operation_id`` / ``base_revision`` and the
    shape of ``operations[]`` still read as the call the model actually made.
    Only long strings lose their tail, and each one says so in place.

    **No foreign key is injected.** The rejection's own reason code sits in the
    ``tool`` message directly below this assistant message, and adding a Host
    key inside a strict, ``additionalProperties: false`` argument object would
    reopen exactly the imitation channel incident K closed: the model copies its
    own transcript, sends the invented key, and every later call dies
    ``task_scope_update_payload_invalid``.
    """

    if not isinstance(arguments_json, str) or not arguments_json:
        return None
    if len(arguments_json.encode("utf-8")) <= REJECTED_TOOL_CALL_ARGUMENTS_MAX_BYTES:
        return None
    try:
        parsed = json.loads(arguments_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    shrunk = _shrink(parsed)
    stub = canonical_tool_arguments_json(shrunk)
    # Never grow the wire: the suffix is longer than the string it replaces when
    # the object is mostly short values that happened to add up.
    if len(stub.encode("utf-8")) >= len(arguments_json.encode("utf-8")):
        return None
    return stub


def _shrink(value: object) -> object:
    if isinstance(value, str):
        if len(value) <= REJECTED_TOOL_CALL_ARGUMENT_PREFIX_CHARS:
            return value
        head = value[:REJECTED_TOOL_CALL_ARGUMENT_PREFIX_CHARS]
        return f"{head}{REJECTED_TOOL_CALL_ARGUMENTS_TRUNCATION_SUFFIX}"
    if isinstance(value, dict):
        return {key: _shrink(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_shrink(item) for item in value]
    return value


def _result_text(content: object) -> str:
    """The tool result body, whether the SDK carried it as text or as blocks."""

    if isinstance(content, str):
        return content.strip()
    if isinstance(content, (list, tuple)):
        parts = [
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("text")
        ]
        return "".join(parts).strip()
    return ""


class ToolCallArgumentsMemo:
    """Arguments per provider tool ``call_id`` (bounded, LRU, poison on conflict).

    **A raw provider ``call_id`` is not globally unique.**  The SDK says so
    itself: ``runtime/drivers/react_loop.py::_internal_effect_identity`` derives
    the internal ``CallId`` by hashing ``{run_id, turn_ordinal,
    raw_provider_call_id, call_ordinal}``, and ``execution_effects`` keeps
    ``raw_call_id`` in its own column precisely because it cannot be the key.
    DeepSeek emits long random ids, but plenty of OpenAI-compatible endpoints
    reachable through the product registry (vLLM, llama.cpp, LM Studio, local
    gateways) emit index-style ids — ``call_0``, ``call_1`` — restarting at every
    turn.

    Attaching turn 2's arguments to turn 1's assistant would be **worse** than
    the ``"{}"`` it replaces: the model would be shown a coherent-looking but
    false "I called ``read_file {"path":"B.md"}``" directly above A.md's
    contents, which is the very imitation channel this memo exists to close.  So
    the memo fails closed on any ambiguity:

    * ``tool_name`` must match on read;
    * re-recording a ``call_id`` with **different** arguments or a different tool
      **poisons** that key — every later ``read`` returns ``None`` and the wire
      degrades to the counted ``"{}"`` fallback.  Re-recording *identical*
      arguments is a no-op, so a protocol resample of the same turn is idempotent
      and stays readable.

    ``_wire_messages`` adds the second half of the guard: a ``call_id`` that
    appears more than once inside one request is ambiguous by construction and
    falls back for every one of its occurrences.
    """

    def __init__(
        self,
        *,
        capacity: int = DEFAULT_TOOL_CALL_ARGUMENTS_MEMO_CAPACITY,
        max_bytes: int = DEFAULT_TOOL_CALL_ARGUMENTS_MEMO_MAX_BYTES,
        entry_max_bytes: int = DEFAULT_TOOL_CALL_ARGUMENTS_ENTRY_MAX_BYTES,
    ) -> None:
        for name, value in (
            ("capacity", capacity),
            ("max bytes", max_bytes),
            ("entry max bytes", entry_max_bytes),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(
                    f"tool call arguments memo {name} must be a positive integer"
                )
        # value: (tool_name, arguments_json) — arguments_json None == poisoned.
        self._entries: OrderedDict[str, tuple[str, str | None]] = OrderedDict()
        self._capacity = capacity
        self._max_bytes = max_bytes
        self._entry_max_bytes = entry_max_bytes
        self._bytes = 0
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def entry_max_bytes(self) -> int:
        return self._entry_max_bytes

    @property
    def recorded_bytes(self) -> int:
        with self._lock:
            return self._bytes

    def record(self, call_id: object, tool_name: object, arguments: object) -> bool:
        """Retain one issued call. Returns whether it is now readable.

        Advisory by contract: this never raises into the Provider path.  A
        rejected or poisoned call simply becomes a counted ``"{}"`` fallback on
        the wire.
        """

        try:
            key = _text(call_id)
            name = _text(tool_name)
            if not key or not name or not isinstance(arguments, dict):
                # Never leave a stale entry standing for a call we could not
                # retain: a later read must not answer with an older turn's
                # arguments. Same direction as the oversized branch below.
                with self._lock:
                    self._poison(key)
                return False
            payload = canonical_tool_arguments_json(arguments)
            size = _entry_bytes(key, name, payload)
            if size > self._entry_max_bytes or size > self._max_bytes:
                # An oversized argument object is not worth evicting the whole
                # memo for; the wire degrades to the counted fallback.
                with self._lock:
                    self._poison(key)
                return False
            with self._lock:
                existing = self._entries.get(key)
                if existing is not None and existing != (name, payload):
                    # Either a poisoned key, or the same raw id reused by a later
                    # turn with different arguments. Both are unresolvable here —
                    # fail closed rather than fabricate a call/result pairing.
                    self._poison(key)
                    return False
                self._drop(key)
                self._entries[key] = (name, payload)
                self._bytes += size
                self._evict_locked()
                return key in self._entries
        except Exception:  # noqa: BLE001 - the memo is advisory, never fatal
            return False

    def read(self, call_id: object, tool_name: object) -> str | None:
        """The canonical ``arguments`` JSON for this call, or ``None``."""

        key = _text(call_id)
        name = _text(tool_name)
        if not key or not name:
            return None
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry[0] != name or entry[1] is None:
                return None
            self._entries.move_to_end(key)
            return entry[1]

    def is_poisoned(self, call_id: object) -> bool:
        with self._lock:
            entry = self._entries.get(_text(call_id))
            return entry is not None and entry[1] is None

    def release_call(self, call_id: object) -> None:
        with self._lock:
            self._drop(call_id)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def _poison(self, key: str) -> None:
        """Make an already-known ``call_id`` unreadable (until eviction).

        Unknown keys are left alone: there is no stale answer to suppress, and a
        rejected record must not spend a slot on a call the memo never held.
        """

        existing = self._entries.get(key) if key else None
        if existing is None:
            return
        name = existing[0]
        self._drop(key)
        self._entries[key] = (name, None)
        self._bytes += _entry_bytes(key, name, None)
        self._evict_locked()

    def _evict_locked(self) -> None:
        while self._entries and (
            len(self._entries) > self._capacity or self._bytes > self._max_bytes
        ):
            oldest, (name, payload) = self._entries.popitem(last=False)
            self._bytes -= _entry_bytes(oldest, name, payload)

    def _drop(self, call_id: object) -> None:
        key = _text(call_id)
        existing = self._entries.pop(key, None)
        if existing is not None:
            self._bytes -= _entry_bytes(key, existing[0], existing[1])

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


_DEFAULT_MEMO = ToolCallArgumentsMemo()


def default_tool_call_arguments_memo() -> ToolCallArgumentsMemo:
    """The process-wide memo the provider adapter uses when none is injected."""

    return _DEFAULT_MEMO


def _entry_bytes(key: str, name: str, payload: str | None) -> int:
    """Every stored byte is accounted, including the tool name."""

    return (
        len(key.encode("utf-8"))
        + len(name.encode("utf-8"))
        + (0 if payload is None else len(payload.encode("utf-8")))
    )


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


__all__ = [
    "DEFAULT_TOOL_CALL_ARGUMENTS_ENTRY_MAX_BYTES",
    "DEFAULT_TOOL_CALL_ARGUMENTS_MEMO_CAPACITY",
    "DEFAULT_TOOL_CALL_ARGUMENTS_MEMO_MAX_BYTES",
    "EMPTY_TOOL_CALL_ARGUMENTS_JSON",
    "REJECTED_TOOL_CALL_ARGUMENTS_MAX_BYTES",
    "REJECTED_TOOL_CALL_ARGUMENTS_TRUNCATION_SUFFIX",
    "REJECTED_TOOL_CALL_ARGUMENT_PREFIX_CHARS",
    "REJECTED_TOOL_OUTCOMES",
    "ToolCallArgumentsMemo",
    "canonical_tool_arguments_json",
    "default_tool_call_arguments_memo",
    "rejected_tool_result_reason",
    "stub_rejected_tool_call_arguments",
]
