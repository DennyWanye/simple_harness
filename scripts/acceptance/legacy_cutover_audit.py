#!/usr/bin/env python3
"""Generate the R5.5 source lock and audit the completed R6 cutover."""

from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
PLAN_DIR = ROOT / "plans/2026-07-20-agent-harness-simplification"
DEFAULT_MANIFEST = PLAN_DIR / "legacy_cutover_spans.json"
AUTHORITY_MANIFEST = PLAN_DIR / "harness_authorities.json"
FACTORY_MODULES = (
    "backend/deskpet/harness/adapters/product_composition.py",
    "backend/deskpet/harness/adapters/subagent_registry.py",
)
FACTORY_SYMBOLS = (
    "build_product_harness_composition",
    "build_harness_subagent_registry",
)
ROOT_SPECS = (
    ("backend/main.py", "control_channel._run_chat"),
    ("backend/main.py", "control_channel._run_chat_with_timeout"),
    ("backend/main.py", "lifespan._auto_resume_dispatch"),
    ("backend/agent/legacy_agent_loop_tool_runtime.py", "LegacyAgentLoopToolRuntime.run_round"),
    ("backend/agent/legacy_subagent_bridge.py", "LegacySubagentBridge.drain"),
    ("backend/pipeline/voice_pipeline.py", "VoicePipeline._run_legacy_chat_stream"),
    ("backend/pipeline/voice_pipeline.py", "VoicePipeline._run_with_tools"),
    ("backend/deskpet/agent/subagent_registry.py", "SubagentRegistry"),
    ("backend/main.py", "_maybe_codify_skill"),
    ("backend/main.py", "_ppt_outline_propose"),
    ("backend/main.py", "_start_workflow_ppt_outline_resume"),
    ("backend/main.py", "_wire_ppt_pro_services_for_startup"),
    ("backend/deskpet/tools/ppt_tools.py", "_ppt_pro_orchestrate"),
    ("backend/deskpet/tools/ppt_tools.py", "_handle_ppt_pro"),
)


def _iter_backend_python(root: Path):
    """Yield project sources without descending into managed runtimes/caches."""

    for current, directories, filenames in os.walk(root):
        directories[:] = sorted(
            name
            for name in directories
            if not name.startswith(".")
            and name
            not in {
                "__pycache__",
                "assets",
                "bin",
                "build",
                "data",
                "dist",
                "dist-msi",
                "dist-portable",
                "models",
                "node_modules",
                "temp",
                "userdata",
            }
        )
        base = Path(current)
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                yield base / filename


class CutoverAuditError(RuntimeError):
    """Raised when cutover evidence cannot be proved exactly."""


@dataclass(frozen=True, slots=True)
class Span:
    path: str
    qualname: str
    kind: str
    line: int
    end_line: int
    source_hash: str
    normalized_ast_hash: str
    statement_count: int
    direct_callees: tuple[str, ...]
    root: bool

    @property
    def key(self) -> str:
        return f"{self.path}::{self.qualname}"


def _git_executable() -> str:
    found = shutil.which("git")
    if found:
        return found
    bundled = Path(
        "C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/"
        "dependencies/native/git/cmd/git.exe"
    )
    if bundled.is_file():
        return str(bundled)
    raise CutoverAuditError("git executable is required")


def _git(*args: str, repo: Path = ROOT, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        [_git_executable(), *args],
        cwd=repo,
        capture_output=True,
        text=not binary,
        check=False,
    )
    if result.returncode:
        error = result.stderr if isinstance(result.stderr, str) else result.stderr.decode()
        raise CutoverAuditError(f"git {' '.join(args)} failed: {error.strip()}")
    return result.stdout


def _full_commit(commit: str, *, repo: Path = ROOT) -> str:
    return str(_git("rev-parse", f"{commit}^{{commit}}", repo=repo)).strip()


def _content(commit: str, path: str, *, repo: Path = ROOT) -> bytes:
    return _git("show", f"{commit}:{path}", repo=repo, binary=True)  # type: ignore[return-value]


def _normalized(value: bytes | str) -> str:
    text = value.decode("utf-8-sig") if isinstance(value, bytes) else value
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _hash(value: bytes | str) -> str:
    return hashlib.sha256(_normalized(value).encode("utf-8")).hexdigest()


def _ast_hash(node: ast.AST) -> str:
    return hashlib.sha256(
        ast.dump(node, annotate_fields=True, include_attributes=False).encode("utf-8")
    ).hexdigest()


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return "<dynamic>"


def _direct_callees(node: ast.AST) -> tuple[str, ...]:
    nested = {
        id(item)
        for child in ast.iter_child_nodes(node)
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        for item in ast.walk(child)
    }
    return tuple(
        sorted(
            {
                _call_name(item.func)
                for item in ast.walk(node)
                if isinstance(item, ast.Call) and id(item) not in nested
            }
        )
    )


def _symbols(
    source: str, path: str, tree: ast.AST | None = None
) -> dict[str, tuple[ast.AST, str]]:
    tree = tree or ast.parse(source, filename=path)
    lines = source.splitlines()
    found: dict[str, tuple[ast.AST, str]] = {}

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def record(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            qualname = ".".join((*self.stack, node.name))
            found[qualname] = (
                node,
                "\n".join(lines[node.lineno - 1 : int(node.end_lineno or node.lineno)]),
            )
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_ClassDef = record
        visit_FunctionDef = record
        visit_AsyncFunctionDef = record

    Visitor().visit(tree)
    return found


def _assignment_symbols(
    source: str, path: str, tree: ast.AST | None = None
) -> dict[str, tuple[ast.AST, str]]:
    tree = tree or ast.parse(source, filename=path)
    lines = source.splitlines()
    found: dict[str, tuple[ast.AST, str]] = {}
    class_name: str | None = None
    function_name: str | None = None

    class Visitor(ast.NodeVisitor):
        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            nonlocal class_name
            previous, class_name = class_name, node.name
            self.generic_visit(node)
            class_name = previous

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            nonlocal function_name
            previous, function_name = function_name, node.name
            self.generic_visit(node)
            function_name = previous

        visit_AsyncFunctionDef = visit_FunctionDef

        def record(self, target: ast.AST, node: ast.AST) -> None:
            qualname = ""
            if isinstance(target, ast.Name) and function_name is None:
                qualname = f"<module>.{target.id}"
            elif (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and class_name
                and function_name == "__init__"
            ):
                qualname = f"{class_name}.{target.attr}"
            if qualname:
                found.setdefault(
                    qualname,
                    (
                        node,
                        "\n".join(
                            lines[node.lineno - 1 : int(getattr(node, "end_lineno", node.lineno))]
                        ),
                    ),
                )

        def visit_Assign(self, node: ast.Assign) -> None:
            for target in node.targets:
                self.record(target, node)
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            self.record(node.target, node)
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


def _span(path: str, qualname: str, node: ast.AST, source: str, *, root: bool) -> Span:
    return Span(
        path=path,
        qualname=qualname,
        kind=("owner" if qualname.startswith("<module>.") or not isinstance(
            node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) else type(node).__name__),
        line=int(getattr(node, "lineno", 0)),
        end_line=int(getattr(node, "end_lineno", getattr(node, "lineno", 0))),
        source_hash=_hash(source),
        normalized_ast_hash=_ast_hash(node),
        statement_count=sum(isinstance(item, ast.stmt) for item in ast.walk(node)),
        direct_callees=_direct_callees(node),
        root=root,
    )


def _owner_specs(
    source_commit: str | None = None, *, repo: Path = ROOT
) -> tuple[tuple[str, str], ...]:
    if source_commit is None:
        payload = json.loads(AUTHORITY_MANIFEST.read_text(encoding="utf-8"))
    else:
        payload = json.loads(_normalized(_content(source_commit, AUTHORITY_MANIFEST.relative_to(ROOT).as_posix(), repo=repo)))
    owners = [
        (str(item["path"]), str(item["symbol"]))
        for item in payload.get("items", ())
        if item.get("authority") == "legacy_survivor"
    ]
    if len(owners) != 15 or len(set(owners)) != 15:
        raise CutoverAuditError("legacy survivor authority set must contain exactly 15 owners")
    return tuple(owners)


def _resolve_local_callee(
    callee: str, current_qualname: str, symbols: Mapping[str, Any]
) -> str | None:
    leaf = callee.rsplit(".", 1)[-1]
    prefixes = current_qualname.split(".")[:-1]
    candidates = [".".join(prefixes[:index] + [leaf]) for index in range(len(prefixes), -1, -1)]
    candidates += [name for name in symbols if name.rsplit(".", 1)[-1] == leaf]
    unique = list(dict.fromkeys(name for name in candidates if name in symbols))
    return unique[0] if len(unique) == 1 else None


def _load_dynamic(path: Path, source_commit: str) -> dict[str, Any]:
    if not path.is_file():
        raise CutoverAuditError(f"dynamic stack evidence is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or payload.get("complete") is not True:
        raise CutoverAuditError("dynamic stack evidence must be schema 1 and complete=true")
    if _full_commit(str(payload.get("captured_commit", ""))) != source_commit:
        raise CutoverAuditError("dynamic stack evidence was not captured from source commit")
    stacks = payload.get("stacks")
    if not isinstance(stacks, list) or not stacks:
        raise CutoverAuditError("dynamic stack evidence must contain at least one stack")
    for item in stacks:
        if not item.get("stack_id") or not isinstance(item.get("legacy_frames"), list):
            raise CutoverAuditError("dynamic stack entry is incomplete")
        for frame in item["legacy_frames"]:
            if set(frame) != {"path", "qualname"} or not all(frame.values()):
                raise CutoverAuditError("dynamic legacy frame is not exact")
    return payload


def generate_manifest(
    *, source_commit: str, dynamic_stacks: Path, repo: Path = ROOT
) -> dict[str, Any]:
    source_commit = _full_commit(source_commit, repo=repo)
    specs = list(ROOT_SPECS) + list(_owner_specs(source_commit, repo=repo))
    by_path: dict[str, dict[str, tuple[ast.AST, str]]] = {}
    selected: dict[str, Span] = {}
    pending = list(specs)
    root_keys = {f"{path}::{qualname}" for path, qualname in specs}
    while pending:
        path, qualname = pending.pop(0)
        if path not in by_path:
            source = _normalized(_content(source_commit, path, repo=repo))
            by_path[path] = {**_symbols(source, path), **_assignment_symbols(source, path)}
        symbols = by_path[path]
        found = symbols.get(qualname)
        if found is None:
            raise CutoverAuditError(f"cutover symbol is missing: {path}::{qualname}")
        key = f"{path}::{qualname}"
        if key in selected:
            continue
        node, segment = found
        item = _span(path, qualname, node, segment, root=key in root_keys)
        selected[key] = item
        for callee in item.direct_callees:
            resolved = _resolve_local_callee(callee, qualname, symbols)
            if resolved is not None and f"{path}::{resolved}" not in selected:
                pending.append((path, resolved))

    dynamic = _load_dynamic(dynamic_stacks, source_commit)
    dynamic_keys = {
        f"{frame['path']}::{frame['qualname']}"
        for stack in dynamic["stacks"]
        for frame in stack["legacy_frames"]
    }
    missing_dynamic = sorted(dynamic_keys - set(selected))
    if missing_dynamic:
        raise CutoverAuditError(
            "dynamic legacy frames are absent from static manifest: "
            + ", ".join(missing_dynamic)
        )
    paths = sorted({item.path for item in selected.values()})
    files = [
        {
            "path": path,
            "source_hash": _hash(_content(source_commit, path, repo=repo)),
            "git_blob": str(_git("rev-parse", f"{source_commit}:{path}", repo=repo)).strip(),
        }
        for path in paths
    ]
    return {
        "schema_version": 1,
        "source_commit": source_commit,
        "owner_count": 15,
        "roots": [
            {"path": path, "qualname": qualname} for path, qualname in ROOT_SPECS
        ],
        "files": files,
        "spans": [asdict(selected[key]) for key in sorted(selected)],
        "dynamic_evidence": dynamic,
    }


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CutoverAuditError(f"legacy cutover manifest is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise CutoverAuditError("unsupported legacy cutover manifest schema")
    return payload


def audit_dormant_factories(*, repo: Path = ROOT) -> dict[str, Any]:
    main = (repo / "backend/main.py").read_text(encoding="utf-8-sig")
    schema = (repo / "backend/deskpet/workflows/store/schema.py").read_text(
        encoding="utf-8-sig"
    )
    direct_mentions = sorted(name for name in FACTORY_SYMBOLS if name in main)
    module_mentions = sorted(path for path in FACTORY_MODULES if path in main)
    imports: dict[str, set[str]] = {}
    for path in _iter_backend_python(repo / "backend"):
        parts = path.relative_to(repo / "backend").parts
        if (
            "tests" in parts
            or any(part.startswith(".") for part in parts)
            or "__pycache__" in parts
        ):
            continue
        rel = path.relative_to(repo).as_posix()
        module = rel[:-3].replace("/", ".")
        imports[module] = set()
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports[module].update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports[module].add(node.module)
    reachable = {"backend.main"}
    queue = ["backend.main"]
    while queue:
        module = queue.pop()
        for target in imports.get(module, ()):
            normalized = target if target.startswith("backend.") else f"backend.{target}"
            if normalized in imports and normalized not in reachable:
                reachable.add(normalized)
                queue.append(normalized)
    factory_modules = {path[:-3].replace("/", ".") for path in FACTORY_MODULES}
    reached_factories = sorted(factory_modules & reachable)
    legacy_default = "1,0,'legacy',NULL,0" in "".join(schema.split())
    activation_wired = all(
        marker in main
        for marker in ("build_product_harness_composition", "_activate_product_harness")
    )
    product_factory_reachable = (
        "build_product_harness_composition" in direct_mentions
        and "backend.deskpet.harness.adapters.product_composition" in reached_factories
    )
    return {
        "direct_factory_mentions": direct_mentions,
        "direct_module_mentions": module_mentions,
        "reachable_factory_modules": reached_factories,
        "production_owner": "kernel/1" if activation_wired and product_factory_reachable else "unknown",
        "schema_starts_fail_closed": legacy_default,
        "passed": (
            legacy_default
            and activation_wired
            and product_factory_reachable
            and not module_mentions
        ),
    }


def audit_readiness(manifest: Mapping[str, Any], *, repo: Path = ROOT) -> dict[str, Any]:
    source_commit = _full_commit(str(manifest.get("source_commit", "")), repo=repo)
    files_ok = True
    for item in manifest.get("files", ()):
        path = str(item.get("path", ""))
        locked = _content(source_commit, path, repo=repo)
        current = (repo / path).read_bytes() if (repo / path).is_file() else b""
        blob = str(_git("rev-parse", f"{source_commit}:{path}", repo=repo)).strip()
        files_ok &= item.get("source_hash") == _hash(locked) == _hash(current)
        files_ok &= item.get("git_blob") == blob
    spans = {f"{item['path']}::{item['qualname']}": item for item in manifest.get("spans", ())}
    owners = {f"{path}::{symbol}" for path, symbol in _owner_specs(source_commit, repo=repo)}
    owner_ok = int(manifest.get("owner_count", -1)) == 15 and owners <= set(spans)
    roots = {f"{item['path']}::{item['qualname']}" for item in manifest.get("roots", ())}
    root_ok = roots == {f"{path}::{name}" for path, name in ROOT_SPECS}
    dynamic = manifest.get("dynamic_evidence", {})
    dynamic_keys = {
        f"{frame['path']}::{frame['qualname']}"
        for stack in dynamic.get("stacks", ())
        for frame in stack.get("legacy_frames", ())
    }
    dynamic_ok = (
        dynamic.get("complete") is True
        and _full_commit(str(dynamic.get("captured_commit", "")), repo=repo) == source_commit
        and bool(dynamic_keys)
        and dynamic_keys <= set(spans)
    )
    dormant = audit_dormant_factories(repo=repo)
    checks = {
        "production_blobs_match_source_commit": bool(files_ok),
        "legacy_owners_exactly_15_and_covered": owner_ok,
        "static_roots_exact_and_covered": root_ok and roots <= set(spans),
        "dynamic_legacy_stacks_complete_and_covered": dynamic_ok,
        "dormant_factories_unreachable_from_main": dormant["passed"],
        "production_owner_is_legacy_0": dormant["production_owner"] == "legacy/0",
    }
    return {"mode": "readiness", "checks": checks, "dormant": dormant, "passed": all(checks.values())}


def audit_cutover(
    manifest: Mapping[str, Any], *, live_stacks: Path, repo: Path = ROOT
) -> dict[str, Any]:
    protected = {
        f"{path}::{name}" for path, name in (
            *ROOT_SPECS,
            *_owner_specs(str(manifest["source_commit"]), repo=repo),
        )
    }
    locked = [item for item in manifest.get("spans", ())
              if f"{item['path']}::{item['qualname']}" in protected]
    current: list[tuple[str, str, ast.AST, str, list[str], Counter[str]]] = []
    sources: dict[str, str] = {}
    line_index: dict[str, set[int]] = {}
    for path in _iter_backend_python(repo / "backend"):
        parts = path.relative_to(repo / "backend").parts
        if (
            (parts and parts[0] in {"tests", "scripts"})
            or any(part.startswith(".") for part in parts)
            or "__pycache__" in parts
        ):
            continue
        rel = path.relative_to(repo).as_posix()
        source = path.read_text(encoding="utf-8-sig")
        sources[rel] = source
        tree = ast.parse(source, filename=rel)
        symbols = {**_symbols(source, rel, tree), **_assignment_symbols(source, rel, tree)}
        for name, (node, segment) in symbols.items():
            lines = [line.strip() for line in segment.splitlines() if line.strip()]
            index = len(current)
            current.append((rel, name, node, segment, lines, Counter(lines)))
            for line in set(lines):
                line_index.setdefault(line, set()).add(index)
    exact: list[str] = []
    similar: list[str] = []
    references: list[str] = []
    for old in locked:
        old_key = f"{old['path']}::{old['qualname']}"
        old_source = _normalized(_content(str(manifest["source_commit"]), str(old["path"]), repo=repo))
        old_symbols = {**_symbols(old_source, str(old["path"])), **_assignment_symbols(old_source, str(old["path"]))}
        old_segment = old_symbols[str(old["qualname"])][1]
        old_lines = [line.strip() for line in old_segment.splitlines() if line.strip()]
        old_counts = Counter(old_lines)
        candidates = set().union(*(line_index.get(line, set()) for line in set(old_lines)))
        for index in candidates:
            path, name, node, segment, new_lines, new_counts = current[index]
            key = f"{path}::{name}"
            if (int(old["statement_count"]) < 4
                    and str(old["qualname"]).rsplit(".", 1)[-1] != name.rsplit(".", 1)[-1]):
                continue
            if _ast_hash(node) == old["normalized_ast_hash"]:
                exact.append(f"{old_key}->{key}")
                continue
            common = sum((old_counts & new_counts).values())
            if (2 * common / max(1, len(old_lines) + len(new_lines)) < 0.70
                    and common / max(1, len(old_lines)) < 0.60):
                continue
            matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
            contiguous = matcher.find_longest_match().size / max(1, len(old_lines))
            if matcher.ratio() >= 0.70 or contiguous >= 0.60:
                similar.append(f"{old_key}->{key}")
        qualname = str(old["qualname"])
        leaf = qualname.rsplit(".", 1)[-1].removeprefix("<module>")
        if leaf and old_key in protected:
            if old.get("kind") == "owner" or qualname.startswith("<module>."):
                token, paths = leaf, {str(old["path"])}
            elif "." in qualname:
                token, paths = leaf, {str(old["path"])}
            else:
                token, paths = leaf, set(sources)
            symbol_pattern = re.compile(rf"(?<!\w){re.escape(token)}(?!\w)")
            references.extend(
                f"{old_key}->{path}" for path, source in sources.items()
                if path in paths and symbol_pattern.search(source)
            )
    live = json.loads(live_stacks.read_text(encoding="utf-8")) if live_stacks.is_file() else {}
    live_keys = {
        f"{frame['path']}::{frame['qualname']}"
        for stack in live.get("stacks", ())
        for frame in stack.get("legacy_frames", ())
    }
    locked_keys = {f"{item['path']}::{item['qualname']}" for item in locked}
    checks = {
        "exact_ast_absent": not exact,
        "similar_fingerprint_absent": not similar,
        "legacy_symbol_references_absent": not references,
        "legacy_roots_unreachable": all(key not in locked_keys for key in live_keys),
        "live_stack_complete_and_clean": live.get("complete") is True and not (live_keys & locked_keys),
    }
    return {
        "mode": "cutover",
        "checks": checks,
        "exact_matches": sorted(set(exact)),
        "similar_matches": sorted(set(similar)),
        "references": sorted(set(references)),
        "passed": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--generate", action="store_true")
    mode.add_argument("--readiness", action="store_true")
    mode.add_argument("--cutover", action="store_true")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source-commit")
    parser.add_argument("--dynamic-stacks", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.generate:
        if not args.source_commit or args.dynamic_stacks is None:
            parser.error("--generate requires --source-commit and --dynamic-stacks")
        result = generate_manifest(
            source_commit=args.source_commit,
            dynamic_stacks=args.dynamic_stacks.resolve(),
        )
    else:
        manifest = _load_manifest(args.manifest.resolve())
        if args.readiness:
            result = audit_readiness(manifest)
        else:
            if args.dynamic_stacks is None:
                parser.error("--cutover requires --dynamic-stacks")
            result = audit_cutover(manifest, live_stacks=args.dynamic_stacks.resolve())
    output = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    if args.generate:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(output, encoding="utf-8")
    print(output, end="")
    return 0 if args.generate or result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
