"""Separate draft preview tool; drafts are data and never applicability authority."""
from deskpet.sdk_adapters.tools import ProductToolRegistration, active_product_tool_context

SCHEMA = {"type":"object","additionalProperties":False,"required":["query","after"],"properties":{
    "query":{"type":"string","minLength":1,"maxLength":512},
    "after":{"type":"string","maxLength":1024,"description":"Empty for first page; otherwise exact next_after from the previous page."}}}


def procedure_discovery_registration(service):
    async def handler(arguments, _context):
        return await service.discover(arguments, active_product_tool_context())
    return ProductToolRegistration(name="procedure_discover", description=(
        "Find draft/eligible saved Procedures before first use, by a substring of their name or steps. "
        "These are unqualified candidate instructions, not applicable recall and not permission. "
        "After choosing a candidate, bind its exact revision and verbatim steps with procedure_use "
        "in an authorized TaskScope. Read next_after for another bounded page if needed."
    ),input_schema=SCHEMA,handler=handler,dispatch_kind="async",permission_category="procedure_discover",
        projectless_admission="safe",metadata={"source":"product-procedure-discovery","version":"1",
            "stable_handler_id":"core.procedure_discover.v1"})
