"""Explicit structured Procedure use selection; no observation/effect grants."""
from deskpet.memory.procedure_applicability import ProcedureUseRejected
from deskpet.sdk_adapters.tools import ProductToolRegistration, active_product_tool_context


SCHEMA = {"type": "object", "required": ["memory_id", "revision", "steps"], "additionalProperties": False,
    "properties": {
        "memory_id": {"type": "string", "maxLength": 1024},
        "revision": {"type": "integer", "minimum": 1},
        "steps": {"type": "array", "minItems": 1, "maxItems": 16, "items": {
            "type": "object", "required": ["text", "tool", "arguments_json"], "additionalProperties": False,
            "properties": {"text": {"type": "string"}, "tool": {"type": "string"},
                           "arguments_json": {"type": "string", "maxLength": 16384,
                               "description": "Exact tool arguments encoded as one JSON object; no duplicate keys or non-finite numbers. You must re-send this object verbatim when you call the step tool; it cannot be changed later in this Run."}}}},
    }}

# run-01j C06-14: six consecutive ``procedure_use`` calls came back as the
# frozen SDK default ``tool_handler_failed`` / "Tool execution failed.", because
# ``ProcedureUseRejected`` propagated out of the handler. The rejection reasons
# were real and fixable by the model (five "the step tool is not in this Run",
# one "the step texts are not the saved ones"), but nothing in the tool return
# said so, so it re-sent the same call until the 900s runway nearly ran out.
# Deny stays deny: the handler still binds nothing. Only the wording is added.
_ACTIVATE_FIRST = (
    "Every tool named in steps[].tool must already be active in this Run. "
    "Run tool_search, then tool_describe, then tool_activate for each one, "
    "and only then call procedure_use again with the same steps."
)
_VERBATIM_STEPS = (
    "steps[].text must repeat the saved Procedure's own steps verbatim and in "
    "order, at the exact revision you passed. Re-read them with "
    "procedure_discover and copy them unchanged; do not paraphrase, merge, "
    "reorder or drop a step."
)
_REDISCOVER = (
    "Call procedure_discover again to re-read the Procedure's current "
    "memory_id, revision and steps, then retry with those exact values."
)
# code -> (what to do instead, whether the identical call could ever succeed)
_GUIDANCE = {
    "procedure_tool_unavailable": (_ACTIVATE_FIRST, False),
    "procedure_step_tools_invalid": (
        "steps[].tool must be a plain tool name; pass between 1 and 16 steps.", False),
    "procedure_current_tool_identity_changed": (
        "A step tool's registered identity or schema changed inside this Run. "
        "Re-run tool_describe and tool_activate for that tool, then retry.", False),
    "procedure_use_steps_do_not_match_revision": (_VERBATIM_STEPS, False),
    "procedure_use_steps_invalid": (
        "Each step needs a non-empty text, a tool name, and arguments_json "
        "holding one JSON object.", False),
    "procedure_use_input_invalid": (
        "memory_id must be the exact id from procedure_discover and revision "
        "the integer revision returned beside it.", False),
    "procedure_use_arguments_invalid": (
        "Pass exactly memory_id, revision and steps; no other fields.", False),
    "procedure_same_run_changed_use": (
        "The Procedure binding for this Run is immutable and was already set "
        "by your first procedure_use call. Call the steps you already bound, "
        "with exactly the arguments you bound, instead of binding again.", False),
    "procedure_use_target_unavailable": (_REDISCOVER, True),
    "procedure_use_scope_changed": (_REDISCOVER, True),
    "procedure_use_route_changed": (_REDISCOVER, True),
    "procedure_current_applicability_drift": (_REDISCOVER, True),
    "procedure_current_hazard_drift": (_REDISCOVER, True),
    "procedure_foreign_task_scope": (
        "This Procedure belongs to another TaskScope. Route to the right "
        "TaskScope with context_route first.", False),
}
_FALLBACK = (
    "procedure_use recorded nothing. Do not re-send the identical call; "
    "re-read the Procedure with procedure_discover and correct the arguments, "
    "or continue without binding a saved Procedure."
)


def _rejection(exc: ProcedureUseRejected) -> dict:
    from deskpet.memory.procedure_guidance import bind_rejection_next_action

    code = str(exc).strip() or "procedure_use_rejected"
    guidance, retriable = _GUIDANCE.get(code, (_FALLBACK, False))
    # r14: a deny that can name the already-bound calls says so instead of
    # repeating a generic sentence. Retriability is unchanged either way.
    detailed = bind_rejection_next_action(exc)
    if detailed is not None:
        guidance = detailed
    return {"error": code, "error_code": code,
            "public_message": f"procedure_use was rejected ({code}). {guidance}",
            "retriable": retriable, "replan_required": not retriable,
            "next_action": guidance}


def procedure_use_registration(service):
    async def handler(arguments, _context):
        try:
            return await service.bind_use(arguments, active_product_tool_context())
        except ProcedureUseRejected as exc:
            # Returned, not raised: the SDK maps this shape to a FAILED result
            # carrying the stable code and message, so the deny is unchanged
            # while the model can finally act on it.
            return _rejection(exc)
    return ProductToolRegistration(name="procedure_use", description=(
        "Before using an exact saved Procedure in the current TaskScope, bind its memory_id/revision "
        "and every verbatim step to one planned tool and exact arguments_json object string, in order. "
        "Activate every step tool first (tool_search, tool_describe, tool_activate); a step tool that is "
        "not active in this Run is rejected. "
        "Whatever you put in steps[].arguments_json must then be re-sent VERBATIM as that step tool's "
        "arguments, in order; one changed character is rejected. The binding is frozen for the whole "
        "Run - procedure_use cannot be called again in this Run, so get the arguments right first. "
        "This only records intended use; every actual tool still requires normal permission. "
        "It grants no execution or successful-observation authority. Never infer success from chat."
    ), input_schema=SCHEMA, handler=handler, dispatch_kind="async", permission_category="procedure_use",
        projectless_admission="safe", metadata={"source": "product-procedure-use", "version": "1",
            "stable_handler_id": "core.procedure_use.v1"})
