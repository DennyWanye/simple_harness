"""Separate draft preview tool; drafts are data and never applicability authority."""
from deskpet.sdk_adapters.tools import ProductToolRegistration, active_product_tool_context

SCHEMA = {"type":"object","additionalProperties":False,"required":["query"],"properties":{
    "query":{"type":"string","minLength":1,"maxLength":512},
    "after":{"type":"string","maxLength":1024,"description":"Omit or leave empty for the first page; otherwise the exact next_after from the previous page."}}}


def procedure_discovery_registration(service):
    async def handler(arguments, _context):
        return await service.discover(arguments, active_product_tool_context())
    return ProductToolRegistration(name="procedure_discover", description=(
        "Find saved Procedures (draft, eligible or adopted/active) before use, by one or two distinctive "
        "words from their name, applicability or steps (not a sentence). Results are candidate instructions "
        "ranked by term hits, not applicable recall and not permission to execute. "
        "After choosing a candidate, bind its exact revision and verbatim steps with procedure_use "
        "in an authorized TaskScope. Read next_after for another bounded page if needed."
    ),input_schema=SCHEMA,handler=handler,dispatch_kind="async",permission_category="procedure_discover",
        projectless_admission="safe",metadata={"source":"product-procedure-discovery","version":"1",
            "stable_handler_id":"core.procedure_discover.v1"})
