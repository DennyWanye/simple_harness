"""Setup-only loopback Provider for authored C07 recent messages.

The actual main resolver, ProductProviderAdapter, guard, SDK Run and terminal
sink remain in use. No production adapter monkeypatch, fake terminal or gold.
"""
import asyncio
from hashlib import sha256
import json

from deskpet.quality.corpus_c07 import RECENT_MESSAGES

FIXTURE_PROVIDER_ID = 'corpus-recent-fixture'
SCORING_PROVIDER_ID = 'corpus-real-provider'
_FIXTURE_KEY = 'corpus-local-fixture-not-a-credential'
_BODY_LIMIT = 2 * 1024 * 1024


class RecentMessagesProvider:
    """One bounded local response, derived solely from original recent_messages.

    Only a fresh isolated scoring process is supported. Failed/interrupted setup
    is retained, never silently replayed against a new fixture Provider instance.
    """
    def __init__(self, recent_messages, *, model):
        if recent_messages not in tuple(RECENT_MESSAGES.values()):
            raise ValueError('c07_original_recent_pair_required')
        self.recent_messages, self.model = recent_messages, model
        self._server = None
        self._started = False
        self._tasks = set()
        self.attempts = 0
        self.accepted_request_hash = None
        self.accepted_request = None
        self.base_url = None

    async def start(self):
        if self._started:
            raise ValueError('c07_fixture_provider_cannot_restart')
        self._started = True
        self._server = await asyncio.start_server(self._connected, '127.0.0.1', 0, limit=65536)
        port = self._server.sockets[0].getsockname()[1]
        self.base_url = f'http://127.0.0.1:{port}/v1'
        return self

    def registration(self):
        if self._server is None:
            raise ValueError('c07_fixture_provider_not_listening')
        return dict(id=FIXTURE_PROVIDER_ID, base_url=self.base_url, api_key=_FIXTURE_KEY,
            models=[self.model], priority=2)

    def _connected(self, reader, writer):
        task = asyncio.create_task(self._serve(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _serve(self, reader, writer):
        try:
            async with asyncio.timeout(5):
                header = await reader.readuntil(b'\r\n\r\n')
                self.attempts += 1
                lines = header.decode('ascii').split('\r\n')
                if lines[0] != 'POST /v1/chat/completions HTTP/1.1':
                    raise ValueError('c07_fixture_endpoint_differs')
                headers = {}
                for line in lines[1:]:
                    if not line:
                        continue
                    key, value = line.split(':', 1)
                    if key.lower() in headers:
                        raise ValueError('c07_duplicate_http_header')
                    headers[key.lower()] = value.strip()
                if (headers.get('authorization') != 'Bearer ' + _FIXTURE_KEY
                        or 'transfer-encoding' in headers):
                    raise ValueError('c07_fixture_http_binding_differs')
                length = int(headers['content-length'])
                if not 0 < length <= _BODY_LIMIT:
                    raise ValueError('c07_fixture_http_body_limit')
                body = await reader.readexactly(length)
                payload = json.loads(body)
                if type(payload) is not dict:
                    raise ValueError('c07_fixture_payload_shape')
                messages = payload.get('messages')
                if (self.attempts != 1 or payload.get('model') != self.model
                        or type(messages) is not list
                        or any(type(row) is not dict for row in messages)
                        or [(row.get('role'), row.get('content')) for row in messages
                            if row.get('role') != 'system'] != [self.recent_messages[0]]):
                    raise ValueError('c07_fixture_actual_request_differs')
                # This local fixture has no model tokens or external bill. The
                # SDK's normal frozen estimator is still used; no price bypass.
                self.accepted_request_hash = sha256(body).hexdigest()
                self.accepted_request = payload
                response = dict(id='c07-fixture-response-' + self.accepted_request_hash,
                    object='chat.completion', model=self.model,
                    choices=[dict(index=0, message=dict(role='assistant',
                        content=self.recent_messages[1][1]), finish_reason='stop')],
                    usage=dict(prompt_tokens=0, completion_tokens=0, total_tokens=0))
                await self._reply(writer, 200, response)
        except asyncio.CancelledError:
            raise
        except (ValueError, KeyError, TypeError, UnicodeError, TimeoutError,
                asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            # Do not log headers, credentials, rejected payload or exception text.
            try:
                await self._reply(writer, 409, {'error': {'message': 'c07_fixture_request_rejected'}})
            except (ConnectionError, OSError):
                pass
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    @staticmethod
    async def _reply(writer, status, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
        writer.write(f'HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode() + body)
        await writer.drain()

    async def close(self):
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def verify_completed_phase(self, *, observations, binding):
        trace = observations.get('trace') or {}
        providers = trace.get('providers', ())
        if (self.attempts != 1 or self.accepted_request_hash is None
                or observations.get('observation_errors')
                or trace.get('trace_status') != 'COMPLETE'
                or trace.get('terminal_status') != 'TERMINAL'
                or trace.get('provider_observation_complete') is not True
                or len(providers) != 1
                or providers[0]['state'] != 'succeeded'
                or providers[0]['handed_off_at'] is None
                or providers[0]['handoff_attempt'] != 1
                or providers[0]['rehandoff_count'] != 0
                or binding.provider_id != FIXTURE_PROVIDER_ID
                or binding.run_id != observations.get('sdk_run_id')
                or binding.model_id != self.model
                or (providers[0].get('response_json') or {}).get('provider_request_id')
                    != 'c07-fixture-response-' + self.accepted_request_hash):
            raise ValueError('c07_setup_phase_actual_trace_incomplete')
        return dict(kind='deterministic_authored_recent_messages', provider_id=binding.provider_id,
            sdk_run_id=observations['sdk_run_id'], host_run_id=observations['host_run_id'],
            request_sha256=self.accepted_request_hash,
            provider_invocation_id=providers[0]['invocation_id'],
            sdk_provider_handoffs=sum(item['handed_off_at'] is not None for item in providers),
            sdk_handoff_attempts=providers[0]['handoff_attempt'], fixture_http_requests=self.attempts,
            real_model_calls=0, trace_hash=trace['trace_hash'])


async def execute_recent_phase(*, main, service, runtime, batch, recent_messages,
        worker, directory, provider, collect_turn, record):
    """Capture setup independently, including a failed/nonterminal attempt."""
    from deskpet.quality.corpus_c07_prepare import prepare_c07_recent_group
    from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1
    from deskpet.sdk_adapters.context_route import local_owner_auth
    phase_dir = directory / 'setup-recent'
    phase_dir.mkdir(exist_ok=False)
    observations = None
    phase = dict(kind='deterministic_authored_recent_messages',
        provider_id=FIXTURE_PROVIDER_ID, status='NOT_CONFIRMED', real_model_calls=0)
    try:
        recent = await prepare_c07_recent_group(path=main._state_db_path,
            subject=local_owner_auth().subject, batch=batch, recent_messages=recent_messages,
            service=service, runtime=runtime, ingestion_worker=worker)
        observations = await collect_turn(main, service, local_owner_auth().subject,
            recent.queue_receipt, batch.recent_messages[0][1], directory=phase_dir)
        binding = SdkRunBindingV1.from_record(main._sdk_runtime_stack.read_closure_run_facts(
            observations['sdk_run_id']).binding_record)
        phase.update(provider.verify_completed_phase(observations=observations, binding=binding),
            status='CONFIRMED', evidence_directory='setup-recent')
        return phase
    except BaseException as exc:
        phase['error_type'] = type(exc).__name__
        if observations is None and not isinstance(exc, asyncio.CancelledError):
            # Queue identity is Host-owned; terminal/provider facts come only
            # from the same public SDK observation reader used for scoring.
            turns = (await service.queue_snapshot())['turns']
            exact = [turn for turn in turns
                if turn['delivery_key'] == 'c07-authored-recent:' + batch.manifest_hash]
            if len(exact) == 1:
                try:
                    await collect_turn(main, service, local_owner_auth().subject,
                        exact[0], batch.recent_messages[0][1], directory=phase_dir)
                except Exception as error:
                    phase['observation_error_type'] = type(error).__name__
        raise
    finally:
        phase.update(fixture_http_requests=provider.attempts,
            request_sha256=provider.accepted_request_hash)
        record(phase_dir / 'phase.json', phase)


async def admit_scoring_provider(*, registry, resolver, session_db, base_url, key, model):
    """Add a distinct process-only binding after actual setup terminal cleanup.

    The retired fixture remains resolvable for old audit identity. Priority 1
    makes the real Provider the next Run's first choice; no TOML/keychain writes.
    """
    if resolver.active_provider_ids():
        raise ValueError('c07_setup_provider_still_active')
    entry = await registry.add_ephemeral_provider(dict(id=SCORING_PROVIDER_ID,
        base_url=base_url, api_key=key, models=[model], priority=1))
    if registry.get_chain()[0]['id'] != SCORING_PROVIDER_ID:
        raise ValueError('c07_scoring_provider_not_selected')
    await session_db.reconcile_provider_bindings({item['id']:
        (str(item['incarnation_id']), int(item['config_revision'])) for item in registry.list_providers()},
        registry_digest=registry.snapshot_digest())
    return entry
