from __future__ import annotations

import json
import sys
import time
import types
from dataclasses import asdict
from pathlib import Path

import pytest
import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from deskpet.companion.control_command_canonical import (
    CanonicalCommandError,
    canonical_request_hash,
    credential_signed_payload,
    encode_control_body,
    parse_canonical_json,
    parse_canonical_u64,
)
from deskpet.companion.control_credentials import (
    WindowControlBootstrap,
    WindowControlCredential,
    WindowControlCredentialError,
    WindowControlCredentialVerifier,
    assert_window_scope,
)
from deskpet.companion.control_ingress import (
    CompanionControlIngress,
    CompanionControlIngressError,
    LocalAuthSnapshotProvider,
)
from deskpet.companion.identity import ProfileBindingCoordinator
from deskpet.companion.identity_gate import IdentityReadyGate
from deskpet.companion.store import CompanionStore

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "fixtures"
    / "control-command-canonical-v1.json"
)


def _vectors() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_python_matches_all_shared_canonical_vectors() -> None:
    vectors = _vectors()
    for case in vectors["valid"]:
        body = parse_canonical_json(case["raw_body_json"])
        assert encode_control_body(body).hex() == case["tlv_hex"]
        assert (
            canonical_request_hash(
                case["command_kind"],
                case["request_seq"],
                case["binding_epoch"],
                body,
            )
            == case["request_hash"]
        )
        equivalent = case.get("equivalent_raw_body_json")
        if equivalent:
            assert encode_control_body(parse_canonical_json(equivalent)).hex() == case["tlv_hex"]

    for case in vectors["reject_raw_json"]:
        with pytest.raises(CanonicalCommandError):
            parse_canonical_json(case["raw"])
    for value in vectors["reject_u64"]:
        with pytest.raises(CanonicalCommandError):
            parse_canonical_u64(value, field="fixture")
    for encoded in vectors["reject_utf8_hex"]:
        with pytest.raises(CanonicalCommandError):
            parse_canonical_json(bytes.fromhex(encoded))


def test_python_verifies_shared_ed25519_golden_and_rejects_drift() -> None:
    golden = _vectors()["credential_golden"]
    body = parse_canonical_json(golden["raw_body_json"])
    assert (
        canonical_request_hash(
            golden["command_kind"],
            golden["request_seq"],
            golden["binding_epoch"],
            body,
        )
        == golden["canonical_request_hash"]
    )
    payload = credential_signed_payload(
        backend_process_instance_id=golden["backend_process_instance_id"],
        connection_id=golden["connection_id"],
        control_epoch=golden["control_epoch"],
        window_label=golden["window_label"],
        scope=golden["scope"],
        challenge_hash=golden["challenge_hash"],
        request_seq=golden["request_seq"],
        command_kind=golden["command_kind"],
        canonical_request_hash_hex=golden["canonical_request_hash"],
        nonce=golden["nonce"],
        issued_at=golden["issued_at"],
        expires_at=golden["expires_at"],
    )
    assert payload.hex() == golden["payload_hex"]
    private_key = Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(golden["private_seed_hex"])
    )
    assert private_key.sign(payload).hex() == golden["signature_hex"]

    credential = WindowControlCredential(
        backend_process_instance_id=golden["backend_process_instance_id"],
        connection_id=golden["connection_id"],
        control_epoch=golden["control_epoch"],
        window_label=golden["window_label"],
        scope=golden["scope"],
        challenge_hash=golden["challenge_hash"],
        request_seq=golden["request_seq"],
        command_kind=golden["command_kind"],
        canonical_request_hash=golden["canonical_request_hash"],
        nonce=golden["nonce"],
        issued_at=golden["issued_at"],
        expires_at=golden["expires_at"],
        signature_hex=golden["signature_hex"],
    )
    verifier = WindowControlCredentialVerifier(
        public_key_hex=golden["public_key_hex"],
        backend_process_instance_id=golden["backend_process_instance_id"],
    )
    assert verifier.verify(
        credential,
        expected_connection_id=golden["connection_id"],
        expected_control_epoch=int(golden["control_epoch"]),
        expected_challenge_hash=golden["challenge_hash"],
        expected_window_label="main",
        expected_scope="identity_bind",
        expected_request_seq=int(golden["request_seq"]),
        expected_binding_epoch=int(golden["binding_epoch"]),
        command_kind=golden["command_kind"],
        body=body,
        now=int(golden["issued_at"]),
    ) == golden["canonical_request_hash"]

    with pytest.raises(WindowControlCredentialError):
        verifier.verify(
            credential,
            expected_connection_id=golden["connection_id"],
            expected_control_epoch=int(golden["control_epoch"]),
            expected_challenge_hash=golden["challenge_hash"],
            expected_window_label="main",
            expected_scope="identity_bind",
            expected_request_seq=int(golden["request_seq"]),
            expected_binding_epoch=int(golden["binding_epoch"]),
            command_kind=golden["command_kind"],
            body={"auth_snapshot": {"mode": "local", "user_id": None}},
            now=int(golden["issued_at"]),
        )


def test_window_scope_matrix_is_fail_closed() -> None:
    # 2026-08-04 Workbench 改版断言翻转：companion_action 迁 main，
    # message-panel 窗口已删除（绑定 acceptance「only-add 显式删除例外」）。
    assert_window_scope("main", "identity_bind")
    assert_window_scope("main", "companion_action")
    for label, scope in (
        ("message-panel", "companion_action"),
        ("message-panel", "identity_bind"),
        ("code-panel", "companion_action"),
        ("main", "general"),
    ):
        with pytest.raises(WindowControlCredentialError):
            assert_window_scope(label, scope)


def test_public_bootstrap_parses_without_private_material() -> None:
    golden = _vectors()["credential_golden"]
    bootstrap = WindowControlBootstrap.parse_line(
        "WINDOW_CONTROL_BOOTSTRAP="
        + json.dumps(
            {
                "schema": "window-control-bootstrap-v1",
                "backend_process_instance_id": "process",
                "public_key_hex": golden["public_key_hex"],
            }
        )
    )
    assert bootstrap.backend_process_instance_id == "process"
    assert bootstrap.public_key_hex == golden["public_key_hex"]
    assert golden["private_seed_hex"] not in repr(bootstrap)


def _credential(
    private_key: Ed25519PrivateKey,
    challenge,
    *,
    window_label: str,
    scope: str,
    command_kind: str,
    binding_epoch: int,
    body: object,
    nonce: str,
) -> WindowControlCredential:
    now = int(time.time())
    request_hash = canonical_request_hash(
        command_kind,
        str(challenge.request_seq),
        str(binding_epoch),
        body,
    )
    payload = credential_signed_payload(
        backend_process_instance_id="process-live",
        connection_id=challenge.connection_id,
        control_epoch=str(challenge.control_epoch),
        window_label=window_label,
        scope=scope,
        challenge_hash=challenge.challenge_hash,
        request_seq=str(challenge.request_seq),
        command_kind=command_kind,
        canonical_request_hash_hex=request_hash,
        nonce=nonce,
        issued_at=str(now),
        expires_at=str(now + 30),
    )
    return WindowControlCredential(
        backend_process_instance_id="process-live",
        connection_id=challenge.connection_id,
        control_epoch=str(challenge.control_epoch),
        window_label=window_label,
        scope=scope,
        challenge_hash=challenge.challenge_hash,
        request_seq=str(challenge.request_seq),
        command_kind=command_kind,
        canonical_request_hash=request_hash,
        nonce=nonce,
        issued_at=str(now),
        expires_at=str(now + 30),
        signature_hex=private_key.sign(payload).hex(),
    )


def _ingress(tmp_path):
    class TrustedLocal:
        async def current_snapshot(self):
            return {"mode": "local", "user_id": None}

    private_key = Ed25519PrivateKey.generate()
    public_hex = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    store = CompanionStore(tmp_path / "companion.db")
    gate = IdentityReadyGate()
    ingress = CompanionControlIngress(
        store=store,
        coordinator=ProfileBindingCoordinator(store=store, gate=gate),
        identity_gate=gate,
        verifier=WindowControlCredentialVerifier(
            public_key_hex=public_hex,
            backend_process_instance_id="process-live",
        ),
        user_data_dir=str(tmp_path),
        challenged_quota=3,
        trusted_auth_provider=TrustedLocal(),
    )
    return private_key, store, gate, ingress


@pytest.mark.asyncio
async def test_two_window_leases_are_independent_and_main_reconnect_is_scoped(
    tmp_path,
) -> None:
    private_key, store, gate, ingress = _ingress(tmp_path)
    main = ingress.open_challenge(
        requested_window_label="forged", requested_scope="companion_action"
    )
    snapshot = {"mode": "local", "user_id": None}
    body = {"auth_snapshot": snapshot}
    bind = _credential(
        private_key,
        main,
        window_label="main",
        scope="identity_bind",
        command_kind="companion_profile_bind",
        binding_epoch=main.binding_epoch,
        body=body,
        nonce="main-1",
    )
    bind_ack = await ingress.execute(
        {
            "type": "companion_profile_bind",
            "auth_snapshot": snapshot,
            "credential": asdict(bind),
        },
        challenge=main,
    )
    assert bind_ack["type"] == "companion_profile_bound"
    assert gate.ready is True

    action = ingress.open_challenge(
        requested_window_label="main", requested_scope="identity_bind"
    )
    action_credential = _credential(
        private_key,
        action,
        # Workbench 改版：companion_action 现由主窗（label=main）发起。
        window_label="main",
        scope="companion_action",
        command_kind="companion_action_ready",
        binding_epoch=gate.freeze().binding_epoch,
        body={"ready": True},
        nonce="action-1",
    )
    await ingress.execute(
        {
            "type": "companion_action_ready",
            "ready": True,
            "credential": asdict(action_credential),
        },
        challenge=action,
    )

    main_reconnect = ingress.open_challenge(
        requested_window_label="main", requested_scope="identity_bind"
    )
    bind2 = _credential(
        private_key,
        main_reconnect,
        window_label="main",
        scope="identity_bind",
        command_kind="companion_profile_bind",
        binding_epoch=main_reconnect.binding_epoch,
        body=body,
        nonce="main-2",
    )
    await ingress.execute(
        {
            "type": "companion_profile_bind",
            "auth_snapshot": snapshot,
            "credential": asdict(bind2),
        },
        challenge=main_reconnect,
    )
    # The trusted reconnect revoked only the prior main/identity lease.
    with pytest.raises(Exception, match="control_lease_expired"):
        await ingress.execute(
            {
                "type": "companion_profile_bind",
                "auth_snapshot": snapshot,
                "credential": asdict(bind),
            },
            challenge=main,
        )

    skipped_seq = main_reconnect.advance(
        request_seq=3, binding_epoch=main_reconnect.binding_epoch
    )
    skipped_credential = _credential(
        private_key,
        skipped_seq,
        window_label="main",
        scope="identity_bind",
        command_kind="companion_profile_bind",
        binding_epoch=skipped_seq.binding_epoch,
        body=body,
        nonce="main-seq-3",
    )
    with pytest.raises(
        Exception, match="control_request_seq_not_next"
    ):
        await ingress.execute(
            {
                "type": "companion_profile_bind",
                "payload": {
                    "auth_snapshot": snapshot,
                    "credential": asdict(skipped_credential),
                },
            },
            challenge=skipped_seq,
        )
    with store.read() as db:
        # Workbench 改版后两条活动租约同为 label=main，按 scope 排序区分。
        active = db.execute(
            """SELECT window_label,scope,connection_id
               FROM profile_control_leases WHERE status='active'
               ORDER BY scope"""
        ).fetchall()
        assert [(row["window_label"], row["scope"]) for row in active] == [
            ("main", "companion_action"),
            ("main", "identity_bind"),
        ]
        assert {row["connection_id"] for row in active} == {
            main_reconnect.connection_id,
            action.connection_id,
        }


def test_shared_secret_only_challenges_are_bounded_and_mutate_nothing(
    tmp_path,
) -> None:
    _private_key, store, gate, ingress = _ingress(tmp_path)
    challenges = [
        ingress.open_challenge(
            requested_window_label="main", requested_scope="identity_bind"
        )
        for _ in range(5)
    ]
    assert gate.ready is False
    with store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM profile_bindings").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0
        rows = db.execute(
            """SELECT connection_id,status FROM profile_control_leases
               ORDER BY issued_at,connection_id"""
        ).fetchall()
        assert len(rows) == 3
        assert {row["status"] for row in rows} == {"challenged"}
        assert challenges[-1].connection_id in {
            row["connection_id"] for row in rows
        }


def test_privileged_wire_is_strictly_reparsed_before_json_loses_duplicates() -> None:
    raw = (
        '{"type":"companion_profile_bind","auth_snapshot":'
        '{"mode":"local","mode":"relay","user_id":null},"credential":{}}'
    )
    with pytest.raises(
        CompanionControlIngressError, match="duplicate_object_key"
    ):
        CompanionControlIngress.parse_privileged_frame(raw)


@pytest.mark.asyncio
async def test_wrong_scope_and_stale_binding_epoch_never_mutate(tmp_path) -> None:
    private_key, store, gate, ingress = _ingress(tmp_path)
    challenge = ingress.open_challenge(
        requested_window_label="main", requested_scope="identity_bind"
    )
    snapshot = {"mode": "local", "user_id": None}
    body = {"auth_snapshot": snapshot}
    # Workbench 改版：改用 ("main","companion_action")——仍在白名单内但与
    # 本命令期望的 ("main","identity_bind") 不符，专测 scope 不匹配路径
    # （而非白名单外拒绝，后者由 test_window_scope_matrix 覆盖）。
    wrong = _credential(
        private_key,
        challenge,
        window_label="main",
        scope="companion_action",
        command_kind="companion_profile_bind",
        binding_epoch=challenge.binding_epoch,
        body=body,
        nonce="wrong-scope",
    )
    with pytest.raises(CompanionControlIngressError):
        await ingress.execute(
            {
                "type": "companion_profile_bind",
                "auth_snapshot": snapshot,
                "credential": asdict(wrong),
            },
            challenge=challenge,
        )
    assert gate.ready is False
    with store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0
        assert (
            db.execute(
                "SELECT COUNT(*) FROM profile_control_commands"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.asyncio
async def test_renderer_forged_auth_snapshot_is_rejected_by_trusted_snapshot(
    tmp_path,
) -> None:
    """renderer 自报的身份永远不作数——只认 trusted provider 的快照。

    2026-08-09：relay 移除后 trusted provider 恒返回 local。renderer 若
    伪造一个 relay 身份（或任何与 trusted 快照不符的内容），必须被拒且
    零副作用；这条安全属性与身份来源无关，因此保留并改写。
    """
    private_key, store, gate, ingress = _ingress(tmp_path)

    ingress.trusted_auth_provider = LocalAuthSnapshotProvider()
    challenge = ingress.open_challenge(
        requested_window_label="main", requested_scope="identity_bind"
    )
    forged = {"mode": "relay", "user_id": "relay-forged"}
    body = {"auth_snapshot": forged}
    credential = _credential(
        private_key,
        challenge,
        window_label="main",
        scope="identity_bind",
        command_kind="companion_profile_bind",
        binding_epoch=challenge.binding_epoch,
        body=body,
        nonce="forged-auth-snapshot",
    )
    with pytest.raises(
        CompanionControlIngressError, match="auth_snapshot_mismatch"
    ):
        await ingress.execute(
            {
                "type": "companion_profile_bind",
                "payload": {
                    "auth_snapshot": forged,
                    "credential": asdict(credential),
                },
            },
            challenge=challenge,
        )
    assert gate.ready is False
    with store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 0
        assert (
            db.execute(
                "SELECT COUNT(*) FROM profile_control_commands"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.asyncio
async def test_local_auth_snapshot_is_constant_and_needs_no_remote_call() -> None:
    """LocalAuthSnapshotProvider 恒返回 local，且不依赖任何远端/keychain。

    这是 WBUI-DEF-AUTH-01 的结构性回归门：旧的
    RegistryRelayAuthSnapshotProvider 读 OS keychain 的 relay token 再调
    /v1/me，token 过期即永久卡「正在恢复身份…」。新实现没有任何 I/O，
    因此不存在"过期后不重读"这一类失败。若将来有人再给它接远端依赖，
    这条测试会因为需要 mock transport 而立刻变红。
    """
    provider = LocalAuthSnapshotProvider()
    for _ in range(3):
        assert await provider.current_snapshot() == {
            "mode": "local",
            "user_id": None,
        }
    # 构造器不接受任何依赖注入——没有 registry / token / transport 可传。
    with pytest.raises(TypeError):
        LocalAuthSnapshotProvider(lambda: None)  # type: ignore[call-arg]

