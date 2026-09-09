# SPDX-License-Identifier: BUSL-1.1
"""Single source of truth for the domain schema chain layered above v49.

The base ``migrations/*.sql`` glob stops at v49; every later step lives in a
domain subdirectory (``s5c/``, ``procedure/``, ``primary/``) and is installed by
its own explicit initializer. Each of those steps used to be admitted by hand
written literal tuples spread over the schema modules and the domain stores
(``if _expected_user_version not in (52, 53, 54)``). A new migration had to
remember every one of them; ``primary/047_..._v55.sql`` did not, and
``S5cStore`` silently fell back to the v50 cursor table that
``s5c/044_prospective_terminals_v52.sql`` had sealed, so every production
prospective registration died on ``s5c_cursor_successor_required``.

The chain is therefore discovered from the migration files themselves and
every admission decision is derived from it:

* :data:`DOMAIN_CHAIN` — ordered ``(user_version, migration_id)`` pairs.
* :func:`accepted_versions` — "a feature introduced at V is present in these
  versions", i.e. V and every later chain version. This replaces the literals.
* :data:`DOMAIN_SCHEMA_STEPS` — the one place a new step registers its module,
  validator and initializer; the guard test fails when a discovered chain
  version has no entry, which is exactly the omission that produced v55.

Nothing here opens a database or imports a schema module at import time: the
module must stay usable from every store and schema module without cycles.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Ordinary v9..v49 steps live directly in ``migrations/``; the domain chain is
# deliberately kept in subdirectories so the base glob cannot activate it.
DOMAIN_MIGRATION_DIRS = ("s5c", "procedure", "primary")

# The last version the base migration runner owns. The domain chain starts at
# ``BASE_SCHEMA_VERSION + 1`` and must stay contiguous from there.
BASE_SCHEMA_VERSION = 49

_MIGRATION_NAME = re.compile(r"^(?P<ordinal>\d{3})_(?P<name>[a-z0-9_]+)_v(?P<version>\d+)\.sql$")


class SchemaChainError(RuntimeError):
    """The on-disk domain migration chain is not a contiguous ordered chain."""


def _discover() -> tuple[tuple[int, str], ...]:
    found: list[tuple[int, int, str]] = []
    for directory in DOMAIN_MIGRATION_DIRS:
        for path in sorted((MIGRATIONS_DIR / directory).glob("*.sql")):
            match = _MIGRATION_NAME.match(path.name)
            if match is None:
                raise SchemaChainError(f"domain_migration_name_invalid:{directory}/{path.name}")
            found.append((int(match["ordinal"]), int(match["version"]), f"{directory}/{path.name}"))
    if not found:
        raise SchemaChainError("domain_migration_chain_empty")
    found.sort()
    chain = tuple((version, migration_id) for _, version, migration_id in found)
    expected = BASE_SCHEMA_VERSION + 1
    for version, migration_id in chain:
        if version != expected:
            raise SchemaChainError(f"domain_migration_chain_not_contiguous:{migration_id}")
        expected += 1
    ordinals = [ordinal for ordinal, _, _ in found]
    if ordinals != list(range(ordinals[0], ordinals[0] + len(ordinals))):
        raise SchemaChainError("domain_migration_ordinals_not_contiguous")
    return chain


DOMAIN_CHAIN: tuple[tuple[int, str], ...] = _discover()
CHAIN_VERSIONS: tuple[int, ...] = tuple(version for version, _ in DOMAIN_CHAIN)
CHAIN_MIGRATIONS: dict[int, str] = {version: name for version, name in DOMAIN_CHAIN}
HEAD_SCHEMA_VERSION: int = CHAIN_VERSIONS[-1]

# The one registration a new domain migration must add: module, validator,
# initializer. The module additionally declares ``SCHEMA_VERSION``;
# ``tests/memory/test_s5c_schema_chain.py`` fails when a discovered chain
# version is missing here, or when the two disagree.
DOMAIN_SCHEMA_STEPS: dict[int, tuple[str, str, str]] = {
    50: ("deskpet.memory.s5c_schema",
         "validate_s5c_state_db", "initialize_s5c_state_db"),
    51: ("deskpet.memory.s5c_timer_schema",
         "validate_s5c_timer_state_db", "initialize_s5c_timer_state_db"),
    52: ("deskpet.memory.s5c_terminal_schema",
         "validate_s5c_terminal_state_db", "initialize_s5c_terminal_state_db"),
    53: ("deskpet.memory.procedure_schema",
         "validate_procedure_state_db", "initialize_procedure_state_db"),
    54: ("deskpet.memory.procedure_recovery_schema",
         "validate_procedure_recovery_state_db", "initialize_procedure_recovery_state_db"),
    55: ("deskpet.memory.primary_tool_call_schema",
         "validate_primary_tool_call_state_db", "initialize_primary_tool_call_state_db"),
    56: ("deskpet.memory.context_use_recollect_schema",
         "validate_context_use_recollect_state_db", "initialize_context_use_recollect_state_db"),
    57: ("deskpet.memory.evidence_kind_schema",
         "validate_evidence_kind_state_db", "initialize_evidence_kind_state_db"),
}


def accepted_versions(introduced_at: int) -> tuple[int, ...]:
    """Chain versions that still carry a feature first published at ``introduced_at``.

    Every domain step so far is additive: it never drops a predecessor's tables,
    so a v52 table is present in v52 and in every later chain version. Callers
    use this instead of a literal tuple, and a future non-additive step must
    remove its predecessors from the chain rather than leave a stale literal.
    """

    if introduced_at not in CHAIN_MIGRATIONS:
        raise SchemaChainError(f"domain_schema_version_unknown:{introduced_at}")
    return tuple(version for version in CHAIN_VERSIONS if version >= introduced_at)


def _resolve(version: int, index: int) -> Callable[..., object] | None:
    entry = DOMAIN_SCHEMA_STEPS.get(version)
    if entry is None:
        return None
    return getattr(importlib.import_module(entry[0]), entry[index])


def domain_validator(version: int) -> Callable[..., None] | None:
    """Resolve the exact validator for a published chain version, else ``None``.

    ``None`` means "not a known successor" — the caller keeps its own
    conservative base validation, which fails closed on an unknown integer.
    """

    return _resolve(version, 1)


def domain_initializer(version: int) -> Callable[..., object] | None:
    """Resolve the initializer that publishes exactly ``version``, else ``None``."""

    return _resolve(version, 2)


__all__ = [
    "BASE_SCHEMA_VERSION",
    "CHAIN_MIGRATIONS",
    "CHAIN_VERSIONS",
    "DOMAIN_CHAIN",
    "DOMAIN_MIGRATION_DIRS",
    "DOMAIN_SCHEMA_STEPS",
    "HEAD_SCHEMA_VERSION",
    "SchemaChainError",
    "accepted_versions",
    "domain_initializer",
    "domain_validator",
]
