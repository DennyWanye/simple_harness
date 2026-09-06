"""Bounded setup-only HTTP Provider; main resolver/adapter/guard stay intact."""
import asyncio
from dataclasses import dataclass
from hashlib import sha256
import json

from simple_harness import CallId, RequestId, thaw_json
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderTarget
from deskpet.quality.corpus_c05 import operational_text
from deskpet.quality.corpus_c05_prepare import TaskSetupProvider

PROVIDER_ID = 'corpus-task-setup-fixture'
REVISION_PROVIDER_ID = 'corpus-task-revision-fixture'
KEY = 'corpus-task-local-fixture-not-a-credential'


@dataclass(frozen=True)
class _PlannerInput:
    # Local HTTP association only, never claimed to be an SDK Run/request ID.
    request_id: RequestId
    messages: tuple


def _text(content):
    if isinstance(content, str):
        return content
    if content is None:
        return ''
    if isinstance(content, list) and all(isinstance(b, dict) and b.get('type') == 'text'
                                        and isinstance(b.get('text'), str) for b in content):
        return ''.join(b['text'] for b in content)
    raise ValueError('c05_unsupported_wire_content')


def _message(row):
    role = MessageRole(row['role'])
    raw_id = row.get('tool_call_id')
    if role is MessageRole.TOOL:
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise ValueError('c05_tool_wire_call_id_missing')
        call_id = CallId(raw_id)
    else:
        if raw_id is not None:
            raise ValueError('c05_non_tool_wire_call_id')
        call_id = None
    return Message(role, _text(row.get('content')), name=row.get('name'), call_id=call_id)


class TaskSetupHttpProvider:
    def __init__(self, *, model, phase='initial'):
        if phase not in {'initial', 'revision'}:
            raise ValueError('c05_fixture_phase_invalid')
        self.model = model
        self.provider_id = PROVIDER_ID if phase == 'initial' else REVISION_PROVIDER_ID
        self.priority = 2 if phase == 'initial' else 0
        self.target = ProviderTarget(self.provider_id, model, model, 'local', 'fixture')
        self.planner = TaskSetupProvider(target=self.target)
        self._server = None
        self._tasks = set()
        self._lock = asyncio.Lock()
        self._started = False
        self._expected_text = None
        self.attempts = 0
        self.request_hashes = []
        self.planned_calls = {}
        self.queued = None
        self.current_request_hashes = []
        self.current_responses = []

    def arm(self, batch, label, *, revision_of=None):
        if self._lock.locked():
            raise RuntimeError('c05_fixture_request_inflight')
        phase = 'create' if revision_of is None else 'before_selection'
        self.planner.arm(batch, label, revision_of=revision_of)
        self._expected_text = operational_text(batch, label, phase=phase)
        self.current_request_hashes = []
        self.current_responses = []
        self.planned_calls = {}
        self.queued = None

    def bind_queued(self, queued):
        if self.queued is not None or not queued.get('turn_ref'):
            raise ValueError('c05_fixture_queue_binding_differs')
        self.queued = dict(queued)

    @property
    def route_receipt(self):
        return self.planner.route_receipt

    @property
    def marker_result(self):
        return self.planner.marker_result

    async def start(self):
        if self._started:
            raise ValueError('c05_fixture_cannot_restart')
        self._started = True
        self._server = await asyncio.start_server(self._connected, '127.0.0.1', 0, limit=65536)
        self.base_url = f'http://127.0.0.1:{self._server.sockets[0].getsockname()[1]}/v1'
        return self

    def registration(self):
        if self._server is None:
            raise RuntimeError('c05_fixture_not_listening')
        return dict(id=self.provider_id, base_url=self.base_url, api_key=KEY, models=[self.model], priority=self.priority)

    def _connected(self, reader, writer):
        task = asyncio.create_task(self._serve(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _serve(self, reader, writer):
        try:
            async with asyncio.timeout(5):
                header = await reader.readuntil(b'\r\n\r\n')
                lines = header.decode('ascii').split('\r\n')
                if lines[0] != 'POST /v1/chat/completions HTTP/1.1':
                    raise ValueError('c05_fixture_endpoint_differs')
                headers = {}
                for line in lines[1:]:
                    if not line:
                        continue
                    key, value = line.split(':', 1)
                    if key.lower() in headers:
                        raise ValueError('c05_duplicate_header')
                    headers[key.lower()] = value.strip()
                if headers.get('authorization') != 'Bearer ' + KEY or 'transfer-encoding' in headers:
                    raise ValueError('c05_fixture_http_binding_differs')
                length = int(headers['content-length'])
                if not 0 < length <= 2 * 1024 * 1024:
                    raise ValueError('c05_fixture_body_limit')
                raw = await reader.readexactly(length)
                payload = json.loads(raw)
                async with self._lock:
                    if payload.get('model') != self.model or self._expected_text is None or self.queued is None:
                        raise ValueError('c05_fixture_not_armed')
                    rows = payload['messages']
                    messages = tuple(_message(r) for r in rows)
                    users = [m.content for m in messages if m.role is MessageRole.USER]
                    if not users or users[-1] != self._expected_text:
                        raise ValueError('c05_fixture_current_source_differs')
                    self.attempts += 1
                    digest = sha256(raw).hexdigest()
                    self.request_hashes.append(digest)
                    self.current_request_hashes.append(digest)
                    planned = await self.planner.invoke(_PlannerInput(RequestId('c05-http-' + digest), messages),
                                                        cancel=asyncio.Event())
                    calls = [dict(id=call.call_id.value, type='function', function=dict(name=call.name,
                        arguments=json.dumps(thaw_json(call.arguments), ensure_ascii=False))) for call in planned.tool_calls]
                    offered = {s['function']['name'] for s in payload.get('tools', []) if s.get('type') == 'function'}
                    if any(c['function']['name'] not in offered for c in calls):
                        raise ValueError('c05_fixture_tool_not_offered')
                    for call in calls:
                        if call['id'] in self.planned_calls:
                            raise ValueError('c05_fixture_duplicate_call_id')
                        self.planned_calls[call['id']] = dict(name=call['function']['name'],
                            arguments=json.loads(call['function']['arguments']), wire_request_hash=digest,
                            turn_ref=self.queued['turn_ref'])
                    response_message = dict(role='assistant', content=planned.message.content)
                    if calls:
                        response_message['tool_calls'] = calls
                    response = dict(id='c05-fixture-' + digest, object='chat.completion', model=self.model,
                        choices=[dict(index=0, message=response_message, finish_reason='tool_calls' if calls else 'stop')],
                        usage=dict(prompt_tokens=0, completion_tokens=0, total_tokens=0))
                    self.current_responses.append(dict(turn_ref=self.queued['turn_ref'],
                        wire_request_hash=digest, response_id=response['id'],
                        response_body_hash=sha256(json.dumps(response, ensure_ascii=False,
                            separators=(',', ':')).encode()).hexdigest(),
                        call_ids=tuple(c['id'] for c in calls)))
                await self._reply(writer, 200, response)
        except asyncio.CancelledError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeError, TimeoutError,
                asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            try:
                await self._reply(writer, 409, {'error': {'message': 'c05_fixture_request_rejected'}})
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
        self._expected_text = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
