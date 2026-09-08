# SPDX-License-Identifier: BUSL-1.1
"""Guards for the domain schema chain above v49 (RUN-RERUN-FLASH-01-REVIEW §1).

`primary/047_primary_assistant_tool_calls_v55.sql` bumped ``user_version`` to 55
without extending ``S5cStore``'s literal ``((52,), (53,), (54,))`` cursor-table
allow-list, so every production prospective registration silently addressed the
v50 cursor table that ``s5c/044_prospective_terminals_v52.sql`` had sealed and
died on ``s5c_cursor_successor_required``. Twenty corpus cases went
SETUP_BLOCKED before anyone saw the constraint name.

Three guards, in the order they would have caught it:

1. ``test_no_closed_user_version_allow_list_survives_in_memory_modules`` — a
   source scan: no Host memory module may admit schema versions from a literal
   collection again.
2. ``test_every_discovered_migration_registers_its_step`` — a new migration file
   that does not register its module/validator/initializer fails here.
3. ``test_full_chain_to_head_registers_a_prospective_record`` — the end-to-end
   fact the closed list broke: migrate to the current head, then commit a real
   registration through the real store.
"""

from __future__ import annotations

import ast
import importlib
import sqlite3
from pathlib import Path

import pytest

from deskpet.memory import schema, schema_chain
from deskpet.memory.s5c_store import S5cStore
from deskpet.memory.s5c_terminal_schema import TERMINAL_TABLES

from tests.memory.test_s5c_store import P, counts, registration

_MEMORY_PACKAGE = Path(schema_chain.__file__).parent
# The neighbourhood a schema-version literal lives in. Wide enough to catch the
# next twenty steps, narrow enough that ordinary small-int tuples are not flagged.
_VERSION_RANGE = range(schema_chain.BASE_SCHEMA_VERSION,
                       schema_chain.BASE_SCHEMA_VERSION + 21)
# `schema_chain` is the single source of truth and is allowed to name versions.
_ALLOWED_SOURCE_LITERALS = {"schema_chain.py"}


def _constants(node: ast.AST) -> list[object]:
    if isinstance(node, ast.Constant):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return [value for element in node.elts for value in _constants(element)]
    return []


def _is_version(value: object) -> bool:
    return type(value) is int and value in _VERSION_RANGE


def _closed_version_membership(source: str) -> list[int]:
    """Line numbers of the two shapes a new migration silently invalidates.

    ``x in (52, 53, 54)`` — the shape that broke ``S5cStore`` on v55 — and
    ``{50: ..., 51: ...}``, the second hand-kept successor map that lived in
    ``schema.py``. Both must come from ``schema_chain`` instead.
    """

    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict) and any(
            _is_version(value) for key in node.keys if key is not None
            for value in _constants(key)
        ):
            found.append(node.lineno)
            continue
        if not isinstance(node, ast.Compare):
            continue
        for operator, comparator in zip(node.ops, node.comparators, strict=True):
            if not isinstance(operator, (ast.In, ast.NotIn)):
                continue
            if not isinstance(comparator, (ast.Tuple, ast.List, ast.Set)):
                continue
            if any(_is_version(value) for value in _constants(comparator)):
                found.append(node.lineno)
    return found


def test_no_closed_user_version_allow_list_survives_in_memory_modules() -> None:
    offenders = {}
    for path in sorted(_MEMORY_PACKAGE.glob("*.py")):
        if path.name in _ALLOWED_SOURCE_LITERALS:
            continue
        lines = _closed_version_membership(path.read_text(encoding="utf-8"))
        if lines:
            offenders[path.name] = lines
    assert offenders == {}, (
        "schema versions must be admitted through schema_chain.accepted_versions(), "
        f"never a literal collection a later migration forgets: {offenders}")


def test_discovered_chain_is_contiguous_and_matches_the_declared_head() -> None:
    assert schema_chain.CHAIN_VERSIONS == tuple(
        range(schema_chain.BASE_SCHEMA_VERSION + 1,
              schema_chain.BASE_SCHEMA_VERSION + 1 + len(schema_chain.CHAIN_VERSIONS)))
    assert schema_chain.HEAD_SCHEMA_VERSION == max(schema_chain.CHAIN_VERSIONS)
    assert schema_chain.accepted_versions(schema_chain.HEAD_SCHEMA_VERSION) == (
        schema_chain.HEAD_SCHEMA_VERSION,)
    with pytest.raises(schema_chain.SchemaChainError):
        schema_chain.accepted_versions(schema_chain.HEAD_SCHEMA_VERSION + 1)


def test_every_discovered_migration_registers_its_step() -> None:
    """A new migration that bumps user_version must join the chain registry."""

    assert set(schema_chain.DOMAIN_SCHEMA_STEPS) == set(schema_chain.CHAIN_VERSIONS)
    for version in schema_chain.CHAIN_VERSIONS:
        module_name, validator, initializer = schema_chain.DOMAIN_SCHEMA_STEPS[version]
        module = importlib.import_module(module_name)
        assert module.SCHEMA_VERSION == version, module_name
        assert schema_chain.domain_validator(version) is getattr(module, validator)
        assert schema_chain.domain_initializer(version) is getattr(module, initializer)
    # An unregistered integer never resolves to a predecessor's validator.
    assert schema_chain.domain_validator(schema_chain.HEAD_SCHEMA_VERSION + 1) is None


def test_head_module_owns_the_head_version() -> None:
    module_name = schema_chain.DOMAIN_SCHEMA_STEPS[schema_chain.HEAD_SCHEMA_VERSION][0]
    assert importlib.import_module(module_name).SCHEMA_VERSION == (
        schema_chain.HEAD_SCHEMA_VERSION)


async def _migrated_to_head(tmp_path: Path) -> Path:
    path = tmp_path / "state.db"
    await schema.initialize_human_memory_program_state_db(path)
    await schema_chain.domain_initializer(schema_chain.HEAD_SCHEMA_VERSION)(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == (
            schema_chain.HEAD_SCHEMA_VERSION)
    return path


@pytest.mark.asyncio
async def test_full_chain_to_head_registers_a_prospective_record(tmp_path) -> None:
    """The exact production fact the closed allow-list broke on v55."""

    path = await _migrated_to_head(tmp_path)
    store = S5cStore(path, P)
    assert store.cursor_table == "prospective_outbox_cursor_v52"
    prepared = await store.commit_registration(*registration(), expected_cursor=None)
    assert prepared is not None
    # One registration row; the legacy v50 cursor (S5C_TABLES[1]) stays empty.
    assert counts(path) == [1, 0, 0, 0]
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM prospective_outbox_cursor_v52").fetchone() == (1,)
        # The v52 step sealed the legacy table; nothing may have reached it.
        assert db.execute(
            "SELECT COUNT(*) FROM prospective_outbox_cursor").fetchone() == (0,)
    assert await store.cursor() is not None


@pytest.mark.asyncio
async def test_every_chain_version_from_v52_selects_the_typed_cursor(tmp_path) -> None:
    """One store instance per published version, not a literal tuple of three."""

    for version in schema_chain.accepted_versions(52):
        path = tmp_path / f"state-{version}.db"
        await schema.initialize_human_memory_program_state_db(path)
        await schema_chain.domain_initializer(version)(path)
        assert S5cStore(path, P).cursor_table == "prospective_outbox_cursor_v52"


@pytest.mark.asyncio
async def test_below_v52_still_selects_the_legacy_cursor(tmp_path) -> None:
    for version in (50, 51):
        path = tmp_path / f"state-{version}.db"
        await schema.initialize_human_memory_program_state_db(path)
        await schema_chain.domain_initializer(version)(path)
        assert S5cStore(path, P).cursor_table == "prospective_outbox_cursor"
        assert TERMINAL_TABLES[1] == "prospective_outbox_cursor_v52"
