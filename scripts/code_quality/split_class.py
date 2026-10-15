#!/usr/bin/env python3
"""代码质量整治阶段 5：把巨型类的方法按业务域原样搬进 mixin 模块（纯移动）。

用法：
    python3 scripts/code_quality/split_class.py <源文件> <类名> <分组.json> [--dry-run]

分组 JSON：
    {"package": "uow_domains",            # 源文件同目录下新建的子包
     "support": "support",                 # 多处共用的模块级辅助定义放这里
     "groups": [{"module": "core", "class": "UowCore", "core": true, ...},
                {"module": "audit", "class": "RunAuditMixin",
                 "doc": "Run, stage and command operation audit reads.",
                 "methods": ["read_run_terminal_record", ...]}, ...]}

规则：
- 方法源码逐字搬走（含紧挨在上方的注释与空行）；方法体里的相对导入多加一个点。
- 分组可写 "bases"：把原类的某个基类改挂到这个 mixin 上（原类不再直接继承它）。
- 标 "core" 的分组是公共基类：原类的 `__slots__` 搬进它，各 mixin 都继承它（mixin 用到的共用方法放这里）。
- mixin 类 `__slots__ = ()`，按分组顺序排在原类基类之前；原类保留没列进分组的方法。
- mixin 调用别的 mixin 的方法时，在类里 `if TYPE_CHECKING:` 块写同签名的存根（只给类型检查看；
  分组 JSON 写 "stubs_in_core": true 时集中写在公共基类里一处）；
  `原类名.方法` 的写法改成 `所属类名.方法` 并导入该类。
- 源文件的模块级辅助定义（函数、类、常量）按使用方放置：只被一个 mixin 模块用 → 放进该模块；
  被多个 mixin 模块用 → 放进 support；只被源文件用 → 留下。辅助定义之间的依赖做闭包。
- 每个新模块的导入从源文件导入语句里按用到的名字筛出（`if TYPE_CHECKING:` 块照样保留在块里）。
- 源文件删去不再用到的导入。收尾自动跑 ruff 导入排序与格式化；mypy 与相关测试另行验证。
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import re
import subprocess
import sys
import textwrap
import tokenize
from pathlib import Path

if sys.version_info < (3, 12):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

REPO = Path(__file__).resolve().parents[2]
RUFF_CONFIG = REPO / "scripts/code_quality/ruff.toml"
MAIN = "<main>"


def names_in(text: str) -> set[str]:
    """一段代码里出现的全部名字（Name 与属性链的根），含注解。"""
    tree = ast.parse(textwrap.dedent(text))
    out = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for n in ast.walk(tree):  # 字符串注解里的名字
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.isidentifier():
            out.add(n.value)
    return out


def bound_names(stmt: ast.stmt) -> list[tuple[str, ast.alias]]:
    out = []
    for alias in stmt.names:  # type: ignore[attr-defined]
        if isinstance(stmt, ast.Import):
            out.append(((alias.asname or alias.name).split(".")[0], alias))
        else:
            out.append((alias.asname or alias.name, alias))
    return out


def render_import(stmt: ast.stmt, keep: list[ast.alias], extra_level: int) -> str:
    if isinstance(stmt, ast.Import):
        return ast.unparse(ast.Import(names=keep))
    assert isinstance(stmt, ast.ImportFrom)
    level = stmt.level + (extra_level if stmt.level else 0)
    return ast.unparse(ast.ImportFrom(module=stmt.module, names=keep, level=level))


class Source:
    def __init__(self, path: Path, class_name: str) -> None:
        self.path = path
        self.text = path.read_text(encoding="utf-8")
        self.lines = self.text.splitlines(keepends=True)
        self.tree = ast.parse(self.text)
        self.cls = next(
            n for n in self.tree.body if isinstance(n, ast.ClassDef) and n.name == class_name
        )
        # 模块级导入：(语句, 是否在 TYPE_CHECKING 块里)
        self.imports: list[tuple[ast.stmt, bool]] = []
        # 模块级辅助定义：名字 → 语句
        self.helpers: dict[str, ast.stmt] = {}
        for node in self.tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self.imports.append((node, False))
            elif isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
                self.imports.extend((s, True) for s in node.body)
            elif node is self.cls:
                continue
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.helpers[node.name] = node
            elif isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id != "__all__":
                    self.helpers[target.id] = node
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                self.helpers[node.target.id] = node

    def span(self, node: ast.stmt, siblings: list[ast.stmt]) -> tuple[int, int]:
        """节点的行范围（0 基，含上方紧挨的注释与空行，直到上一个兄弟节点之后）。"""
        index = siblings.index(node)
        start = siblings[index - 1].end_lineno if index else node.lineno - 1  # type: ignore[assignment]
        first = node.decorator_list[0].lineno if getattr(node, "decorator_list", None) else node.lineno
        if not index:
            start = first - 1
        return start, node.end_lineno  # type: ignore[return-value]

    def chunk(self, node: ast.stmt, siblings: list[ast.stmt]) -> str:
        start, end = self.span(node, siblings)
        return "".join(self.lines[start:end])


def deepen_relative_imports(text: str) -> str:
    return re.sub(r"^(\s*from )(\.+)", lambda m: m.group(1) + "." + m.group(2), text, flags=re.M)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("class_name")
    parser.add_argument("spec")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = Path(args.source).resolve()
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    src = Source(path, args.class_name)
    body = src.cls.body
    methods = {
        n.name: n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    owner: dict[str, str] = {}
    for group in spec["groups"]:
        for name in group["methods"]:
            if name not in methods:
                sys.exit(f"类里没有方法 {name}")
            if name in owner:
                sys.exit(f"方法 {name} 同时分在 {owner[name]} 与 {group['module']}")
            owner[name] = group["module"]

    core = next((g for g in spec["groups"] if g.get("core")), None)
    slots = next(
        (n for n in body if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "__slots__"),
        None,
    )
    cls_of = {g["module"]: g["class"] for g in spec["groups"]}
    extra_imports: dict[str, set[str]] = {g["module"]: set() for g in spec["groups"]}

    def wanted_stubs(module: str, code: str) -> set[str]:
        """本模块用到、但定义在别的 mixin 里的方法。"""
        tree = ast.parse(textwrap.dedent(code)) if code.strip() else ast.Module(body=[])
        return set(
            {
                n.attr
                for n in ast.walk(tree)
                if isinstance(n, ast.Attribute)
                and isinstance(n.value, ast.Name)
                and n.value.id in ("self", "cls")
                and n.attr in owner
                and owner[n.attr] not in (module, core and core["module"])
            }
        )

    def render_stubs(wanted: set[str]) -> str:
        """类型检查用的同签名存根（`if TYPE_CHECKING:` 块，运行期不存在）。"""
        out = []
        for name in sorted(wanted):
            node = ast.parse(textwrap.dedent(src.chunk(methods[name], body))).body[0]
            node.body = [ast.Expr(ast.Constant(...))]  # type: ignore[attr-defined]
            out.append(textwrap.indent(ast.unparse(node), "        "))
        return ("    if TYPE_CHECKING:\n" + "\n".join(out) + "\n\n") if out else ""

    def retarget_class_refs(module: str, code: str) -> str:
        def sub(m: re.Match[str]) -> str:
            target = owner.get(m.group(1))
            if target is None:
                return m.group(0)
            if target != module:
                extra_imports[module].add(target)
            return f"{cls_of[target]}.{m.group(1)}"

        # 只改代码里的 `原类名.方法`（按词法记号找），字符串与注释原样不动（如 writer="HtnStore.x" 会落库）
        toks = list(tokenize.generate_tokens(io.StringIO(code).readline))
        lines = code.splitlines(keepends=True)
        edits = []
        for i in range(len(toks) - 2):
            a, dot, b = toks[i], toks[i + 1], toks[i + 2]
            if (a.type == tokenize.NAME and a.string == args.class_name and dot.string == "."
                    and b.type == tokenize.NAME and (i == 0 or toks[i - 1].string != ".")):
                m = re.match(r"(\w+)\.(\w+)", f"{a.string}.{b.string}")
                assert m is not None
                edits.append((a.start, b.end, sub(m)))
        for (r1, c1), (r2, c2), rep in reversed(edits):
            assert r1 == r2
            line = lines[r1 - 1]
            lines[r1 - 1] = line[:c1] + rep + line[c2:]
        return "".join(lines)

    # 每个模块（含源文件余下部分）的代码文本
    texts: dict[str, list[str]] = {g["module"]: [] for g in spec["groups"]}
    remaining_body: list[str] = []
    for node in body:
        chunk = src.chunk(node, body)
        if core and node is slots:
            texts[core["module"]].append(chunk)
            remaining_body.append("    __slots__ = ()\n")
        elif core and isinstance(node, (ast.Assign, ast.AnnAssign)):
            texts[core["module"]].append(chunk)  # 类属性（常量）搬进公共基类，各 mixin 经 self 读得到
        elif getattr(node, "name", None) in owner:
            texts[owner[node.name]].append(chunk)  # type: ignore[union-attr]
        else:
            remaining_body.append(chunk)

    # 辅助定义放置：使用方集合做闭包，直到稳定
    helper_text = {name: src.chunk(node, src.tree.body) for name, node in src.helpers.items()}
    helper_deps = {
        name: (names_in(text) & set(src.helpers)) - {name} for name, text in helper_text.items()
    }
    helper_nodes = list(src.helpers.values())
    top_rest = "".join(
        src.chunk(n, src.tree.body)
        for n in src.tree.body
        if not isinstance(n, (ast.Import, ast.ImportFrom))
        and not (isinstance(n, ast.If) and "TYPE_CHECKING" in ast.unparse(n.test))
        and n is not src.cls
        and n not in helper_nodes
    )
    main_used = names_in("".join(remaining_body)) | names_in(top_rest)
    main_used |= {n for b in src.cls.bases for n in names_in(ast.unparse(b))}
    users: dict[str, set[str]] = {name: set() for name in src.helpers}
    used_by_module = {m: names_in("".join(t)) for m, t in texts.items()}
    used_by_module[MAIN] = main_used
    for module, used in used_by_module.items():
        for name in used & set(src.helpers):
            users[name].add(module)
    # 分组 JSON 写 "records": 模块名 时，源文件的模块级定义整族搬进子包的这个模块（源文件只组合类、经 __all__ 转出）
    records = spec.get("records")
    support = records or spec.get("support", "support")

    def place(name: str) -> str:
        if records:
            return records
        found = users[name] - {MAIN}
        if not found:
            return MAIN
        return next(iter(found)) if len(found) == 1 else support

    placement: dict[str, str] = {}
    while True:
        placement = {name: place(name) for name in src.helpers}
        changed = False
        for name, dests in helper_deps.items():
            where = placement[name]
            for dep in dests:
                if where not in users[dep]:
                    users[dep].add(where)
                    changed = True
        if not changed:
            break

    package = path.parent / spec["package"]
    module_names = [g["module"] for g in spec["groups"]]
    if support in module_names:
        sys.exit("support 模块名与分组重名")
    pkg_mod = f"{path.stem}"

    def import_block(used: set[str], extra_level: int) -> str:
        def render(in_block: bool, names: set[str]) -> list[str]:
            out = []
            for stmt, in_checking in src.imports:
                if in_checking != in_block:
                    continue
                keep = [alias for bound, alias in bound_names(stmt) if bound in names]
                if keep:
                    out.append(render_import(stmt, keep, extra_level))
            return out

        checking = render(True, used)
        plain = render(False, used | ({"TYPE_CHECKING"} if checking else set()))
        out = "\n".join(plain)
        if checking:
            out += "\n\nif TYPE_CHECKING:\n" + "\n".join("    " + line for line in checking)
        return out

    def helper_imports(module: str, used: set[str], *, from_main: bool) -> str:
        lines: dict[str, list[str]] = {}
        for name in sorted(used & set(src.helpers)):
            where = placement[name]
            if where == module:
                continue
            if where == MAIN:
                sys.exit(f"{module} 要用留在源文件的 {name}（会循环导入）")
            target = (
                f".{spec['package']}.{where}" if from_main else f".{where}"
            )
            lines.setdefault(target, []).append(name)
        return "\n".join(f"from {t} import {', '.join(ns)}" for t, ns in lines.items())

    header = "".join(src.lines[: src.tree.body[0].lineno - 1])  # 版权头
    outputs: dict[Path, str] = {}

    def module_file(module: str, doc: str, code: str) -> str:
        used = names_in(code)
        parts = [header + f'"""{doc}"""\n\nfrom __future__ import annotations\n']
        block = import_block(used - {"annotations"}, 1)
        helpers = helper_imports(module, used, from_main=False)
        classes = "\n".join(
            f"from .{m} import {cls_of[m]}" for m in sorted(extra_imports.get(module, ()))
        )
        if args.class_name in used:  # 注解里用到原类本身：只给类型检查导入（运行期会循环）
            classes += (
                f"\n\nif TYPE_CHECKING:\n    from ..{path.stem} import {args.class_name}"
            )
            if "TYPE_CHECKING" not in block:
                classes += "\nfrom typing import TYPE_CHECKING"
        parts.append("\n".join(x for x in (block, helpers, classes) if x))
        parts.append(code)
        text = "\n\n".join(p.rstrip("\n") for p in parts) + "\n"
        # 源文件经 `.子包.模块` 导入的名字，子包里的文件改成同包导入（从定义处导入）
        return re.sub(rf"^(\s*)from \.\.{re.escape(spec['package'])}\.", r"\1from .", text, flags=re.M)

    all_wanted: set[str] = set()
    for g in spec["groups"]:
        all_wanted |= wanted_stubs(g["module"], "".join(texts[g["module"]]))

    # support 模块
    support_names = [n for n in src.helpers if placement[n] == support]
    for module in [*module_names, support]:
        own = "".join(
            helper_text[n]
            for n in src.helpers  # 保持源文件里的先后顺序
            if placement[n] == module
        )
        if module == support:
            if not support_names:
                continue
            outputs[package / f"{support}.py"] = module_file(
                support,
                spec.get("records_doc" if records else "support_doc", "Shared helpers for the split class."),
                own,
            )
            continue
        group = next(g for g in spec["groups"] if g["module"] == module)
        moved_code = retarget_class_refs(module, deepen_relative_imports("".join(texts[module])))
        if spec.get("stubs_in_core") and core:
            # 跨领域调用的存根集中写在公共基类里一处，各 mixin 不再各写一份
            stubs = render_stubs(all_wanted - set(group["methods"])) if group.get("core") else ""
        else:
            stubs = render_stubs(wanted_stubs(module, "".join(texts[module])))
        if group.get("core"):
            head = f"class {group['class']}:\n"
            slot_line = ""
        else:
            bases = ([core["class"]] if core else []) + group.get("bases", [])
            head = f"class {group['class']}({', '.join(bases)}):\n" if bases else f"class {group['class']}:\n"
            slot_line = "    __slots__ = ()\n\n"
            if core:
                extra_imports[module].add(core["module"])
        cls_text = head + f'    """{group["doc"]}"""\n\n' + slot_line + stubs + moved_code
        code = (own + "\n\n" + cls_text) if own else cls_text
        outputs[package / f"{module}.py"] = module_file(module, group["doc"], code)
    outputs[package / "__init__.py"] = (
        header + f'"""Domain mixins composing {args.class_name} ({pkg_mod}.py)."""\n'
    )

    # 源文件：删搬走的方法与辅助定义，改基类，加导入，删不再用的导入
    drop: list[tuple[int, int]] = []
    for node in body:
        if getattr(node, "name", None) in owner or (
            core and node is not slots and isinstance(node, (ast.Assign, ast.AnnAssign))
        ):
            drop.append(src.span(node, body))
    for name, node in src.helpers.items():
        if placement[name] != MAIN:
            drop.append(src.span(node, src.tree.body))
    keep_lines = list(src.lines)
    replace: dict[int, str] = {}
    if core and slots is not None:  # 原类的 __slots__ 已搬进公共基类
        drop.append((slots.lineno - 1, slots.end_lineno))  # type: ignore[arg-type]
        replace[slots.lineno - 1] = "    __slots__ = ()\n"
    for start, end in sorted(drop, reverse=True):
        keep_lines[start:end] = [replace[start]] if start in replace else []
    new_text = "".join(keep_lines)
    mixins = ", ".join(g["class"] for g in spec["groups"] if not g.get("core"))
    moved_bases = {b for g in spec["groups"] for b in g.get("bases", [])}
    old_bases = ", ".join(ast.unparse(b) for b in src.cls.bases)
    head = f"class {args.class_name}({old_bases}):" if old_bases else f"class {args.class_name}:"
    kept_bases = [ast.unparse(b) for b in src.cls.bases if ast.unparse(b) not in moved_bases]
    if spec["groups"]:  # 没有分组时只搬模块级定义，类头不动
        if head not in new_text:
            sys.exit(f"找不到类头 {head}")
        new_text = new_text.replace(
            head, f"class {args.class_name}({', '.join([mixins, *kept_bases])}):", 1
        )
    tree = ast.parse(new_text)
    used_main = names_in(
        "\n".join(
            ast.get_source_segment(new_text, n) or ""
            for n in tree.body
            if not isinstance(n, (ast.Import, ast.ImportFrom))
            and not (isinstance(n, ast.If) and "TYPE_CHECKING" in ast.unparse(n.test))
        )
    )
    # 重写导入区：删掉原有的每条模块级导入（含 TYPE_CHECKING 块；散在代码中间的也删），
    # 在第一条导入的位置放新的导入块：原导入按用到的名字筛，加 mixin 与辅助定义导入
    first_import = min(s.lineno for s, _ in src.imports)
    lines = new_text.splitlines(keepends=True)
    import_nodes = [
        n
        for n in tree.body
        if isinstance(n, (ast.Import, ast.ImportFrom))
        or (isinstance(n, ast.If) and "TYPE_CHECKING" in ast.unparse(n.test))
    ]
    block = import_block(used_main | {"annotations"}, 0)
    mixin_imports = "\n".join(
        f"from .{spec['package']}.{g['module']} import {g['class']}"
        for g in spec["groups"]
        if not g.get("core")
    )
    helpers_for_main = helper_imports(MAIN, used_main, from_main=True)
    new_imports = "\n".join(x for x in (block, mixin_imports, helpers_for_main) if x) + "\n"
    for n in sorted(import_nodes, key=lambda n: n.lineno, reverse=True):
        del lines[n.lineno - 1 : n.end_lineno]
    lines.insert(first_import - 1, new_imports)
    new_text = "".join(lines)

    outputs[path] = new_text
    summary = {m: sum(t.count("\n") for t in texts[m]) for m in module_names}
    print("搬移行数：", json.dumps(summary, ensure_ascii=False))
    print("辅助定义放置：", json.dumps(
        {k: sorted(n for n in src.helpers if placement[n] == k)
         for k in sorted(set(placement.values()))}, ensure_ascii=False))
    if args.dry_run:
        return 0
    package.mkdir(exist_ok=True)
    for out_path, text in outputs.items():
        out_path.write_text(text, encoding="utf-8")
    files = [str(p) for p in outputs]
    ruff = [os.environ.get("RUFF") or str(REPO.parent / "simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff")]
    # 导入按名字筛时会把字符串常量里的同名词也算上，多出的导入由 F401 删掉
    subprocess.run([*ruff, "check", "--config", str(RUFF_CONFIG), "--select", "F401,I001",
                    "--fix", "--quiet", *files], check=False)
    subprocess.run([*ruff, "format", "--config", str(RUFF_CONFIG), "--quiet", *files], check=False)
    res = subprocess.run([*ruff, "check", "--config", str(RUFF_CONFIG), "--select",
                          "F401,F811,F821,F841", *files], capture_output=True, text=True)
    print(res.stdout[-4000:] or "ruff：无未定义/未使用名字")
    return 0


if __name__ == "__main__":
    sys.exit(main())
