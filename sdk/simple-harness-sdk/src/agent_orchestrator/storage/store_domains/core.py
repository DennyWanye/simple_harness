# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""Opening, schema migration, transactions, read views and fault injection."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Self

from simple_harness.contracts import canonical_json

from .. import schema, source_records
from ..assurance_changes import install_change_context
from ..assurance_upgrade import install_upgrade_functions
from ..source_records import begin, record_written_rows
from .records import InjectedCrash, SchemaIncompatible, StoreBusy, StoreConflict, StoreError, _loads


class StoreCore:
    """Opening, schema migration, transactions, read views and fault injection."""

    def __init__(self, connection: sqlite3.Connection, path: Path, clock: Callable[[], float]):
        self._connection = connection
        self._path = path
        self._clock = clock
        self._lock = threading.RLock()
        self._depth = 0
        self._holder: object | None = None
        # Counts outermost write transactions; a consumer that locked a use early
        # in a UoW proves it commits in that same UoW by this number.
        self._transaction_generation = 0
        self._reading = False  # host support S2 review P1-B: a read view writes nothing
        self._source_naming = False  # set by open(): see storage/source_records.py
        self._armed: set[str] = set()
        self._skips: dict[str, int] = {}
        self._times: dict[str, int] = {}
        self.fired: list[str] = []
        self._readonly = False
        self._assurance_change_context: tuple[str, object | None] | None = None

        install_change_context(self)

        install_upgrade_functions(connection)

    # ---------------------------------------------------------------- lifecycle
    @classmethod
    def open(cls, path: str | Path, *, clock: Callable[[], float] = time.time) -> Self:
        resolved = Path(path).expanduser()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            resolved, isolation_level=None, timeout=5.0, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        store = cls(connection, resolved, clock)
        store._initialize_or_validate()

        source_records.install(connection)
        store._source_naming = True
        return store

    @classmethod
    def open_readonly(cls, path: str | Path) -> Self:
        """Step 8 (plan D8-1'): read an existing library without writing it — no
        directory creation, no WAL switch, no migration or backup.  An older schema is
        accepted as it is (``snapshot`` skips tables it does not have); a newer one is
        refused."""

        resolved = Path(path).expanduser().resolve()
        if not resolved.is_file():
            raise StoreError(f"no library at {resolved}")
        connection = sqlite3.connect(
            f"file:{resolved}?mode=ro",
            uri=True,
            isolation_level=None,
            timeout=5.0,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        store = cls(connection, resolved, time.time)
        store._readonly = True
        rows = [
            tuple(row)
            for row in connection.execute(
                "SELECT version,name,checksum FROM orch_schema_migrations ORDER BY version"
            )
        ]
        expected = [(m.version, m.name, m.checksum) for m in schema.MIGRATIONS]
        if len(rows) > len(expected) or rows != expected[: len(rows)]:
            connection.close()
            raise SchemaIncompatible(f"orchestrator schema mismatch: {rows}")
        return store

    def has_table(self, name: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (name,)
        ).fetchone()
        return row is not None

    def close(self) -> None:
        self._connection.close()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def now(self) -> float:
        return float(self._clock())

    def _initialize_or_validate(self) -> None:
        tables = {
            row[0]
            for row in self._connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "orch_schema_migrations" not in tables:
            if tables:
                raise SchemaIncompatible("file is not an orchestrator library")
            with self.transaction() as connection:
                for migration in schema.MIGRATIONS:
                    self._apply_migration(connection, migration)
            return
        rows = [
            tuple(row)
            for row in self._connection.execute(
                "SELECT version,name,checksum FROM orch_schema_migrations ORDER BY version"
            )
        ]
        expected = [(m.version, m.name, m.checksum) for m in schema.MIGRATIONS]
        if rows == expected:
            return
        if len(rows) > len(expected) or rows != expected[: len(rows)]:
            raise SchemaIncompatible(f"orchestrator schema mismatch: {rows}")
        # D4-15 / ORCH §12.6: an older library is backed up, then upgraded in place
        pending = schema.MIGRATIONS[len(rows) :]
        if str(self._path) != ":memory:" and self._path.is_file():
            backup = self._path.with_name(
                f"{self._path.name}.pre-schema-{pending[-1].version}.backup"
            )
            if not backup.exists():
                self._connection.commit()
                target = sqlite3.connect(backup)
                try:
                    self._connection.backup(target)
                finally:
                    target.close()
        with self.transaction() as connection:
            for migration in pending:
                self._apply_migration(connection, migration)

    def _apply_migration(self, connection: sqlite3.Connection, migration: schema.Migration) -> None:
        if migration.version == 2:
            self._renumber_artifact_lineage(connection)  # P1-5: v1 libraries may hold duplicates
        # A trigger body (or a quoted value/comment) may contain semicolons.
        # sqlite's parser identifies complete statements without executescript's
        # implicit commit, so the encompassing migration transaction stays atomic.
        pending = ""
        for character in migration.ddl:
            pending += character
            if character == ";" and sqlite3.complete_statement(pending):
                connection.execute(pending)
                pending = ""
        if pending.strip():
            connection.execute(pending)
        if migration.version == 6:
            self._bind_legacy_missions(connection)
        connection.execute(
            "INSERT INTO orch_schema_migrations VALUES (?,?,?,?)",
            (migration.version, migration.name, migration.checksum, self.now),
        )

    def _bind_legacy_missions(self, connection: sqlite3.Connection) -> None:
        """Step 9 (plan D9-3', review P1-2): Missions created before policy binding are
        bound to ``policy-legacy`` — no parameters, meaning "the deployment configuration,
        as these Missions always ran" — so an upgraded library still recovers them.
        ``legacy`` is never ACTIVE."""

        missions = [str(row[0]) for row in connection.execute("SELECT mission_id FROM missions")]
        if not missions:
            return
        legacy = schema.LEGACY_POLICY_VERSION
        now = self.now
        record: dict[str, Any] = {
            "version_id": legacy,
            "params_hash": "legacy",
            "params": None,
            "source": "legacy",
            "status": "LEGACY",
            "detail": {"note": "迁移推定：迁移前的 Mission 沿用部署配置运行"},
        }
        connection.execute(
            "INSERT INTO policy_versions(version_id,params_hash,source,status,json,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
            (legacy, "legacy", "legacy", "LEGACY", canonical_json(record), now, now),
        )
        for mission_id in missions:
            binding: dict[str, Any] = {
                "mission_id": mission_id,
                "version_id": legacy,
                "source": "legacy",
                "provider_kind": "unknown",
            }
            connection.execute(
                "INSERT INTO mission_policies(mission_id,version_id,source,provider_kind,json,bound_at)"
                " VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                (mission_id, legacy, "legacy", "unknown", canonical_json(binding), now),
            )

    @staticmethod
    def _renumber_artifact_lineage(connection: sqlite3.Connection) -> None:
        """Step-3 libraries (L3-2) could record two candidates of one path with the same
        version; before the (mission, path, version) index exists, renumber each lineage
        in (created_at, artifact_id) order — ids and hashes are untouched."""

        rows = connection.execute(
            "SELECT artifact_id, mission_id, path, version, json FROM artifacts"
            " ORDER BY mission_id, path, created_at, artifact_id"
        ).fetchall()
        current: tuple[str, str] | None = None
        counter = 0
        for artifact_id, mission_id, path, version, raw in rows:
            key = (mission_id, path)
            if key != current:
                current, counter = key, 0
            counter += 1
            if counter == version:
                continue
            document = _loads(raw)
            document["version"] = counter
            connection.execute(
                "UPDATE artifacts SET version = ?, json = ? WHERE artifact_id = ?",
                (counter, canonical_json(document), artifact_id),
            )

    # ------------------------------------------------------------- transactions
    @staticmethod
    def _current_task() -> object | None:
        try:
            return asyncio.current_task()
        except RuntimeError:  # no running loop: plain synchronous caller
            return None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if self._reading:
                raise StoreError("a read view is open: nothing may be written inside it")
            if self._depth:
                # step 6 (review P1-15): an RLock lets a *different* coroutine of the same
                # thread walk into an open transaction; that would be silent corruption, so a
                # nested entry must come from the task that opened it
                holder = self._holder
                current = self._current_task()
                if holder is not None and current is not None and current is not holder:
                    raise StoreError(
                        "transaction crossed an await: another task entered an open transaction"
                    )
                # A caller may catch a refused inner operation and commit its audit
                # event. Its partial writes must not survive in that outer unit.
                savepoint = f"store_nested_{self._depth}"
                self._connection.execute(f"SAVEPOINT {savepoint}")
                self._depth += 1
                try:
                    yield self._connection
                except BaseException:
                    self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    raise
                finally:
                    self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                    self._depth -= 1
                return
            try:
                self._connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                if "locked" in str(error).lower() or "busy" in str(error).lower():
                    raise StoreBusy(str(error)) from error
                raise
            self._depth = 1
            self._holder = self._current_task()
            self._transaction_generation += 1
            if self._source_naming:  # 只记本事务里的写
                begin(self._connection)
            try:
                yield self._connection
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                try:
                    if self._source_naming:
                        record_written_rows(self, self._connection)
                    self._connection.execute("COMMIT")
                except BaseException:
                    # A deferred FK can fail at COMMIT, leaving SQLite's
                    # transaction open. Do not release ownership over that UoW.
                    if self._connection.in_transaction:
                        self._connection.execute("ROLLBACK")
                    raise
            finally:
                self._depth = 0
                self._holder = None

    @property
    def transaction_generation(self) -> int:
        """Identity of the open outermost write transaction; 0 before the first."""
        return self._transaction_generation

    @contextmanager
    def read_view(self) -> Iterator[sqlite3.Connection]:
        """A consistent read (host support S2, P3.1-A05): every SELECT inside sees one
        snapshot of the library, so a Mission snapshot and its event cursor agree.  Inside
        an open transaction it simply joins it.  Like :meth:`transaction` it must never
        span an await.  It writes nothing: a :meth:`transaction` opened inside it is refused
        (review P1-B); an exception rolls the view back, and the state is reset whatever
        COMMIT / ROLLBACK does, so a failed end can never leave later transactions
        non-atomic."""

        with self._lock:
            if self._depth:
                yield self._connection
                return
            self._connection.execute("BEGIN")  # deferred: a read snapshot, no write lock
            self._depth = 1
            self._holder = self._current_task()
            self._reading = True
            try:
                yield self._connection
            except BaseException:
                try:
                    self._connection.execute("ROLLBACK")
                finally:
                    self._depth, self._holder, self._reading = 0, None, False
                raise
            else:
                try:
                    self._connection.execute("COMMIT")
                finally:
                    self._depth, self._holder, self._reading = 0, None, False

    @property
    def connection(self) -> sqlite3.Connection:
        return self._connection

    def read_generation(self) -> tuple[int, int, int] | None:
        """Changes whenever anything this Store reads may have changed; ``None`` inside a
        write transaction (its uncommitted rows may still roll back).

        Own writes move ``total_changes``; another connection's commit moves
        ``PRAGMA data_version``; a replaced connection moves the identity.  A reader may
        reuse a result it verified under an equal generation (2026-09-28: the loop
        re-verified whole TaskGraph revisions many times per idle cycle, ~100% CPU).
        """

        with self._lock:
            if self._depth and not self._reading:
                return None
            version = int(self._connection.execute("PRAGMA data_version").fetchone()[0])
            return (id(self._connection), int(self._connection.total_changes), version)

    # ---------------------------------------------------------- fault injection
    def arm(self, *points: str, skip: int = 0, times: int = 1) -> None:
        """Arm crash points; ``skip`` lets the first ``skip`` hits pass (crash on the next);
        ``times`` keeps the point armed for that many crashes (step 4: repeated retrieval
        failures)."""

        self._armed.update(points)
        for point in points:
            self._skips[point] = skip
            self._times[point] = max(1, times)

    def disarm(self, *points: str) -> None:
        if points:
            self._armed.difference_update(points)
        else:
            self._armed.clear()

    def fault(self, point: str, kind: str | None = None) -> None:
        """Crash here if ``point`` (or ``point:kind``) is armed; one shot per arming."""

        for candidate in (point, f"{point}:{kind}") if kind else (point,):
            if candidate in self._armed:
                if self._skips.get(candidate, 0) > 0:
                    self._skips[candidate] -= 1
                    continue
                remaining = self._times.get(candidate, 1) - 1
                if remaining <= 0:
                    self._armed.discard(candidate)
                else:
                    self._times[candidate] = remaining
                self.fired.append(candidate)
                raise InjectedCrash(candidate)

    # ------------------------------------------------------------------ util
    def _cas(
        self,
        table: str,
        key_column: str,
        key: str,
        expected_version: int,
        values: Mapping[str, Any],
    ) -> None:
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE {table} SET {assignments}, updated_at = ? WHERE {key_column} = ? AND version = ?",
                (*values.values(), self.now, key, expected_version),
            )
            if cursor.rowcount != 1:
                raise StoreConflict(f"{table}:{key} is not at version {expected_version}")
