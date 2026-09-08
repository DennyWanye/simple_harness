"""Runway-owned signed control connection over the real companion ingress.

The scoring process has no window. This fixture plays the local main window:
it generates its own Ed25519 key, binds the local profile through the actual
`CompanionControlIngress`, and reuses `HumanMemoryControlBinding` so that the
Host disclosure configuration and the declared enqueue run inside a live,
verified request scope. No Host verifier or permission check is bypassed.
"""
from dataclasses import asdict
from pathlib import Path
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from deskpet.companion.control_command_canonical import canonical_request_hash, credential_signed_payload
from deskpet.companion.control_credentials import WindowControlCredential, WindowControlCredentialVerifier
from deskpet.companion.control_ingress import CompanionControlIngress, LocalAuthSnapshotProvider
from deskpet.companion.identity import ProfileBindingCoordinator
from deskpet.companion.identity_gate import IdentityReadyGate
from deskpet.companion.store import CompanionStore
from deskpet.memory.control_binding import HumanMemoryControlBinding

PROCESS_INSTANCE = 'corpus-c12-fixture-control'


class FixtureSignedControl:
    def __init__(self, *, root):
        self.root = Path(root)
        self.binding = HumanMemoryControlBinding()
        self.ingress = self.challenge = self.store = None
        self._key = None

    async def start(self):
        if self.ingress is not None:
            raise ValueError('c12_control_already_started')
        self.root.mkdir(parents=True, exist_ok=False)
        self._key = Ed25519PrivateKey.generate()
        public_hex = self._key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw).hex()
        self.store = CompanionStore(self.root / 'companion.db')
        gate = IdentityReadyGate()
        self.ingress = CompanionControlIngress(store=self.store,
            coordinator=ProfileBindingCoordinator(store=self.store, gate=gate), identity_gate=gate,
            verifier=WindowControlCredentialVerifier(public_key_hex=public_hex,
                backend_process_instance_id=PROCESS_INSTANCE),
            user_data_dir=str(self.root), challenged_quota=3, trusted_auth_provider=LocalAuthSnapshotProvider())
        challenge = self.ingress.open_challenge(requested_window_label='main', requested_scope='identity_bind')
        snapshot = {'mode': 'local', 'user_id': None}
        credential = self._credential(challenge, command_kind='companion_profile_bind',
            binding_epoch=challenge.binding_epoch, body={'auth_snapshot': snapshot})
        result = await self.ingress.execute({'type': 'companion_profile_bind', 'auth_snapshot': snapshot,
            'credential': asdict(credential)}, challenge=challenge)
        self.binding.bound(self.ingress, challenge)
        self.challenge = challenge.advance(request_seq=int(result['payload']['next_request_seq']),
                                           binding_epoch=int(result['payload']['binding_epoch']))
        # Fail closed now if the verified scope cannot be opened for this owner.
        self.binding.authenticate(self.ingress, self.challenge)
        return self

    def _credential(self, challenge, *, command_kind, binding_epoch, body):
        now = int(time.time())
        request_hash = canonical_request_hash(command_kind, str(challenge.request_seq), str(binding_epoch), body)
        common = dict(backend_process_instance_id=PROCESS_INSTANCE, connection_id=challenge.connection_id,
            control_epoch=str(challenge.control_epoch), window_label='main', scope='identity_bind',
            challenge_hash=challenge.challenge_hash, request_seq=str(challenge.request_seq),
            command_kind=command_kind, nonce=challenge.connection_id, issued_at=str(now), expires_at=str(now + 30))
        payload = credential_signed_payload(canonical_request_hash_hex=request_hash, **common)
        return WindowControlCredential(canonical_request_hash=request_hash,
            signature_hex=self._key.sign(payload).hex(), **common)

    def auth(self):
        return self.binding.authenticate(self.ingress, self.challenge)

    def request_scope(self):
        """Live verified scope; required by every signed-control Host write."""
        return self.binding.request_scope(self.ingress, self.challenge)

    async def configure_disclosure(self, service, *, request_id, selection):
        with self.request_scope():
            current = (await service.current_disclosure_configuration())['configuration']
            expected = None if current is None else current['binding_ref']
            return await service.configure_disclosure(request_id=request_id, expected_ref=expected,
                                                      selection=dict(selection))

    def receipt(self):
        identity = self.ingress.identity_gate.freeze()
        return dict(process_instance=PROCESS_INSTANCE, connection_id=self.challenge.connection_id,
            binding_epoch=identity.binding_epoch, challenge_hash=self.challenge.challenge_hash,
            store=str(self.store.path) if hasattr(self.store, 'path') else str(self.root / 'companion.db'))

    def close(self):
        self.binding.clear()
        close = getattr(self.store, 'close', None)
        if close is not None:
            close()
