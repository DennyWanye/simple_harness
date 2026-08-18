#!/usr/bin/env python3
"""Black-box API backward-compatibility check between two simple_harness SDK wheels.

Binding: S1-AC-3 / TO-S1-3 (plans/2026-08-19-sdk-usability-optimization/acceptance.md).
Plan anchor: Slice 1 Task 6.

Compares OLD wheel against NEW wheel on three dimensions, purely by static
inspection (zipfile + ast). This script NEVER imports simple_harness itself —
the wheels under test are treated as opaque archives.

Dimensions (assertion for each: NO REMOVALS; additions are allowed):
  1. Top-level ``simple_harness/__init__.py`` ``__all__`` export set.
  2. Inventory of ``.py`` module files inside the ``simple_harness/`` package.
  3. Field-name set of the ``RuntimePorts`` class in ``simple_harness/runtime/kernel.py``.

Usage:
    python api_compat_check.py OLD_WHEEL.whl NEW_WHEEL.whl

Exit codes:
    0  PASS — no deletions on any dimension
    1  FAIL — at least one deletion (details printed to stdout)
    2  ORACLE ERROR — the script could not resolve a dimension (e.g. __all__
       not statically resolvable, kernel.py missing). Exit 2 is NOT a PASS:
       the gate cannot prove compatibility and must be investigated.

Stdlib only. Python >= 3.11.
"""
from __future__ import annotations

import ast
import sys
import zipfile
from pathlib import Path

PACKAGE_PREFIX = "simple_harness/"
INIT_MEMBER = "simple_harness/__init__.py"
KERNEL_MEMBER = "simple_harness/runtime/kernel.py"
RUNTIME_PORTS_CLASS = "RuntimePorts"


# ---------------------------------------------------------------- extraction

def _read_member(wheel: Path, member: str) -> str:
    try:
        with zipfile.ZipFile(wheel) as zf:
            names = set(zf.namelist())
            if member not in names:
                raise _OracleError(f"{wheel.name}: member {member!r} not found")
            return zf.read(member).decode("utf-8")
    except zipfile.BadZipFile as e:
        raise _OracleError(f"{wheel}: not a readable zip/wheel: {e}") from e


class _OracleError(Exception):
    """The oracle itself cannot resolve a dimension (distinct from FAIL)."""


def module_inventory(wheel: Path) -> set[str]:
    """Set of .py file paths inside the simple_harness/ package."""
    with zipfile.ZipFile(wheel) as zf:
        return {
            n
            for n in zf.namelist()
            if n.startswith(PACKAGE_PREFIX) and n.endswith(".py")
        }


def _string_constants(node: ast.AST) -> set[str]:
    """Collect every string constant under an expression node."""
    out: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.add(sub.value)
    return out


def top_level_all(wheel: Path) -> set[str]:
    """Statically resolve ``__all__`` from simple_harness/__init__.py.

    Resolution strategy: find a module-level assignment to ``__all__``; first
    try ast.literal_eval (plain list/tuple of strings); if that fails, fall
    back to collecting all string constants in the assigned expression
    (handles ``__all__ = [...] + [...]`` / ``sorted([...])`` shapes).
    Multiple assignments to ``__all__`` or an unresolvable expression is an
    oracle error, never a silent pass.
    """
    source = _read_member(wheel, INIT_MEMBER)
    tree = ast.parse(source, filename=f"{wheel.name}:{INIT_MEMBER}")
    assigns: list[ast.expr] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            assigns.append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(
            node.target, ast.Name
        ) and node.target.id == "__all__" and node.value is not None:
            assigns.append(node.value)
    if not assigns:
        raise _OracleError(f"{wheel.name}: no module-level __all__ assignment found")
    if len(assigns) > 1:
        raise _OracleError(
            f"{wheel.name}: multiple __all__ assignments ({len(assigns)}); "
            "oracle refuses to guess"
        )
    value = assigns[0]
    try:
        resolved = ast.literal_eval(value)
        if isinstance(resolved, (list, tuple)) and all(
            isinstance(x, str) for x in resolved
        ):
            return set(resolved)
    except (ValueError, SyntaxError, TypeError):
        pass
    names = _string_constants(value)
    if not names:
        raise _OracleError(
            f"{wheel.name}: __all__ expression not statically resolvable "
            "(no string constants found)"
        )
    return names


def runtime_ports_fields(wheel: Path) -> set[str]:
    """Field-name set of class ``RuntimePorts`` in runtime/kernel.py.

    Collects class-body ``AnnAssign`` targets (dataclass/NamedTuple style)
    and plain ``Assign`` targets. Names starting with ``_`` are excluded
    (private members are not part of the public API contract).
    """
    source = _read_member(wheel, KERNEL_MEMBER)
    tree = ast.parse(source, filename=f"{wheel.name}:{KERNEL_MEMBER}")
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == RUNTIME_PORTS_CLASS:
            fields: set[str] = set()
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(
                    item.target, ast.Name
                ):
                    fields.add(item.target.id)
                elif isinstance(item, ast.Assign):
                    for t in item.targets:
                        if isinstance(t, ast.Name):
                            fields.add(t.id)
            fields = {f for f in fields if not f.startswith("_")}
            if not fields:
                raise _OracleError(
                    f"{wheel.name}: class {RUNTIME_PORTS_CLASS} has no "
                    "resolvable public fields"
                )
            return fields
    raise _OracleError(
        f"{wheel.name}: class {RUNTIME_PORTS_CLASS} not found at module level "
        f"of {KERNEL_MEMBER}"
    )


# ---------------------------------------------------------------- comparison

def _diff_dimension(
    label: str, old: set[str], new: set[str]
) -> tuple[bool, list[str]]:
    removed = sorted(old - new)
    added = sorted(new - old)
    lines = [
        f"[{label}] old={len(old)} new={len(new)} "
        f"removed={len(removed)} added={len(added)}"
    ]
    if removed:
        lines.append(f"  REMOVED (compat violation): {removed}")
    if added:
        lines.append(f"  added (allowed): {added}")
    return (not removed), lines


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    old_wheel, new_wheel = Path(argv[1]), Path(argv[2])
    for w in (old_wheel, new_wheel):
        if not w.is_file():
            print(f"ORACLE ERROR: wheel not found: {w}")
            return 2

    print(f"OLD wheel: {old_wheel}")
    print(f"NEW wheel: {new_wheel}")
    print()

    try:
        dimensions = [
            ("top-level __all__", top_level_all(old_wheel), top_level_all(new_wheel)),
            ("module file inventory", module_inventory(old_wheel), module_inventory(new_wheel)),
            (
                "RuntimePorts fields",
                runtime_ports_fields(old_wheel),
                runtime_ports_fields(new_wheel),
            ),
        ]
    except _OracleError as e:
        print(f"ORACLE ERROR: {e}")
        return 2

    all_ok = True
    for label, old_set, new_set in dimensions:
        ok, lines = _diff_dimension(label, old_set, new_set)
        all_ok = all_ok and ok
        for ln in lines:
            print(ln)
    print()

    if all_ok:
        print("PASS: no deletions on any dimension (additions allowed) — "
              "S1-AC-3 / TO-S1-3 satisfied")
        return 0
    print("FAIL: deletions detected — new wheel is NOT a backward-compatible "
          "superset of the old wheel (S1-AC-3 / TO-S1-3 violated)")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
