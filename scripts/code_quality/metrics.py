#!/usr/bin/env python3
"""代码质量整治：统一的指标采集与棘轮检查（计划 0.1、0.10、0.12、1.6）。

用法：
    python3 scripts/code_quality/metrics.py collect [--slow] [--out FILE]
        采集全部指标，打印摘要；--out 写 JSON。--slow 额外跑 mypy 与 eslint。
    python3 scripts/code_quality/metrics.py check [--slow] [--baseline FILE] [--new-since REF]
        棘轮：任何指标比基线高就失败。--new-since 时，REF 之后新建的文件
        必须没有超 150 行的函数、没有复杂度超 15 的函数（新代码硬规则）。
        从旧文件原样搬来的函数（九成以上的行出自本次删掉的行）不算新代码，由全局棘轮管。
    python3 scripts/code_quality/metrics.py lower [--slow] [--baseline FILE]
        把基线下调到当前值（只降不升）。
    python3 scripts/code_quality/metrics.py rebase --keys K1,K2 --reason "原因"
        口径变化时（例如全库格式化把长行拆开、函数变长）把指定指标重设为当前值，
        原因记进基线的 _rebase_log。只用于口径变化，不许用来放过真实变差。
    python3 scripts/code_quality/metrics.py long-functions [--min 300] [--md FILE]
        列出超长函数（附录 A 的进度来源）；--md 输出带阶段归属的附录表。

口径：
- Python 范围：backend/ 与 sdk/simple-harness-sdk/src/，排除 vendor、dist、build、userdata、
  .venv、egg-info、tests 目录与 test_ 开头的文件。
- 禁区不计：backend/deskpet/workflows/store/schema.py（表结构与迁移）与 migrator.run_migrations。
- 前端范围：tauri-app/src/，排除 *.test.* 文件。
- 函数内导入按导入的名字计（2026-10-12 起；拆行合并不影响计数）。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

if sys.version_info < (3, 11):  # 系统自带 3.9 解析不了新语法，换用后端虚拟环境的解释器
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

REPO = Path(__file__).resolve().parents[2]
_VENV_BIN = REPO / "sdk/simple-harness-sdk/.venv/bin"
RUFF = Path(os.environ.get("CQ_RUFF") or (_VENV_BIN / "ruff" if (_VENV_BIN / "ruff").exists() else "ruff"))
MYPY = Path(os.environ.get("CQ_MYPY") or (_VENV_BIN / "mypy" if (_VENV_BIN / "mypy").exists() else "mypy"))
JSCPD_IGNORE = ("**/tests/**,**/test_*.py,**/*.test.ts,**/*.test.tsx,**/vendor/**,**/dist/**,**/dist-1/**,"
                "**/build/**,**/userdata/**,**/.venv/**,**/node_modules/**,**/__pycache__/**,**/*.egg-info/**")
RUFF_CONFIG = REPO / "scripts/code_quality/ruff.toml"
BASELINE = REPO / "scripts/code_quality/baseline.json"
PY_TREES = {"backend": REPO / "backend", "sdk": REPO / "sdk/simple-harness-sdk/src"}
TS_TREE = REPO / "tauri-app/src"
SKIP_DIRS = {"vendor", "dist", "dist-1", "build", "userdata", ".venv", "__pycache__",
             "tests", "node_modules", ".mypy_cache", ".ruff_cache"}
FORBIDDEN_FILES = {"backend/deskpet/workflows/store/schema.py"}
FORBIDDEN_FUNCS = {("backend/deskpet/memory/migrator.py", "run_migrations")}
SDK_PACKAGES = ("simple_harness", "agent_orchestrator")
INFO_KEYS = {"py_lines"}  # 只记录，不进棘轮
CODENAME = re.compile(
    r"\b(P[0-9]+-S[0-9]+[a-z]?|FEAT-[A-Z][0-9]+|opt\.[0-9]+|T[0-9]+\.[0-9]+|N[0-9]+-[0-9]+|"
    r"S[0-9]+[a-z]? Task [0-9]+|V[0-9]{2}|A[0-9]{2}|R[0-9]+-[0-9]+)\b"
)


def rel(p: Path) -> str:
    return p.relative_to(REPO).as_posix()


def py_files(tree: Path):
    for dp, dn, fn in os.walk(tree):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and not d.endswith(".egg-info")
                 and not d.startswith("dist")]
        for f in fn:
            if f.endswith(".py") and not f.startswith("test_") and f != "conftest.py":
                yield Path(dp) / f


def ts_files():
    for dp, dn, fn in os.walk(TS_TREE):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if (f.endswith(".ts") or f.endswith(".tsx")) and ".test." not in f:
                yield Path(dp) / f


def is_silent(handler: ast.ExceptHandler) -> bool:
    body = [s for s in handler.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    if not body:
        return True
    return all(isinstance(s, (ast.Pass, ast.Continue)) or
               (isinstance(s, ast.Return) and (s.value is None or
                (isinstance(s.value, ast.Constant) and s.value.value in (None, False, 0, ""))))
               for s in body)


def broad(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    if t is None:
        return True
    names = [t] if not isinstance(t, ast.Tuple) else list(t.elts)
    return any(isinstance(n, ast.Name) and n.id in ("Exception", "BaseException") for n in names)


def sdk_internal(module: str) -> bool:
    """后端产品代码不许碰的 SDK 模块：私有模块、存储适配器包根以下、编排测试夹具。"""
    if not module.startswith(SDK_PACKAGES):
        return False
    parts = module.split(".")
    return (any(p.startswith("_") and not p.startswith("__") for p in parts)
            or module.startswith("simple_harness.execution.sqlite.")
            or module.startswith("agent_orchestrator.testing"))


def ast_metrics() -> dict:
    m: dict = {
        "classes_over_1500": [], "files_over_3000": [], "files_2000_3000": [],
        "funcs_over_300_backend": [], "funcs_over_300_sdk": [], "funcs_over_150": 0,
        "inner_imports": 0, "except_broad": 0, "except_broad_silent": 0,
        "backend_imports_sdk_internal": 0, "backend_sdk_module_paths": set(), "print_calls": 0, "sys_path_hacks": 0,
        "global_stmts": 0, "codename_comment_lines": 0, "legacy_lines": 0, "py_lines": 0,
        # 阶段 3.5 起：留在函数里、标了"循环依赖"的导入（上限：SDK 阶段 5 末 ≤ 30，后端阶段 6 末 ≤ 20）
        "cycle_imports_backend": 0, "cycle_imports_sdk": 0,
    }
    for tree_name, tree in PY_TREES.items():
        for path in py_files(tree):
            r = rel(path)
            src = path.read_text(encoding="utf-8", errors="replace")
            lines = src.count("\n") + 1
            m["py_lines"] += lines
            m["legacy_lines"] += len(re.findall(r"^.*(?:legacy|deprecated).*$", src, re.I | re.M))
            m[f"cycle_imports_{tree_name}"] += len(re.findall(r"^\s*# 按需导入：循环依赖", src, re.M))
            for line in src.splitlines():
                s = line.strip()
                if s.startswith("#") and CODENAME.search(s):
                    m["codename_comment_lines"] += 1
            if r not in FORBIDDEN_FILES:
                if lines > 3000:
                    m["files_over_3000"].append(f"{r}:{lines}")
                elif lines > 2000:
                    m["files_2000_3000"].append(f"{r}:{lines}")
            if r == "backend/main.py":
                m["main_py_lines"] = lines
            try:
                t = ast.parse(src)
            except SyntaxError:
                continue
            func_stack: list = []

            def visit(node, in_func: bool) -> None:
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        n = child.end_lineno - child.lineno + 1
                        if r not in FORBIDDEN_FILES and (r, child.name) not in FORBIDDEN_FUNCS:
                            if n > 300:
                                m[f"funcs_over_300_{tree_name}"].append(f"{r}:{child.name}:{n}")
                            if n > 150:
                                m["funcs_over_150"] += 1
                        visit(child, True)
                        continue
                    if isinstance(child, ast.ClassDef):
                        n = child.end_lineno - child.lineno + 1
                        if n > 1500:
                            m["classes_over_1500"].append(f"{r}:{child.name}:{n}")
                    if in_func and isinstance(child, (ast.Import, ast.ImportFrom)):
                        m["inner_imports"] += len(child.names)  # 按名字计，拆行合并不影响
                    if isinstance(child, ast.ImportFrom) and tree_name == "backend" and child.module \
                            and child.module.startswith(SDK_PACKAGES):
                        m["backend_sdk_module_paths"].add(child.module)
                        if sdk_internal(child.module):
                            m["backend_imports_sdk_internal"] += 1
                    if isinstance(child, ast.ExceptHandler) and broad(child):
                        m["except_broad"] += 1
                        if is_silent(child):
                            m["except_broad_silent"] += 1
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "print":
                        m["print_calls"] += 1
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) \
                            and child.func.attr in ("insert", "append") \
                            and isinstance(child.func.value, ast.Attribute) and child.func.value.attr == "path" \
                            and isinstance(child.func.value.value, ast.Name) and child.func.value.value.id == "sys":
                        m["sys_path_hacks"] += 1
                    if isinstance(child, ast.Global):
                        m["global_stmts"] += 1
                    visit(child, in_func)

            visit(t, False)
    ts_any = ts_inline = 0
    for path in ts_files():
        src = path.read_text(encoding="utf-8", errors="replace")
        lines = src.count("\n") + 1
        r = rel(path)
        if lines > 3000:
            m["files_over_3000"].append(f"{r}:{lines}")
        elif lines > 2000:
            m["files_2000_3000"].append(f"{r}:{lines}")
        ts_any += len(re.findall(r":\s*any\b|\bas any\b|<any>", src))
        ts_inline += src.count("style={{")
        m["legacy_lines"] += len(re.findall(r"^.*(?:legacy|deprecated).*$", src, re.I | re.M))
        for line in src.splitlines():
            s = line.strip()
            if s.startswith("//") and CODENAME.search(s):
                m["codename_comment_lines"] += 1
    m["backend_sdk_module_paths"] = sorted(m["backend_sdk_module_paths"])
    m["ts_explicit_any"] = ts_any
    m["ts_inline_styles"] = ts_inline
    for k in ("classes_over_1500", "files_over_3000", "files_2000_3000", "backend_sdk_module_paths",
              "funcs_over_300_backend", "funcs_over_300_sdk"):
        m[k].sort()
    return m


def ruff_metrics() -> dict:
    out: dict = {}
    for name, tree in PY_TREES.items():
        res = subprocess.run(
            [str(RUFF), "check", "--config", str(RUFF_CONFIG), "--output-format", "json",
             "--exclude", "tests,test_*.py,conftest.py", str(tree)],
            cwd=REPO, capture_output=True, text=True,
        )
        items = json.loads(res.stdout or "[]")
        items = [i for i in items if rel(Path(i["filename"])) not in FORBIDDEN_FILES]
        by: dict = {}
        for i in items:
            by[i["code"] or "syntax"] = by.get(i["code"] or "syntax", 0) + 1
        out[f"ruff_{name}_total"] = len(items)
        out[f"ruff_{name}_by_rule"] = dict(sorted(by.items(), key=lambda kv: -kv[1]))
        c = [i for i in items if i["code"] == "C901"]
        cc = [int(re.search(r"\((\d+) > \d+\)", i["message"]).group(1)) for i in c]
        out[f"complexity_over_15_{name}"] = sum(1 for x in cc if x > 15)
        out[f"complexity_over_25_{name}"] = sum(1 for x in cc if x > 25)
    return out


def slow_metrics() -> dict:
    out: dict = {}
    res = subprocess.run([str(MYPY)], cwd=REPO / "sdk/simple-harness-sdk", capture_output=True, text=True)
    mm = re.search(r"Found (\d+) errors?", res.stdout)
    out["mypy_sdk_errors"] = int(mm.group(1)) if mm else (0 if "Success" in res.stdout else -1)
    res = subprocess.run([str(MYPY), "--config-file", "scripts/code_quality/mypy-backend.ini"], cwd=REPO,
                         capture_output=True, text=True)
    mm = re.search(r"Found (\d+) errors?", res.stdout)
    out["mypy_backend_errors"] = int(mm.group(1)) if mm else (0 if "Success" in res.stdout else -1)
    jout = REPO / "tauri-app/node_modules/.tmp/cq-jscpd"
    subprocess.run(["npx", "--yes", "jscpd@4.0.5", "--min-lines", "30", "--min-tokens", "150",
                    "--max-size", "5mb", "--max-lines", "50000", "--reporters", "json", "--output", str(jout),
                    "--silent", "--ignore", JSCPD_IGNORE, "--format", "python,typescript,tsx",
                    "backend", "sdk/simple-harness-sdk/src", "tauri-app/src"], cwd=REPO,
                   capture_output=True, text=True)
    rep = jout / "jscpd-report.json"
    if rep.exists():
        tot = json.loads(rep.read_text())["statistics"]["total"]
        out["dup_clones_30_lines"] = tot["clones"]
        out["dup_lines"] = tot["duplicatedLines"]
    tmp = REPO / "tauri-app/node_modules/.tmp/cq-eslint.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["npx", "eslint", ".", "-f", "json", "-o", str(tmp)], cwd=REPO / "tauri-app",
                   capture_output=True, text=True)
    data = json.loads(tmp.read_text()) if tmp.exists() else []
    out["eslint_errors"] = sum(f["errorCount"] for f in data)
    out["eslint_warnings"] = sum(f["warningCount"] for f in data)
    return out


def git_metrics() -> dict:
    files = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True).stdout.splitlines()
    return {
        "tracked_bak_files": sum(1 for f in files if re.search(r"\.bak\d*$", f)),
        "tracked_root_reports": sum(1 for f in files if "/" not in f and f.endswith(".md")),
        "tracked_root_images": sum(1 for f in files if "/" not in f and f.endswith(".png")),
        "tracked_test_evidence": sum(1 for f in files if f.startswith(".local-test-evidence/")),
    }


def collect(slow: bool) -> dict:
    m = ast_metrics()
    m.update(ruff_metrics())
    m.update(git_metrics())
    if slow:
        m.update(slow_metrics())
    return m


def numeric(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, list):
        return len(v)
    return None


def summary(m: dict) -> None:
    for k, v in m.items():
        if k.endswith("_by_rule"):
            continue
        n = numeric(v)
        print(f"{k:34s} {n if n is not None else v}")


def _moved_lines(ref: str) -> set[str]:
    """本次相对 ref 删掉的行（去掉首尾空白）。新文件里的函数若几乎都出自这些行，就是搬家而不是新写。"""
    diff = subprocess.run(["git", "diff", "-U0", ref, "--", "backend", "sdk/simple-harness-sdk/src"],
                          cwd=REPO, capture_output=True, text=True).stdout
    return {ln[1:].strip() for ln in diff.splitlines()
            if ln.startswith("-") and not ln.startswith("---") and ln[1:].strip()}


def _is_moved(src_lines: list[str], node: ast.AST, removed: set[str]) -> bool:
    body = [ln.strip() for ln in src_lines[node.lineno - 1:node.end_lineno] if ln.strip()]
    return bool(body) and sum(1 for ln in body if ln in removed) >= 0.9 * len(body)


def new_file_violations(ref: str) -> list[str]:
    added = subprocess.run(["git", "diff", "--name-only", "--diff-filter=A", ref], cwd=REPO,
                           capture_output=True, text=True).stdout.splitlines()
    added += subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=REPO,
                            capture_output=True, text=True).stdout.splitlines()
    removed = _moved_lines(ref)
    bad: list[str] = []
    py = [f for f in added if f.endswith(".py") and (f.startswith("backend/") or f.startswith("sdk/"))
          and "/tests/" not in f and not Path(f).name.startswith("test_")]
    for f in py:
        p = REPO / f
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        lines = text.splitlines()
        tree = ast.parse(text)
        funcs = {(n.name, n.lineno): n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for (name, _), node in funcs.items():
            n = node.end_lineno - node.lineno + 1
            if n > 150 and not _is_moved(lines, node, removed):
                bad.append(f"新文件函数超 150 行：{f}:{name}:{n}")
        res = subprocess.run([str(RUFF), "check", "--config", str(RUFF_CONFIG), "--select", "C901",
                              "--output-format", "json", f], cwd=REPO, capture_output=True, text=True)
        for item in json.loads(res.stdout or "[]"):
            row = item["location"]["row"]
            node = next((nd for (nm, ln), nd in funcs.items() if ln == row), None)
            if node is not None and _is_moved(lines, node, removed):
                continue
            bad.append(f"新文件复杂度超 15：{f}:{row} {item['message']}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["collect", "check", "lower", "rebase", "long-functions"])
    ap.add_argument("--keys", default="")
    ap.add_argument("--reason", default="")
    ap.add_argument("--md")
    ap.add_argument("--slow", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--baseline", default=str(BASELINE))
    ap.add_argument("--new-since")
    ap.add_argument("--min", type=int, default=300)
    a = ap.parse_args()

    if a.cmd == "long-functions":
        m = ast_metrics()
        rows = [x for k in ("funcs_over_300_backend", "funcs_over_300_sdk") for x in m[k]]
        rows = [r for r in rows if int(r.rsplit(":", 1)[1]) >= a.min]
        rows = sorted(rows, key=lambda r: -int(r.rsplit(":", 1)[1]))
        for r in rows:
            print(r)
        print(f"合计 {len(rows)}")
        if a.md:
            def phase(path: str) -> str:
                if path == "backend/main.py":
                    return "阶段 2"
                if path.startswith("sdk/"):
                    return "阶段 5"
                return "阶段 6"
            out = ["# 附录 A：超长函数清单与归属", "",
                   f"由 `metrics.py long-functions --min {a.min} --md` 生成（格式化后口径）。"
                   "归属规则：主入口归阶段 2，SDK 归阶段 5，其余后端归阶段 6。禁区迁移函数不计。", "",
                   "| 行数 | 文件 | 函数 | 归属 |", "|---|---|---|---|"]
            for r in rows:
                path, fn, n = r.rsplit(":", 2)
                out.append(f"| {n} | {path} | {fn} | {phase(path)} |")
            Path(a.md).write_text("\n".join(out) + "\n", encoding="utf-8")
        return 0

    m = collect(a.slow)
    if a.cmd == "collect":
        summary(m)
        if a.out:
            Path(a.out).write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        return 0

    base = json.loads(Path(a.baseline).read_text(encoding="utf-8"))
    if a.cmd == "rebase":
        keys = [k for k in a.keys.split(",") if k]
        if not keys or not a.reason:
            raise SystemExit("rebase 需要 --keys 与 --reason")
        log = base.setdefault("_rebase_log", [])
        for k in keys:
            if k not in m:
                raise SystemExit(f"未知指标 {k}")
            log.append({"key": k, "from": numeric(base.get(k)), "to": numeric(m[k]), "reason": a.reason})
            base[k] = m[k]
        Path(a.baseline).write_text(json.dumps(base, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"重设 {len(keys)} 项：{', '.join(keys)}")
        return 0
    if a.cmd == "lower":
        changed = 0
        for k, v in m.items():
            if k.endswith("_by_rule") or k.startswith("_") or k not in base:
                if k not in base and not k.endswith("_by_rule"):
                    base[k] = v
                continue
            nb, nv = numeric(base[k]), numeric(v)
            if nb is not None and nv is not None and nv < nb:
                base[k] = v
                changed += 1
        Path(a.baseline).write_text(json.dumps(base, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"基线下调 {changed} 项")
        return 0

    worse: list[str] = []
    for k, v in m.items():
        if k.endswith("_by_rule") or k.startswith("_") or k not in base or k in INFO_KEYS:
            continue
        nb, nv = numeric(base[k]), numeric(v)
        if nb is not None and nv is not None and nv > nb:
            extra = ""
            if isinstance(v, list) and isinstance(base[k], list):
                ident = lambda x: x.rsplit(":", 1)[0] if re.search(r":\d+$", x) else x  # noqa: E731
                fresh = sorted({ident(x) for x in v} - {ident(x) for x in base[k]})
                extra = "；新出现：" + "，".join(fresh[:5]) if fresh else "；（条目相同，只是数量口径变化）"
            worse.append(f"{k}: 基线 {nb} → 现在 {nv}{extra}")
    if a.new_since:
        worse += new_file_violations(a.new_since)
    for w in worse:
        print("变差", w)
    print("棘轮检查：" + ("不通过" if worse else "通过"))
    return 1 if worse else 0


if __name__ == "__main__":
    sys.exit(main())
