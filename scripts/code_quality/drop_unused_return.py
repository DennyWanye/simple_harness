#!/usr/bin/env python3
"""代码质量整治阶段 6D：抽取后 ruff 报 F841（`x = self.新方法(...)` 赋了却没用）时，去掉这次多余的返回。

规划器按"段后还读到"判断返回值，读之前若会重新赋值就多算一个。本工具对每处 F841：
- 赋值语句必须是 `名字 = [await] self.方法(...)`（或模块级 `方法(...)`），方法末尾是 `return 名字`；
- 改成单独一句调用，删掉方法末尾的 `return 名字`，返回类型改成 None。
该方法若还有别的调用处在用返回值，就不改（报出来人工看）。

用法：python3 scripts/code_quality/drop_unused_return.py <文件>...
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUFF = os.environ.get("RUFF") or str(
    REPO.parent / "simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff"
)


def fix(path: Path) -> int:
    res = subprocess.run(
        [
            RUFF,
            "check",
            "--no-cache",
            "--config",
            str(REPO / "scripts/code_quality/ruff.toml"),
            "--select",
            "F841",
            "--output-format",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    items = json.loads(res.stdout or "[]")
    done = 0
    for item in sorted(items, key=lambda x: -x["location"]["row"]):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        row = item["location"]["row"]
        stmt = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.Assign) and n.lineno == row), None
        )
        if stmt is None or len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
            print(f"{path}:{row} 不是单名字赋值，跳过")
            continue
        var = stmt.targets[0].id
        call = stmt.value.value if isinstance(stmt.value, ast.Await) else stmt.value
        if not isinstance(call, ast.Call):
            print(f"{path}:{row} 右边不是调用，跳过")
            continue
        name = (
            call.func.attr
            if isinstance(call.func, ast.Attribute)
            else getattr(call.func, "id", None)
        )
        defs = [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
        ]
        if len(defs) != 1:
            print(f"{path}:{row} 找不到唯一的 {name}，跳过")
            continue
        fn = defs[0]
        last = fn.body[-1]
        if not (
            isinstance(last, ast.Return)
            and isinstance(last.value, ast.Name)
            and last.value.id == var
        ):
            print(f"{path}:{row} {name} 末尾不是 return {var}，跳过")
            continue
        uses = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and (getattr(n.func, "attr", None) == name or getattr(n.func, "id", None) == name)
        ]
        if len(uses) != 1:
            print(f"{path}:{row} {name} 有 {len(uses)} 处调用，跳过")
            continue
        lines = text.splitlines(keepends=True)
        # 先改后面的（方法在调用处之后）
        del lines[last.lineno - 1 : last.end_lineno]
        head_end = fn.body[0].lineno - 1  # 签名所在的行（含）
        sig = "".join(lines[fn.lineno - 1 : head_end])
        if fn.returns is not None:
            sig2 = re.sub(r"\)\s*->\s*[^:]+:\s*\n$", ") -> None:\n", sig, flags=re.S)
        else:
            sig2 = re.sub(r"\):\s*\n$", ") -> None:\n", sig)
        lines[fn.lineno - 1 : head_end] = [sig2]
        line = lines[row - 1]
        lines[row - 1] = re.sub(rf"^(\s*){re.escape(var)} = ", r"\1", line, count=1)
        path.write_text("".join(lines), encoding="utf-8")
        done += 1
        print(f"{path}:{row} 去掉 {var} 的多余返回（{name}）")
    return done


def main() -> int:
    for f in sys.argv[1:]:
        fix(Path(f))
    return 0


if __name__ == "__main__":
    sys.exit(main())
