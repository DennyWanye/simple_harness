# SPDX-License-Identifier: Apache-2.0
"""验收资产自己不能烂（HTN 补齐 F1-4）：三张清单里写的东西都得在代码里真实存在。

* 接缝表：每个"现在的生产方"能导入到；
* 断点表：每个注入点名字在代码里出现过，绑定用例存在；
* 改坏清单：有文件的条目，原文在该文件里恰好出现一次（执行器才能改准），绑定用例存在；
  没文件的条目（TaskGraph 12 条，留联测补写）不许挂用例。

只检查清单与代码对得上，不跑任何被绑定的用例。
"""
from __future__ import annotations

import importlib
import json
import re
from functools import lru_cache
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SDK = HERE.parents[2]


def _load(name: str) -> dict:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


SEAMS = _load("seams_current.json")["rows"]
POINTS = _load("crash_points.json")["points"]
MUTATIONS = _load("mutations.json")["mutations"]
# 进程强退的切点由这个测试辅助脚本埋（K01、K02），其余注入点都在源码里
CRASH_SEEDS = [SDK / path for path in ['tests/orchestrator/full_target/taskgraph_exec/crash_seed.py']]


@lru_cache(maxsize=1)
def _code_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8")
                     for path in [*(SDK / "src").rglob("*.py"), *CRASH_SEEDS])


def _test_exists(nodeid: str) -> bool:
    file, _, rest = nodeid.partition("::")
    path = SDK / file
    if not path.is_file():
        return False
    if not rest:
        return True
    name = rest.split("::")[-1].split("[")[0]
    return re.search(rf"^\s*(async\s+)?def {re.escape(name)}\(", path.read_text(encoding="utf-8"),
                     re.MULTILINE) is not None


def _resolve(target: str) -> object:
    module, _, qualname = target.partition(":")
    found: object = importlib.import_module(module)
    for part in qualname.split(".") if qualname else ():
        found = getattr(found, part)
    return found


def test_ids_are_unique() -> None:
    for rows in (SEAMS, POINTS, MUTATIONS):
        ids = [row["id"] for row in rows]
        assert len(ids) == len(set(ids)), ids


@pytest.mark.parametrize("row", SEAMS, ids=lambda row: row["id"])
def test_every_seam_producer_imports(row: dict) -> None:
    for target in row["current_producer"]:
        try:
            _resolve(target)
        except (ImportError, AttributeError) as error:
            raise AssertionError(f"{row['id']}: {target} does not resolve ({error})") from error
    for nodeid in row.get("tests") or ():
        assert _test_exists(nodeid), nodeid


@pytest.mark.parametrize("row", POINTS, ids=lambda row: row["id"])
def test_every_crash_point_names_real_code(row: dict) -> None:
    text = _code_text()
    for injection in row["injection"]:
        name = re.split(r"[（(\s]", injection, maxsplit=1)[0]
        if "函数级" in injection:  # 在这个函数里抛错，不是具名注入点
            function = name.rsplit(".", 1)[-1]
            assert re.search(rf"def {re.escape(function)}\(", text), f"{row['id']}: {name} is not in the code"
            continue
        assert f'"{name}"' in text or f"'{name}'" in text, f"{row['id']}: {name} is not in the code"
    for nodeid in row.get("tests") or ():
        assert _test_exists(nodeid), nodeid


@pytest.mark.parametrize("row", MUTATIONS, ids=lambda row: row["id"])
def test_every_mutation_anchors_exactly_once(row: dict) -> None:
    if row["file"] is None:
        assert not row["tests"] and row["original"] is None, row["id"]
        return
    source = (SDK / row["file"]).read_text(encoding="utf-8")
    assert source.count(row["original"]) == 1, f"{row['id']}: anchor found {source.count(row['original'])} times"
    assert row["replacement"] != row["original"]
    assert row["tests"], row["id"]
    for nodeid in row["tests"]:
        assert _test_exists(nodeid), nodeid


FINDINGS = _load("assurance_findings.json")["rows"]


@pytest.mark.parametrize("row", FINDINGS, ids=lambda row: row["id"])
def test_every_assurance_finding_names_real_tests_and_mutations(row: dict) -> None:
    """Assurance 原计划审查意见的对应表：点名的用例都在、点名的改坏条目都在清单里；标"有用例守着"的
    至少挂一条用例，有缺口的要写明缺什么。"""
    missing = [nodeid for nodeid in row["tests"] if not _test_exists(nodeid)]
    assert not missing, missing
    known = {item["id"] for item in MUTATIONS}
    assert set(row["mutations"]) <= known, sorted(set(row["mutations"]) - known)
    if row["status"] in {"COVERED", "COVERED_AS_DECIDED", "SEE_TEST"}:
        assert row["tests"]
    if row["status"] in {"PARTIAL", "NOT_APPLICABLE", "RECORD_ONLY"}:
        assert row["gap"]
