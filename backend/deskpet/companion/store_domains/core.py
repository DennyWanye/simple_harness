"""Connection, writes, ownership checks and detail versions."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..contracts import CompanionOwnerError, GrowthEvent, OwnerRef
from ..schema import configure_connection, initialize_schema
from .support import canonical_hash


class CompanionStoreCore:
    """Connection, writes, ownership checks and detail versions."""

    if TYPE_CHECKING:

        def _enqueue_job_db(
            self,
            db: sqlite3.Connection,
            owner: OwnerRef,
            *,
            job_id: str,
            kind: str,
            dedupe_key: str,
            payload: Mapping[str, Any],
            reason_code: str,
            budget_reserved_tokens: int,
            budget_reserved_ms: int,
            now: str,
        ) -> Mapping[str, Any]: ...
        def _find_candidate_build(
            self, db: sqlite3.Connection, build_id: str, *, owner: OwnerRef | None = None
        ) -> sqlite3.Row: ...
        @staticmethod
        def _insert_outbox(
            db: sqlite3.Connection,
            owner: OwnerRef,
            *,
            outbox_id: str,
            event_kind: str,
            event_id: str,
            sink_kind: str,
            payload: Mapping[str, Any],
            now: str,
            reason_code: str,
        ) -> None: ...
        def _record_growth_event_db(
            self, db: sqlite3.Connection, event: GrowthEvent, *, now: str
        ) -> Mapping[str, Any]: ...
        @staticmethod
        def _validate_relative_path(path: str) -> None: ...
        def create_notification(
            self,
            owner: OwnerRef,
            *,
            notification_id: str,
            kind: str,
            source_refs: Sequence[str],
            summary: str,
            detail: Mapping[str, Any] | Sequence[Any],
            available_actions: Sequence[str],
            reason_code: str = ...,
        ) -> Mapping[str, Any]: ...

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime | str] | None = None,
        initialize: bool = True,
    ) -> None:
        self.path = str(path)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._active_write: ContextVar[sqlite3.Connection | None] = ContextVar(
            f"companion_store_write_{id(self)}", default=None
        )
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        if initialize:
            db = self._connect()
            try:
                initialize_schema(db)
            finally:
                # sqlite3.Connection's context manager commits/rolls back but
                # does not close.  Leaving the schema connection alive keeps
                # companion.db locked on Windows across profile deletion and
                # application shutdown.
                db.close()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, isolation_level=None, timeout=5.0)
        configure_connection(db)
        return db

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        active = self._active_write.get()
        if active is not None:
            yield active
            return
        db = self._connect()
        token = self._active_write.set(db)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            self._active_write.reset(token)
            db.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def _now(self) -> str:
        value = self._clock()
        if isinstance(value, str):
            return value
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @staticmethod
    def _owner(owner: OwnerRef | tuple[str, int] | Mapping[str, Any]) -> OwnerRef:
        if isinstance(owner, OwnerRef):
            return owner
        if isinstance(owner, tuple):
            return OwnerRef(str(owner[0]), int(owner[1]))
        return OwnerRef(
            str(owner["profile_id"]),
            int(owner.get("profile_generation", owner.get("generation"))),
        )

    @staticmethod
    def _require_owner(db: sqlite3.Connection, owner: OwnerRef) -> None:
        row = db.execute(
            "SELECT status FROM profiles WHERE profile_id=? AND generation=?",
            (owner.profile_id, owner.profile_generation),
        ).fetchone()
        if row is None or row["status"] != "active":
            raise CompanionOwnerError(
                f"companion_owner_stale:{owner.profile_id}:{owner.profile_generation}"
            )

    @staticmethod
    def _same(row: sqlite3.Row, expected: Mapping[str, Any]) -> bool:
        return all(row[key] == value for key, value in expected.items())

    def _bump_detail(
        self,
        db: sqlite3.Connection,
        owner: OwnerRef,
        *,
        mutation_hash: str,
        now: str,
    ) -> int:
        row = db.execute(
            """SELECT detail_version,detail_hash FROM companion_detail_versions
               WHERE profile_id=? AND profile_generation=?""",
            (owner.profile_id, owner.profile_generation),
        ).fetchone()
        if row is None:
            version = 1
            detail_hash = canonical_hash(
                {"version": version, "prior": None, "mutation_hash": mutation_hash}
            )
            db.execute(
                """INSERT INTO companion_detail_versions(
                     profile_id,profile_generation,detail_version,detail_hash,updated_at
                   ) VALUES (?,?,?,?,?)""",
                (owner.profile_id, owner.profile_generation, version, detail_hash, now),
            )
            return version
        version = int(row["detail_version"]) + 1
        detail_hash = canonical_hash(
            {
                "version": version,
                "prior": str(row["detail_hash"]),
                "mutation_hash": mutation_hash,
            }
        )
        db.execute(
            """UPDATE companion_detail_versions
               SET detail_version=?,detail_hash=?,updated_at=?
               WHERE profile_id=? AND profile_generation=? AND detail_version=?""",
            (
                version,
                detail_hash,
                now,
                owner.profile_id,
                owner.profile_generation,
                version - 1,
            ),
        )
        return version

    def get_detail_version(self, owner: OwnerRef | tuple[str, int]) -> Mapping[str, Any]:
        resolved = self._owner(owner)
        with self.read() as db:
            self._require_owner(db, resolved)
            row = db.execute(
                """SELECT detail_version,detail_hash,updated_at
                   FROM companion_detail_versions
                   WHERE profile_id=? AND profile_generation=?""",
                (resolved.profile_id, resolved.profile_generation),
            ).fetchone()
            if row is None:
                return {"detail_version": 0, "detail_hash": canonical_hash([]), "updated_at": None}
            return dict(row)
