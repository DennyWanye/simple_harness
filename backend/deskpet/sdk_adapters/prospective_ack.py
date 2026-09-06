"""Five Context routes share one exact, Host-bound occurrence ACK tool."""
from collections.abc import Mapping

from deskpet.memory.s5c_store import S5cConflict
from deskpet.memory.prospective_occurrence import _key
from deskpet.sdk_adapters.tools import ProductToolRegistration, active_product_tool_context

PROSPECTIVE_ACK_SCHEMA = {
    'type':'object',
    'properties':{'occurrence_key':{'type':'string','pattern':'^[0-9a-f]{64}$'}},
    'required':['occurrence_key'],
    'additionalProperties':False,
}


def prospective_ack_registration(*, coordinator, context_getter=active_product_tool_context):
    async def handler(arguments, _context):
        if not isinstance(arguments,Mapping) or set(arguments)!={'occurrence_key'}:
            raise S5cConflict('s5c_ack_arguments_invalid')
        key=_key(arguments['occurrence_key'])
        context=context_getter()
        # The real SDK ToolContext is carried beside model arguments by the
        # production adapter; read_current authenticates this Run to its owner.
        if context is None or getattr(context,'run_id',None) is None:
            raise S5cConflict('s5c_ack_runtime_context_missing')
        return await coordinator.ack(sdk_run_id=context.run_id.value,occurrence_key=key)
    return ProductToolRegistration(name='prospective_ack',
        description='Acknowledge an exact reminder key presented by Host in this Run. '
                    'Acknowledgement persists a receipt; it does not perform the reminder action.',
        input_schema=PROSPECTIVE_ACK_SCHEMA,handler=handler,dispatch_kind='async',
        permission_category='prospective_ack',projectless_admission='safe',
        metadata={'source':'product-prospective-ack','version':'1','stable_handler_id':'core.prospective_ack.v1'})
