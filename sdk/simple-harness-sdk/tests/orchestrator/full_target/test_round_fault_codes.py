# SPDX-License-Identifier: Apache-2.0
"""一轮故障按类型码处理（TaskGraph 补全第一批，原计划 §12）。

主循环接住一个任务这一轮冲出的异常后，只按异常带的类型码查错误码表决定"数据损坏、当轮停"
还是"原地重试、上限兜住"，不读异常文字。没带码的、带了却没登记的一律原地重试（用户
2026-09-28：基础设施故障原地重试）；"未知码拒绝"落在登记上——本文件的源码扫描守住。
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from agent_orchestrator.contracts.error_table import (
    ROUND_FAULTS,
    CodedFault,
    RoundFaultCode,
    RoundFaultHandling,
    round_fault_handling,
)
from agent_orchestrator.orchestrator import failure_classes
from agent_orchestrator.orchestrator.failure_classes import ROUND_CORRUPT, ROUND_RETRY, classify_round_fault

SRC = Path(failure_classes.__file__).resolve().parents[1]


def coded_fault_violations(source: str, path: str = "<src>") -> list[str]:
    """带码异常类（基类里有 CodedFault）必须写 ``code = RoundFaultCode.<登记过的名字>``。"""

    out = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(isinstance(base, ast.Name) and base.id == "CodedFault" for base in node.bases):
            continue
        names = [stmt.value for stmt in node.body if isinstance(stmt, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "code" for t in stmt.targets)]
        if len(names) != 1:
            out.append(f"{path}:{node.name}: no single class-level code")
            continue
        value = names[0]
        if not (isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
                and value.value.id == "RoundFaultCode"):
            out.append(f"{path}:{node.name}: code is not a RoundFaultCode member")
        elif value.attr not in RoundFaultCode.__members__ or RoundFaultCode[value.attr] not in ROUND_FAULTS:
            out.append(f"{path}:{node.name}: code {value.attr} is not registered")
    return out


def test_every_round_fault_code_has_a_handling():
    assert set(ROUND_FAULTS) == set(RoundFaultCode)
    assert all(isinstance(handling, RoundFaultHandling) for handling in ROUND_FAULTS.values())


def test_every_coded_exception_in_the_sdk_carries_a_registered_code():
    found, violations = 0, []
    for path in sorted(SRC.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "CodedFault" not in source:
            continue
        found += source.count("CodedFault):")
        violations += coded_fault_violations(source, str(path.relative_to(SRC)))
    assert found >= 6 and not violations, violations


def test_the_scan_catches_an_unregistered_code():
    bad = "class X(RuntimeError, CodedFault):\n    code = RoundFaultCode.NOT_A_CODE\n"
    missing = "class Y(RuntimeError, CodedFault):\n    pass\n"
    assert coded_fault_violations(bad) == ["<src>:X: code NOT_A_CODE is not registered"]
    assert coded_fault_violations(missing) == ["<src>:Y: no single class-level code"]


def test_the_classifier_reads_the_type_code_not_the_message():
    for function in (classify_round_fault, round_fault_handling):
        body = inspect.getsource(function)
        assert "str(error)" not in body and ".args" not in body, function.__name__

    class Damaged(RuntimeError, CodedFault):
        code = RoundFaultCode.TASKGRAPH_HISTORY_INTEGRITY

    class Unregistered(RuntimeError, CodedFault):
        code = "SOMETHING_NEW"  # type: ignore[assignment]

    assert classify_round_fault(Damaged("anything")) == (ROUND_CORRUPT, "TASKGRAPH_HISTORY_INTEGRITY")
    # 文字里写着损坏的码、对象上没有码：不读文字，原地重试
    assert classify_round_fault(RuntimeError("TASKGRAPH_HISTORY_INTEGRITY: x")) == (ROUND_RETRY, None)
    # 带了没登记的码：原地重试，码照实带出（事件里写明）
    assert classify_round_fault(Unregistered("x")) == (ROUND_RETRY, "SOMETHING_NEW")
