# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""``python -m agent_orchestrator`` — the operator CLI (ORCH-BUILD §14.3).

Subcommands:

    mission create --tenant T --evidence-dir DIR --spec spec.json [--provider ...]
    mission get|cancel|events --evidence-dir DIR MISSION_ID
    attempt get --evidence-dir DIR ATTEMPT_ID
    artifact show --evidence-dir DIR ARTIFACT_ID
    approval list|approve|reject|revoke|comment|review|arbitrate|takeover|resolve --evidence-dir DIR --as PRINCIPAL ...
    replay --evidence-dir DIR MISSION_ID [--events FILE] [--failures] [--attribution] [--out FILE]
    policy list|show|status --evidence-dir DIR

``replay`` (step 8) rebuilds a Mission's formal state from its events on a read-only copy
of the library and compares it with the library; it never executes or writes.

``approval`` (step 7) acts as the caller named by ``--as`` — in this local build a
self-declared identity; a real deployment binds it to its authentication.

``mission create`` only creates the Mission (a Mission is run by a deployment that has
the hierarchical assembly installed, such as the desktop Host).  ``--provider`` names the
provider the Mission's policy binding records: ``fixtures`` or ``env`` (``SH_BASEURL`` /
``SH_APIKEY`` / ``SH_MODEL`` and optional ``SH_PRICE_INPUT_MICROS`` /
``SH_PRICE_OUTPUT_MICROS`` per million tokens, read from the environment; the key never
reaches any file).  The flat-mode ``demo`` scenarios were removed on 2026-10-02.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .orchestrator.commit_service import CommitService
from .orchestrator.event_handler import Orchestrator
from .runtime.assembly import OrchestratorConfig, PriceTable
from .storage.store import Store

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


def _print(value: Any) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _config(
    args: argparse.Namespace, *, model: str | None, price: PriceTable | None
) -> OrchestratorConfig:
    return OrchestratorConfig(
        evidence_root=Path(args.evidence_dir).resolve(),
        model=model or "agent-model",
        price_table=price,
        hard_cap_micros=getattr(args, "hard_cap_micros", None),
        max_concurrency=getattr(args, "max_concurrency", 1),
        test_timeout_seconds=getattr(args, "test_timeout", 120.0),
    )


def _provider(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    """Return (provider, model, price_table, provider_kind)."""

    if args.provider == "fixtures":
        from .testing.fixtures import RoleScriptedProvider

        return RoleScriptedProvider({}), "agent-model", None, "fixtures"
    if args.provider == "env":
        base_url = os.environ.get("SH_BASEURL")
        api_key = os.environ.get("SH_APIKEY")
        model = os.environ.get("SH_MODEL")
        if not (base_url and api_key and model):
            raise SystemExit("--provider env needs SH_BASEURL, SH_APIKEY and SH_MODEL")
        import httpx

        from simple_harness.providers import OpenAICompatibleProvider, Secret

        provider = OpenAICompatibleProvider(
            httpx.AsyncClient(), base_url, model, Secret(api_key), timeout=180.0
        )
        price = None
        if os.environ.get("SH_PRICE_INPUT_MICROS") and os.environ.get("SH_PRICE_OUTPUT_MICROS"):
            price = PriceTable(
                snapshot_id=f"env-{model}",
                input_micros_per_million_tokens=int(os.environ["SH_PRICE_INPUT_MICROS"]),
                output_micros_per_million_tokens=int(os.environ["SH_PRICE_OUTPUT_MICROS"]),
            )
        elif not getattr(args, "unpriced", False):
            raise SystemExit(
                "--provider env is a paid provider: set SH_PRICE_INPUT_MICROS/SH_PRICE_OUTPUT_MICROS"
                " (micros per million tokens) or pass --unpriced to record costs as unpriced"
            )
        return provider, model, price, "env"
    raise SystemExit(f"unknown provider {args.provider!r}")


def _open_store(args: argparse.Namespace) -> Store:
    return Store.open(Path(args.evidence_dir).resolve() / "orchestrator.db")


# ------------------------------------------------------------------ commands
def cmd_mission(args: argparse.Namespace) -> int:
    if args.action == "create":
        spec_data = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        provider, model, price, kind = _provider(args)

        async def run() -> int:
            async with Orchestrator(
                _config(args, model=model, price=price), provider
            ) as orchestrator:
                from .api.missions import MissionApi

                mission, created = MissionApi(  # host support 0.9.8: the deployment-aware door
                    orchestrator.commit, orchestrator=orchestrator
                ).create(tenant_id=args.tenant, request=spec_data)
                _print(
                    {"mission_id": mission.id, "created": created, "status": str(mission.status)}
                )
            return EXIT_OK

        return asyncio.run(run())
    store = _open_store(args)
    try:
        if args.action == "get":
            _print(store.snapshot(args.mission_id))
        elif args.action == "events":
            _print([event.to_json() for event in store.list_events(args.mission_id)])
        elif args.action == "cancel":
            mission = CommitService(store).cancel_mission(args.mission_id)
            _print({"mission_id": mission.id, "status": str(mission.status)})
    finally:
        store.close()
    return EXIT_OK


def cmd_attempt(args: argparse.Namespace) -> int:
    store = _open_store(args)
    try:
        attempt = store.get_attempt(args.attempt_id)
        if attempt is None:
            _print({"error": "unknown attempt"})
            return EXIT_FAILED
        stored = store.find_result_for_attempt(attempt.id)
        _print(
            {
                "attempt": attempt.to_json(),
                "intent": (lambda i: None if i is None else i.to_json())(
                    store.get_intent_for_subject(attempt.id)
                ),
                "result": None
                if stored is None
                else {
                    **stored.to_json(),
                    "verifications": store.list_verifications(stored.envelope.id),
                },
            }
        )
    finally:
        store.close()
    return EXIT_OK


def cmd_artifact(args: argparse.Namespace) -> int:
    store = _open_store(args)
    try:
        artifact = store.get_artifact(args.artifact_id)
        if artifact is None:
            _print({"error": "unknown artifact"})
            return EXIT_FAILED
        record = artifact.to_json()
        from .artifacts.store import ArtifactStoreError, read_verified

        try:  # P3.2 D3: the stored bytes, hash re-checked, never through a symlink
            record["content"] = read_verified(artifact).decode("utf-8")
        except (ArtifactStoreError, UnicodeDecodeError):
            record["content"] = None
        _print(record)
    finally:
        store.close()
    return EXIT_OK


REAL_KNOBS: dict[str, Any] = {  # flash spends its output cap on reasoning (step 5 run 2)
    "max_concurrent_model_calls": 2,
    "default_max_output_tokens": 8192,
    "max_output_tokens_ceiling": 32768,
    "attempt_reserve_tokens": 120_000,
    "critic_reserve_tokens": 30_000,
    "planner_reserve_tokens": 30_000,
    "lease_seconds": 120.0,
    "stall_seconds": 300.0,
    "turn_deadline_seconds": 900.0,
}


def cmd_replay(args: argparse.Namespace) -> int:
    """``replay`` (plan D8-9'): read-only; a mismatch, a gap or coverage below 100 % exits
    1; a missing library, events file or Mission exits 2."""

    import tempfile

    from .observability.replay import library_copy, replay_mission
    from .observability.secrets import redact_text
    from .observability.traces import attribution
    from .storage.store import StoreError

    library = Path(args.evidence_dir).resolve() / "orchestrator.db"
    events = Path(args.events).resolve() if args.events else None
    problem = None
    if events is not None and not events.is_file():
        problem = f"no events file at {events}"
    elif events is None and not library.is_file():
        problem = f"no library at {library} and no --events file"
    elif args.attribution and not library.is_file():
        problem = f"--attribution reads the library and there is none at {library}"
    if problem is not None:  # review P2-5: an answer, never a traceback or a silent gap
        _print({"error": problem})
        return EXIT_USAGE
    try:
        report = replay_mission(
            mission_id=args.mission_id,
            library=library if library.is_file() else None,
            events_file=events,
            failures=args.failures,
        )
    except (StoreError, ValueError) as error:
        _print({"error": str(error)})
        return EXIT_USAGE
    if args.attribution:
        with tempfile.TemporaryDirectory() as scratch:
            store = Store.open_readonly(library_copy(library, Path(scratch)))
            try:
                report["attribution"] = attribution(store, args.mission_id)
            finally:
                store.close()
    text, _found = redact_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    )
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    comparison = report.get("comparison") or {}
    complete = (
        comparison.get("consistent", True)
        and comparison.get("coverage", 1.0) == 1.0
        and not report["gaps"]
    )
    return EXIT_OK if complete else EXIT_FAILED


def cmd_approval(args: argparse.Namespace) -> int:
    """``approval ...`` (plan D7-10'): the caller named by ``--as`` decides; the local
    build takes that name as given — a real deployment binds it to authentication."""

    from .api.approvals import ApprovalApi, ApprovalRequestError
    from .contracts import ContractError
    from .governance.permissions import Principal
    from .orchestrator.action_commits import ActionCommitError
    from .orchestrator.commit_service import CommitRejected
    from .storage.store import StoreError

    store = _open_store(args)
    try:
        api = ApprovalApi(CommitService(store), Principal(args.as_principal, args.as_principal))
        try:
            value: Any
            if args.action == "list":
                value = api.list(args.mission_id, state=None if args.all else "PENDING")
            elif args.action == "approve":
                value = api.approve(args.request_id, nonce=args.nonce)
            elif args.action == "reject":
                value = api.reject(args.request_id, reason=args.reason, nonce=args.nonce)
            elif args.action == "revoke":
                value = api.revoke(args.request_id, reason=args.reason)
            elif args.action == "comment":
                value = api.comment(args.target_id, args.text)
            elif args.action == "review":
                value = api.review(
                    args.request_id, verdict=args.verdict, note=args.note, nonce=args.nonce
                )
            elif args.action == "arbitrate":
                value = api.arbitrate(
                    args.request_id, ruling=args.ruling, basis=args.basis, nonce=args.nonce
                )
            elif args.action == "takeover":
                value = api.takeover(
                    args.task_id, action=args.takeover_action, basis=args.basis, note=args.note
                )
            else:  # resolve
                value = api.resolve_unknown(
                    args.action_key,
                    outcome=args.outcome,
                    basis=args.basis,
                    evidence=json.loads(args.evidence),
                )
        except (
            ApprovalRequestError,
            ActionCommitError,
            CommitRejected,
            ContractError,
            StoreError,
            ValueError,
        ) as error:  # review P2-8: a refusal is an answer, never a traceback
            _print({"error": str(error)})
            return EXIT_FAILED
        _print(value)
    finally:
        store.close()
    return EXIT_OK


def cmd_policy(args: argparse.Namespace) -> int:
    """``policy list / show / status`` (ORCH-BUILD §11.4): read the policy library.  The
    library is opened as a read-only copy; nothing here writes.  Exit 0 done, 1 refused,
    2 a bad call."""

    import tempfile

    from .api.policies import PolicyApi, PolicyRequestError
    from .governance.permissions import Principal
    from .governance.promotion import registry_consistency
    from .observability.replay import library_copy

    library = Path(args.evidence_dir).resolve() / "orchestrator.db"
    if not library.is_file():  # review P2-4: never create an empty library by accident
        _print({"error": f"no orchestrator library at {library}"})
        return EXIT_USAGE
    scratch = tempfile.TemporaryDirectory(prefix="orch-policy-")
    store = Store.open_readonly(library_copy(library, Path(scratch.name)))
    try:
        reader = PolicyApi(CommitService(store), Principal("cli-reader", "cli-reader"))
        try:
            value: Any
            if args.action == "list":
                value = reader.list()
            elif args.action == "show":
                value = reader.show(args.identifier)
            else:
                value = {**reader.status(), "consistency": registry_consistency(store)}
        except PolicyRequestError as error:
            _print({"error": str(error)})
            return EXIT_FAILED
        except (OSError, KeyError, TypeError, ValueError) as error:
            _print({"error": str(error)})
            return EXIT_USAGE
        _print(value)
    finally:
        store.close()
        scratch.cleanup()
    return EXIT_OK



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent_orchestrator", description=__doc__)
    parser.add_argument("--version", action="version", version=f"agent_orchestrator {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, *, provider: bool) -> None:
        p.add_argument("--evidence-dir", required=True)
        if provider:
            p.add_argument("--provider", default="fixtures", choices=("fixtures", "env"))
            p.add_argument("--tenant", default="local")
            p.add_argument("--hard-cap-micros", type=int, default=None, dest="hard_cap_micros")
            p.add_argument("--max-concurrency", type=int, default=1, dest="max_concurrency")
            p.add_argument("--test-timeout", type=float, default=120.0, dest="test_timeout")
            p.add_argument(
                "--unpriced",
                action="store_true",
                help="allow a paid provider without a price table (costs recorded as unpriced)",
            )

    mission = sub.add_parser("mission")
    mission_sub = mission.add_subparsers(dest="action", required=True)
    create = mission_sub.add_parser("create")
    common(create, provider=True)
    create.add_argument(
        "--spec", required=True, help="JSON file with goal/success_criteria/idempotency_key/..."
    )
    for action in ("get", "events", "cancel"):
        p = mission_sub.add_parser(action)
        common(p, provider=False)
        p.add_argument("mission_id")

    attempt = sub.add_parser("attempt")
    attempt_sub = attempt.add_subparsers(dest="action", required=True)
    get_attempt = attempt_sub.add_parser("get")
    common(get_attempt, provider=False)
    get_attempt.add_argument("attempt_id")

    artifact = sub.add_parser("artifact")
    artifact_sub = artifact.add_subparsers(dest="action", required=True)
    show = artifact_sub.add_parser("show")
    common(show, provider=False)
    show.add_argument("artifact_id")

    approval = sub.add_parser("approval")
    approval_sub = approval.add_subparsers(dest="action", required=True)

    def caller(p: argparse.ArgumentParser) -> None:
        common(p, provider=False)
        p.add_argument(
            "--as",
            required=True,
            dest="as_principal",
            help="the caller's identity (local build: self-declared; a real deployment authenticates it)",
        )

    listing = approval_sub.add_parser("list")
    caller(listing)
    listing.add_argument("--mission", dest="mission_id", default=None)
    listing.add_argument("--all", action="store_true", help="every request, not only PENDING")
    for name, target in (
        ("approve", "request_id"),
        ("reject", "request_id"),
        ("revoke", "request_id"),
    ):
        p = approval_sub.add_parser(name)
        caller(p)
        p.add_argument(target)
        if name != "approve":
            p.add_argument("--reason", required=True)
        if name != "revoke":
            p.add_argument("--nonce", default=None)
    p = approval_sub.add_parser("comment")
    caller(p)
    p.add_argument("target_id")
    p.add_argument("--text", required=True)
    p = approval_sub.add_parser("review")
    caller(p)
    p.add_argument("request_id")
    p.add_argument("--verdict", required=True, choices=("pass", "fail"))
    p.add_argument("--note", default="")
    p.add_argument("--nonce", default=None)
    p = approval_sub.add_parser("arbitrate")
    caller(p)
    p.add_argument("request_id")
    p.add_argument("--ruling", required=True)
    p.add_argument("--basis", required=True)
    p.add_argument("--nonce", default=None)
    p = approval_sub.add_parser("takeover")
    caller(p)
    p.add_argument("task_id")
    p.add_argument(
        "--action", required=True, choices=("stop", "retry_with_note"), dest="takeover_action"
    )
    p.add_argument("--basis", required=True)
    p.add_argument("--note", default="")
    p = approval_sub.add_parser("resolve")
    caller(p)
    p.add_argument("action_key")
    p.add_argument("--outcome", required=True, choices=("succeeded", "failed"))
    p.add_argument("--basis", required=True)
    p.add_argument("--evidence", required=True, help="a JSON object with what the person saw")

    replay = sub.add_parser("replay")  # step 8
    common(replay, provider=False)
    replay.add_argument("mission_id")
    replay.add_argument(
        "--events", default=None, help="replay an events.jsonl instead of the library's events"
    )
    replay.add_argument("--failures", action="store_true", help="add the timeline of what failed")
    replay.add_argument(
        "--attribution", action="store_true", help="add the contribution attribution"
    )
    replay.add_argument("--out", default=None, help="also write the report to this file")
    policy = sub.add_parser("policy")  # read-only: the policy library has no write verbs
    policy_sub = policy.add_subparsers(dest="action", required=True)
    for name in ("list", "status"):
        p = policy_sub.add_parser(name)
        common(p, provider=False)
    p = policy_sub.add_parser("show")
    common(p, provider=False)
    p.add_argument("identifier")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "mission":
        return cmd_mission(args)
    if args.command == "attempt":
        return cmd_attempt(args)
    if args.command == "artifact":
        return cmd_artifact(args)
    if args.command == "approval":
        return cmd_approval(args)
    if args.command == "replay":
        return cmd_replay(args)
    if args.command == "policy":
        return cmd_policy(args)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
