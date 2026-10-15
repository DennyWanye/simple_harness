"""One import path per name for classes split into a ``<module>_domains`` subpackage (代码质量整治阶段 5C).

A split class keeps a public facade module (``storage/store.py``) that composes the class and
re-exports the records through ``__all__``; the records, the core base and the mixins live in the
subpackage (``storage/store_domains/``). The rule, from the stage 5 storage-split ruling:

* code outside the subpackage imports public names only from the facade;
* files inside the subpackage import from where a name is defined, never from the facade (the class
  the facade composes is defined there, so that one name may come from the facade);
* private names (leading underscore) are not re-exported by the facade: there is one path, the
  definition;
* every name imported from the facade is listed in its ``__all__``;
* the subpackage is never a public module.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from agent_orchestrator.public_api import PUBLIC_MODULES as ORCHESTRATOR_PUBLIC
from simple_harness.public_api import PUBLIC_MODULES as HARNESS_PUBLIC

SDK = Path(__file__).resolve().parents[1]
REPO = SDK.parents[1]
SRC = SDK / "src"
ROOTS = (SRC, SDK / "tests", SDK / "scripts", REPO / "backend", REPO / "scripts")
SKIP = {".venv", "vendor", "__pycache__", "node_modules", "dist", "build"}

#: 门面规则的显式例外（阶段5-偏差单-解环与门面规则.md 裁决）：这些下层模块经门面取名字会成环，
#: 只能从 ``<facade>_domains/records.py`` 直接导入，且只能从那里导入。
#: 条目：(导入方模块, records 模块, 名字)。
#: 加条目先写偏差单；每条在偏差单里附成环路径。
RECORDS_DIRECT = {
    (
        "agent_orchestrator.storage.source_records",
        "agent_orchestrator.storage.store_domains.records",
        "StoreError",
    ),
    (
        "agent_orchestrator.storage.assurance_work",
        "agent_orchestrator.storage.store_domains.records",
        "StoreConflict",
    ),
}

#: Facade modules whose class was split into ``<facade>_domains``.
FACADES = (
    "agent_orchestrator.orchestrator.commit_service",
    "agent_orchestrator.orchestrator.hierarchical_dispatch",
    "agent_orchestrator.storage.htn_store",
    "agent_orchestrator.storage.store",
    "simple_harness.runtime.kernel",
    "simple_harness.runtime.memory_protocol",
)


def _module_of(path: Path) -> str | None:
    try:
        return ".".join(path.relative_to(SRC).with_suffix("").parts).removesuffix(".__init__")
    except ValueError:
        return None


def _absolute(path: Path, node: ast.ImportFrom) -> str | None:
    if not node.level:
        return node.module
    own = _module_of(path)
    if own is None:
        return None
    parts = own.split(".")
    if path.name != "__init__.py":
        parts = parts[:-1]
    base = parts[: len(parts) - node.level + 1]
    return ".".join(base + ([node.module] if node.module else []))


def _imports() -> list[tuple[Path, str, str, int, bool]]:
    """(file, absolute module, imported name, line, type-only) for every ``from X import name``."""

    out = []
    for root in ROOTS:
        for path in root.rglob("*.py"):
            if set(path.parts) & SKIP or path == Path(__file__).resolve():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if not any(f.rsplit(".", 1)[-1] in text for f in FACADES):
                continue
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            typing_only = {
                id(n)
                for block in ast.walk(tree)
                if isinstance(block, ast.If) and "TYPE_CHECKING" in ast.unparse(block.test)
                for n in ast.walk(block)
            }
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = _absolute(path, node)
                    if module:
                        out += [
                            (path, module, a.name, node.lineno, id(node) in typing_only)
                            for a in node.names
                        ]
    return out


def _violations(
    facade: str,
    imports: list[tuple[Path, str, str, int, bool]],
    direct: set[tuple[str, str, str]] = RECORDS_DIRECT,
) -> list[str]:
    package = facade + "_domains"
    allowed = {
        (importer, name) for importer, records, name in direct if records == package + ".records"
    }
    public = set(importlib.import_module(facade).__all__)
    # 门面自己定义的名字（组合出来的类）在门面就是定义处，子包可以从这里导入
    tree = ast.parse(_facade_path(facade).read_text(encoding="utf-8"))
    own = {n.name for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    bad = []
    for path, module, name, line, typing_only in imports:
        where = f"{path.relative_to(REPO)}:{line} {module}.{name}"
        inside = (_module_of(path) or "").startswith(package + ".") or _module_of(path) == package
        importer = _module_of(path) or ""
        if module == facade and (importer, name) in allowed:
            bad.append(f"例外清单里的名字改回从门面导入：{where}")
        elif module == package + ".records" and (importer, name) in allowed and not typing_only:
            continue
        elif module == facade:
            if inside and name not in own:
                bad.append(f"子包里从门面导入：{where}")
            elif name not in public:
                bad.append(f"门面导入的名字不在 __all__：{where}")
        elif module.startswith(package + ".") and not inside and path != _facade_path(facade):
            if name in public:
                bad.append(f"公开名字绕过门面：{where}")
            elif not name.startswith("_") and not typing_only:
                # 只给类型检查用的导入（标注写公共基类）不是第二条运行时路径
                bad.append(f"子包内部名字被外部导入：{where}")
        elif (
            module.startswith(package + ".")
            and path == _facade_path(facade)
            and name.startswith("_")
        ):
            bad.append(f"门面转出私有名字：{where}")
    return bad


def _facade_path(facade: str) -> Path:
    return SRC / (facade.replace(".", "/") + ".py")


def test_each_name_has_one_import_path() -> None:
    imports = _imports()
    bad = [v for facade in FACADES for v in _violations(facade, imports)]
    assert bad == []


def _direct_imports(imports: list[tuple[Path, str, str, int, bool]]) -> set[tuple[str, str, str]]:
    # 门面自己、子包内部从 records 导入是常规写法；私有名字本来就只有定义处一条路径
    records = {facade + "_domains.records": facade for facade in FACADES}
    return {
        (_module_of(path) or "", module, name)
        for path, module, name, _, typing_only in imports
        if module in records
        and not typing_only
        and not name.startswith("_")
        and _module_of(path) != records[module]
        and not (_module_of(path) or "").startswith(module.rsplit(".", 1)[0] + ".")
    }


def test_records_direct_exceptions_match_the_list() -> None:
    """例外清单与实际的"子包外直连 records"导入双向相等：清单不多一条，实际不多一处。"""

    assert _direct_imports(_imports()) == RECORDS_DIRECT


def test_the_records_exception_guard_bites() -> None:
    """改坏检验三条：清单外加直连、清单内改回门面、清单多一条，都要被拦下。"""

    facade = "agent_orchestrator.storage.store"
    records = facade + "_domains.records"
    outsider = SRC / "agent_orchestrator" / "storage" / "_guard_probe.py"
    listed = SRC / "agent_orchestrator" / "storage" / "source_records.py"
    # 1. 清单外的文件直连 records 取公开名字
    assert _violations(facade, [(outsider, records, "StoreError", 1, False)])
    # 2. 清单内的文件改回门面导入
    assert _violations(facade, [(listed, facade, "StoreError", 1, False)])
    # 3. 清单多一条没有对应导入
    extra = {*RECORDS_DIRECT, ("agent_orchestrator.storage.schema", records, "StoreBusy")}
    assert _direct_imports(_imports()) != extra


def test_split_subpackages_are_never_public() -> None:
    public = (*HARNESS_PUBLIC, *ORCHESTRATOR_PUBLIC)
    assert [m for m in public if any(m.startswith(f + "_domains") for f in FACADES)] == []


def test_the_guard_sees_a_bypass(tmp_path: Path) -> None:
    """改坏检验：门面外直接从子包导入公开名字，守卫要报出来。"""

    facade = FACADES[-1]
    name = sorted(importlib.import_module(facade).__all__)[0]
    fake = SRC / "agent_orchestrator" / "_guard_probe.py"
    imports = [
        (fake, facade + "_domains.records", name, 1, False),
        (fake, facade, "_private", 2, False),
        (fake, facade + "_domains.core", "StoreCore", 3, False),
    ]
    assert len(_violations(facade, imports)) == 3
