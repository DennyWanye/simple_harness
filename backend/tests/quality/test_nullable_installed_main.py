"""One installed H0710/M619 main-start/catalog/wire seam, no model dispatch.

Reuse the real main initializer and cleanup control. Do not rerun the four
source controls or substitute a registry/validator/production factory.
"""
import json
import socket
from pathlib import Path

import httpx
import pytest
import simple_harness
import simple_harness.tools.schema as sdk_schema
from simple_harness import RequestId, thaw_json
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import CancelToken, ProviderRequest, ProviderToolSpec, Secret

from deskpet.sdk_adapters.provider import _ProductOpenAICompatibleProvider
from deskpet.sdk_adapters.tools import ProductToolsAdapter
from tests.quality.test_corpus_scoring_initialization import (
    test_actual_main_initialize_close_and_original_input_isolation as _initialize,
)


@pytest.mark.asyncio
async def test_installed_nullable_actual_main_catalog_and_wire(tmp_path, monkeypatch):
    target = Path('/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/2026-09-07/harness0710-artifact/installed').resolve()
    assert simple_harness.__version__ == '0.7.10'
    assert Path(simple_harness.__file__).resolve().is_relative_to(target)
    assert Path(sdk_schema.__file__).resolve().is_relative_to(target)
    captured = []
    original_init = ProductToolsAdapter.__init__
    def capture(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        captured.extend(spec for spec in self.specs if spec.name == 'context_route')
    monkeypatch.setattr(ProductToolsAdapter, '__init__', capture)
    # The reused control executes main's real memory/runtime/ingress activation,
    # denies network and oracle reads, then closes every owned service.
    with monkeypatch.context() as initialization_guard:
        await _initialize(tmp_path, initialization_guard)
    assert captured, 'actual main never registered context_route'
    schemas = [thaw_json(spec.input_schema) for spec in captured]
    for schema in schemas:
        assert schema['required'] == ['route']
        for field in ('reuse_workspace_of', 'expected_source_hash'):
            assert schema['properties'][field]['type'] == ['string', 'null']
    spec = captured[-1]
    schema = schemas[-1]
    proposals = [dict(route='memory_standalone', query='本地组合合同', memory_types=['semantic'],
                      reuse_workspace_of=None, expected_source_hash=None),
                 dict(route='memory_standalone', query='本地组合合同', memory_types=['semantic'],
                      reuse_workspace_of=42, expected_source_hash=None)]
    wires = []
    def deny_network(*args, **kwargs):
        raise AssertionError('network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', deny_network)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny_network)
    def transport(request):
        wires.append(json.loads(request.content))
        n = len(wires) - 1
        assert n < 2
        return httpx.Response(200, json={'id': f'local-{n}', 'model': 'gpt-5.5',
            'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': '',
                'tool_calls': [{'id': f'local-call-{n}', 'type': 'function', 'function': {
                    'name': 'context_route', 'arguments': json.dumps(proposals[n])}}]}}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = _ProductOpenAICompatibleProvider(client, 'https://local.invalid/v1',
            'gpt-5.5', Secret('local-not-a-key'))
        for n, expected in enumerate(proposals):
            response = await provider.invoke(ProviderRequest(RequestId(f'local-{n}'),
                (Message(MessageRole.USER, '本地已安装合同'),),
                tools=(ProviderToolSpec(spec.name, spec.description, schema),)), cancel=CancelToken())
            function = wires[n]['tools'][0]['function']
            assert function['strict'] is False and function['parameters'] == schema
            args = thaw_json(response.tool_calls[0].arguments)
            assert args == expected
            if n == 0:
                sdk_schema.validate_arguments(args, schema)
            else:
                with pytest.raises(sdk_schema.ArgumentsValidationError, match='reuse_workspace_of'):
                    sdk_schema.validate_arguments(args, schema)
    (tmp_path/'installed-nullable.json').write_text(json.dumps({
        'scope': 'actual_main_initialize_close_and_registered_schema_local_wire',
        'sdk_version': simple_harness.__version__, 'sdk_file': simple_harness.__file__,
        'schema_file': sdk_schema.__file__, 'registered_schema_count': len(captured),
        'local_http_calls': len(wires), 'remote_calls': 0, 'tool_dispatches': 0,
    }, indent=2))
