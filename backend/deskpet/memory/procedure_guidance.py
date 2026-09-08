# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Model-facing wording for the Procedure binding contract.

Native r14 (DeepSeek, Host main ``7cec5249``) failed the real-use chain with the
contract working exactly as designed: ``procedure_use`` bound two steps, the
model then sent ``file_write`` with different ``content`` and got
``procedure_call_not_bound_step`` behind the single opaque public string
"Procedure use was rejected before execution.", concluded it should re-bind, and
got ``procedure_same_run_changed_use`` until ``react_max_turns_exceeded``.

Nothing here loosens the contract: the binding is still immutable inside a Run,
a step call must still match the bound ``tool`` plus ``arguments_hash`` exactly
and in order, and no message grants execution. Only the wording changes, so the
model can act on a denial instead of guessing.

Echoing the bound arguments back is not a disclosure: they are the model's own
``steps[].arguments_json`` from this same Run, never memory content, never
another subject's data, and never anything the Run has not already produced.
``procedure_discover`` is the surface with a disclosure invariant (it re-resolves
the disclosure before and after the read); binding echoes carry no such content.
"""
from __future__ import annotations

import json

# The bound arguments can be up to 16 KiB per step. A denial has to stay small
# enough to be worth reading; past this the model is told to copy them from the
# ``procedure_use`` result it already holds.
_MAX_ARGUMENTS_ECHO = 1200
_MAX_MESSAGE = 2400

_FROZEN = "The Procedure binding for this run is frozen and cannot be re-bound."


def bound_step_calls(steps):
    """The exact calls the model must now issue, in order.

    ``arguments`` is the decoded JSON object exactly as bound. Rows written
    before this field existed fall back to the ``arguments_hash`` they carry.
    """
    calls = []
    for ordinal, step in enumerate(steps, 1):
        call = {"ordinal": ordinal, "tool": step["tool"]}
        if "arguments" in step:
            call["arguments"] = step["arguments"]
        else:
            call["arguments_hash"] = step["arguments_hash"]
        calls.append(call)
    return calls


def _arguments_text(call):
    if "arguments" not in call:
        return None
    text = json.dumps(call["arguments"], ensure_ascii=False, sort_keys=True)
    return text if len(text) <= _MAX_ARGUMENTS_ECHO else None


def _step_listing(calls, *, with_arguments):
    parts = []
    for call in calls:
        text = _arguments_text(call) if with_arguments else None
        if text is None:
            parts.append(f"step {call['ordinal']} `{call['tool']}`")
        else:
            parts.append(f"step {call['ordinal']} `{call['tool']}` with arguments {text}")
    return "; ".join(parts)


def bind_next_action(calls):
    """What to do right after a successful bind."""
    return (
        "Binding is frozen for this run. `execution_authorized: false` only means this record "
        "grants no extra permission by itself - you must now issue the bound calls yourself, in "
        f"order: {_step_listing(calls, with_arguments=False)}, each with exactly the arguments "
        "echoed in bound_steps, copied verbatim (one changed character is rejected). "
        "Do not call procedure_use again in this run. Once the last bound step has succeeded the "
        "binding is complete and ordinary tool calls are available again, so you can verify the "
        "result before answering."
    )


def _clamp(message):
    return message if len(message) <= _MAX_MESSAGE else message[: _MAX_MESSAGE - 1] + "…"


def _listing_within_budget(calls, prefix, suffix):
    detailed = f"{prefix}{_step_listing(calls, with_arguments=True)}{suffix}"
    if len(detailed) <= _MAX_MESSAGE:
        return detailed
    return _clamp(f"{prefix}{_step_listing(calls, with_arguments=False)}{suffix}")


def _call_not_bound_step(detail):
    expected = detail["expected"]
    total = detail["total"]
    received = detail.get("received_tool")
    arguments = _arguments_text(expected)
    if arguments is None:
        wanted = (
            "with exactly the arguments you bound for that step (copy them verbatim from the "
            "procedure_use result; do not rewrite them)"
        )
    else:
        wanted = f"with exactly these arguments: {arguments}"
    if received == expected["tool"]:
        sent = "You sent that tool with different arguments."
    else:
        sent = f"You sent `{received}` instead."
    return _clamp(
        f"{_FROZEN} Expected next: step {expected['ordinal']} of {total}, tool "
        f"`{expected['tool']}`, {wanted}. {sent} Re-send the expected call verbatim - "
        "calling procedure_use again in this run is rejected."
    )


def _same_run_changed_use(detail):
    calls = detail.get("bound_steps") or ()
    if not calls:
        return (
            f"{_FROZEN} Call the steps you already bound, exactly as you bound them, instead of "
            "binding again."
        )
    return _listing_within_budget(
        calls,
        "The Procedure binding for this run is immutable and is already set to: ",
        ". Issue those bound calls verbatim instead of binding again.",
    )


def _previous_step_not_successful(detail):
    ordinal, total = detail["failed_ordinal"], detail["total"]
    return (
        f"Step {ordinal} of the {total} bound Procedure steps did not succeed, so no later step "
        f"can run. {_FROZEN} Report the failure to the user instead of retrying or re-binding."
    )


# ``procedure_use_already_complete`` used to live here. Since r15 a binding whose
# last step settled ``succeeded`` simply stops governing the Run, so the code has
# no reachable raise site and the model is never told to stop calling tools.
_DETAILED = {
    "procedure_call_not_bound_step": _call_not_bound_step,
    "procedure_same_run_changed_use": _same_run_changed_use,
    "procedure_previous_step_not_successful": _previous_step_not_successful,
}

# Codes reachable from ``before_call`` that carry no per-step detail.
_STATIC = {
    "procedure_call_replay_changed": (
        "This tool call id was already reserved for a different bound step call. Re-send the "
        f"bound step exactly as bound. {_FROZEN}"
    ),
    "procedure_use_scope_changed": (
        "The TaskScope moved away from the one this Procedure use was bound to, so the bound "
        f"steps can no longer run here. {_FROZEN} Route back with context_route, or finish "
        "without the saved Procedure."
    ),
    "procedure_current_applicability_drift": (
        "A step tool's registered identity changed after the binding, so the bound steps no "
        f"longer apply. {_FROZEN} Continue without the saved Procedure in this run."
    ),
    "procedure_use_target_unavailable": (
        "The bound Procedure record is no longer readable at the revision you bound. "
        f"{_FROZEN} Continue without the saved Procedure in this run."
    ),
    "procedure_use_target_changed": (
        "The bound Procedure record changed after the binding. "
        f"{_FROZEN} Continue without the saved Procedure in this run."
    ),
}


def call_rejection_public_message(error):
    """Public message for a ``ProcedureUseRejected`` raised before a step call.

    The stable ``error_code`` stays ``str(error)``; only this text changes.
    """
    code = str(error).strip() or "procedure_use_rejected"
    detail = getattr(error, "detail", None)
    builder = _DETAILED.get(code)
    if builder is not None and detail is not None:
        try:
            return builder(detail)
        except (KeyError, TypeError, ValueError):
            pass  # Never let wording turn a clean deny into an exception.
    static = _STATIC.get(code)
    if static is not None:
        return static
    return (
        f"Procedure use was rejected before execution ({code}). Do not retry the identical call. "
        f"{_FROZEN}"
    )


def bind_rejection_next_action(error):
    """Detail-driven next action for a rejection raised by ``procedure_use`` itself."""
    code = str(error).strip()
    detail = getattr(error, "detail", None)
    builder = _DETAILED.get(code)
    if builder is None or detail is None:
        return None
    try:
        return builder(detail)
    except (KeyError, TypeError, ValueError):
        return None
