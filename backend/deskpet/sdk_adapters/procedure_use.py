"""Explicit structured Procedure use selection; no observation/effect grants."""
from deskpet.sdk_adapters.tools import ProductToolRegistration, active_product_tool_context


SCHEMA = {"type": "object", "required": ["memory_id", "revision", "steps"], "additionalProperties": False,
    "properties": {
        "memory_id": {"type": "string", "maxLength": 1024},
        "revision": {"type": "integer", "minimum": 1},
        "steps": {"type": "array", "minItems": 1, "maxItems": 16, "items": {
            "type": "object", "required": ["text", "tool", "arguments_json"], "additionalProperties": False,
            "properties": {"text": {"type": "string"}, "tool": {"type": "string"},
                           "arguments_json": {"type": "string", "maxLength": 16384,
                               "description": "Exact tool arguments encoded as one JSON object; no duplicate keys or non-finite numbers."}}}},
    }}


def procedure_use_registration(service):
    async def handler(arguments, _context):
        return await service.bind_use(arguments, active_product_tool_context())
    return ProductToolRegistration(name="procedure_use", description=(
        "Before using an exact saved Procedure in the current TaskScope, bind its memory_id/revision "
        "and every verbatim step to one planned tool and exact arguments_json object string, in order. "
        "This only records intended use; every actual tool still requires normal permission. "
        "It grants no execution or successful-observation authority. Never infer success from chat."
    ), input_schema=SCHEMA, handler=handler, dispatch_kind="async", permission_category="procedure_use",
        projectless_admission="safe", metadata={"source": "product-procedure-use", "version": "1",
            "stable_handler_id": "core.procedure_use.v1"})
