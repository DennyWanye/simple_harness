"""A non-self disclosure head presents no reminder occurrence; the self lane is unchanged."""
from types import SimpleNamespace

import pytest
import simple_harness_memory as m

from deskpet.memory.prospective_current_reader import PublicOccurrenceCurrentReader
from deskpet.memory.prospective_occurrence import CurrentOccurrenceRead
from deskpet.memory.s5c_store import S5cStore, S5cConflict
from deskpet.memory.s5c_terminal_schema import initialize_s5c_terminal_state_db
from deskpet.memory.trusted_disclosure import TrustedDisclosureError
from tests.memory.test_trusted_disclosure import env, command, ok, selection


class _Runtime:
    def __init__(self, principal):
        self._principal = principal
        self.manager_calls = 0

    def principal(self):
        return self._principal

    async def manager(self):
        self.manager_calls += 1
        raise AssertionError('public inbox must not be read for a non-self audience turn')


@pytest.mark.asyncio
async def test_non_self_head_yields_empty_presentation_and_self_lane_still_resolves(env):
    principal = m.MemoryPrincipal('dep', 'house', env.auth.subject, 'session')
    await initialize_s5c_terminal_state_db(env.path)
    store = S5cStore(env.path, principal)
    runtime = _Runtime(principal)
    reader = PublicOccurrenceCurrentReader(store=store, runtime_getter=lambda: runtime)
    # No configuration yet: the self lane resolves the (missing) run itself, as before.
    with pytest.raises(TrustedDisclosureError):
        await reader(principal=principal, sdk_run_id='product-sdk-' + 'a' * 64)
    ok(await command(env, 'disclosure.configure', selection(
        recipient='external_party', recipient_id='供应商', intended_audience='external')))
    current = await reader(principal=principal, sdk_run_id='product-sdk-' + 'b' * 64)
    assert type(current) is CurrentOccurrenceRead
    assert (current.visible, current.exits, current.unverifiable_keys) == ((), (), ())
    assert current.owner == store.owner and current.sdk_run_id == 'product-sdk-' + 'b' * 64
    current.validate(owner=store.owner, sdk_run_id='product-sdk-' + 'b' * 64)
    assert runtime.manager_calls == 0
    with pytest.raises(S5cConflict, match='principal_differs'):
        await reader(principal=m.MemoryPrincipal('dep', 'house', 'other', 'session'), sdk_run_id='x')
