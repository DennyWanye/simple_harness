from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.acceptance import harness_authority_audit as authority_audit


PLAN_DIR = Path("plans/2026-07-20-agent-harness-simplification")


def _write(repo: Path, relative: str, source: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_r45_authority_manifests_lock_real_baseline_without_enforcing_exit_target() -> None:
    result = authority_audit.audit()

    assert result == {
        "passed": True,
        "failures": [],
        "current": {
            "dml": {"runtime_dml_authority_count": 2},
            "uow": {"transaction_starter_count": 33},
            "harness": {
                "legacy_survivor_count": 15,
                "run_map_authority_count": 4,
                "supervisor_task_authority_count": 2,
                "presenter_converter_authority_count": 1,
            },
        },
    }

    target = authority_audit.audit(enforce_target=True)
    assert target["passed"] is False
    assert any("runtime DML authority count is 1, found 2" in item for item in target["failures"])
    assert any("transaction starter count is <=23, found 33" in item for item in target["failures"])


@pytest.mark.parametrize(
    "name",
    (
        "execution_table_dml_authorities.json",
        "uow_public_write_ops.json",
        "harness_authorities.json",
    ),
)
def test_authority_manifest_items_have_traceable_machine_readable_fields(name: str) -> None:
    payload = json.loads((PLAN_DIR / name).read_text(encoding="utf-8"))
    assert payload["baseline_commit"] == "796905b9888c2659af78cec21de1ee96b46a2c51"
    assert payload["items"]
    for item in payload["items"]:
        assert authority_audit.REQUIRED_FIELDS <= item.keys()
        assert item["path"].startswith("backend/")
        assert item["symbol"]
        assert item["authority"]
        assert isinstance(item["tables"], list)
        assert isinstance(item["callsites"], list)
        assert len(item["source_hash"]) == 64


def test_dml_discovery_finds_moved_or_renamed_execution_writer(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "backend/deskpet/renamed/storage_facade.py",
        """
class HiddenPersistenceFacade:
    async def _mutate(self, db):
        await db.execute("UPDATE execution_runs SET status='failed'")
""",
    )

    items = authority_audit.discover_dml_authorities(tmp_path)

    assert [(item["symbol"], item["authority"], item["tables"]) for item in items] == [
        ("HiddenPersistenceFacade", "unclassified", ["execution_runs"])
    ]


def test_transaction_starter_discovery_counts_private_method_and_reachable_tables(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "backend/deskpet/workflows/store/execution_uow.py",
        """
class SqliteExecutionUnitOfWork:
    def _write_transaction(self):
        raise NotImplementedError

    async def _hidden_write(self):
        async with self._write_transaction() as db:
            await self._helper(db)

    async def _helper(self, db):
        await db.execute("INSERT INTO execution_events(event_id) VALUES(?)")
""",
    )

    items = authority_audit.discover_uow_write_ops(tmp_path)

    assert len(items) == 1
    assert items[0]["symbol"] == "SqliteExecutionUnitOfWork._hidden_write"
    assert items[0]["public"] is False
    assert items[0]["tables"] == ["execution_events"]


def test_generic_opcode_sql_dispatch_is_rejected_even_without_execution_name(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "backend/deskpet/harness/renamed_writer.py",
        """
async def dispatch(db, opcode, table):
    await db.execute(f"{opcode} {table}")
""",
    )

    violations = authority_audit._generic_sql_violations(tmp_path)

    assert violations == [
        "backend/deskpet/harness/renamed_writer.py:dispatch:generic_opcode"
    ]


def test_ast_discovers_new_run_map_task_and_converter_without_name_allowlist(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "backend/deskpet/harness/relocated.py",
        """
import asyncio

class RenamedLiveOwner:
    def __init__(self):
        self.volatile_runs: dict[str, object] = {}
        self.worker: asyncio.Task[None] | None = None
""",
    )
    _write(
        tmp_path,
        "backend/deskpet/presentation/translation.py",
        """
class RelocatedTranslation:
    def translate(self, event: RunEvent) -> tuple[AgentEvent, ...]:
        return ()
""",
    )

    items = authority_audit.discover_harness_authorities(tmp_path)
    identities = {(item["kind"], item["path"], item["symbol"]) for item in items}

    assert (
        "run_map",
        "backend/deskpet/harness/relocated.py",
        "RenamedLiveOwner.volatile_runs",
    ) in identities
    assert (
        "task",
        "backend/deskpet/harness/relocated.py",
        "RenamedLiveOwner.worker",
    ) in identities
    assert (
        "converter",
        "backend/deskpet/presentation/translation.py",
        "RelocatedTranslation.translate",
    ) in identities


def test_manifest_comparison_fails_closed_for_unclassified_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(
        tmp_path,
        "backend/deskpet/workflows/store/execution_uow.py",
        """
class SqliteExecutionUnitOfWork:
    def _write_transaction(self):
        raise NotImplementedError
""",
    )
    _write(
        tmp_path,
        "backend/deskpet/harness/new_owner.py",
        """
class NewOwner:
    def __init__(self):
        self.active_runs: dict[str, object] = {}
""",
    )
    monkeypatch.setattr(authority_audit, "_git_commit", lambda _repo: "fixture")
    current = authority_audit.build_manifests(tmp_path)
    for label, real_path in authority_audit.MANIFEST_PATHS.items():
        payload = dict(current[label])
        payload["items"] = []
        path = tmp_path / real_path.relative_to(authority_audit.ROOT)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    result = authority_audit.audit(tmp_path)

    assert result["passed"] is False
    assert any("harness unclassified discoveries" in item for item in result["failures"])
