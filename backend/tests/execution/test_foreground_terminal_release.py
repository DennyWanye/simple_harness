"""Cold terminal cleanup does not restore authority or mask listener errors."""
from types import SimpleNamespace

import pytest

from deskpet.execution.foreground_runtime_ports import (
    ProductForegroundProviderPort, ProductForegroundToolPort,
)
from deskpet.sdk_adapters.run_bindings import SdkRunBindingRegistry
from deskpet.sdk_adapters.tool_authority import SdkRunToolAuthorityRegistry


def test_cold_terminal_release_has_no_active_authority(tmp_path):
    bindings = SdkRunBindingRegistry()
    calls = []
    provider = ProductForegroundProviderPort(None, SimpleNamespace(
        registry=bindings, mark_terminal=lambda *args: calls.append(args)))
    registry = SdkRunToolAuthorityRegistry()
    tools = ProductForegroundToolPort(tmp_path / 'state.db', catalog={},
        inventory=(), registry=registry)
    for _ in range(2):
        provider.mark_terminal('already-completed', 'completed')
        tools.mark_terminal('already-completed', 'completed')
    assert calls == []
    assert bindings.resolve('already-completed') is None
    with pytest.raises(KeyError):
        registry.resolve('already-completed')


@pytest.mark.parametrize('kind', ['provider', 'tool'])
def test_present_authority_cleanup_error_is_not_absence(tmp_path, kind):
    calls = []
    def release(*args):
        calls.append(args)
        raise KeyError('listener-failed')
    registry = SimpleNamespace(resolve=lambda _: object(), mark_terminal=release)
    if kind == 'provider':
        port = ProductForegroundProviderPort(None, SimpleNamespace(
            registry=registry, mark_terminal=release))
    else:
        port = ProductForegroundToolPort(tmp_path / 'state.db', catalog={},
            inventory=(), registry=registry)
    with pytest.raises(KeyError, match='listener-failed'):
        port.mark_terminal('active', 'completed')
    assert calls == [('active', 'completed')]
