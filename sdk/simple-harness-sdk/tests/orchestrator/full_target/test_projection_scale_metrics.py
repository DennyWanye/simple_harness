# SPDX-License-Identifier: Apache-2.0
"""TaskGraph 规模用例记录实际延迟与内存（V11 轻量版，试用前 2026-10-08）。

原计划 §15 / 附录 D S08："规模：记录实际延迟、内存与 SQL 次数"。这里在默认上限附近（约 2000 个
节点）建一张执行图，量"编译执行投影 + 校验"一遍的墙钟与内存峰值，写进 junit 属性；设了
``TASKGRAPH_SCALE_METRICS_OUT`` 时另写一份 JSON 进该目录。这条路径纯内存、不读库，SQL 次数记 0。

只记不判快慢：断言只管"在上限内照常通过、拓扑序完整"，时限给得很宽，免得机器忙时误报。
"""
from __future__ import annotations

import json
import os
import time
import tracemalloc
from pathlib import Path

from test_projection_integrity import binding, instance, occurrence, order, snapshot

from agent_orchestrator.contracts.htn import MethodInstanceId, TaskForm
from agent_orchestrator.graph.projection_validation import validate_execution_projection
from agent_orchestrator.graph.task_network import DEFAULT_PROJECTION_BUDGET

COMPOUNDS, LEAVES = 100, 18  # 2 + 2×100 + 100×18 = 2002 个节点，默认上限 2048


def _wide_network():
    occurrences = [occurrence("o-root", "t-root", form=TaskForm.COMPOUND)]
    bindings = [binding("t-root", form=TaskForm.COMPOUND, adopted="mi-root")]
    instances = [instance("mi-root", "t-root", "o-root", tuple((f"s{c}", f"o-c{c}") for c in range(COMPOUNDS)))]
    orders = []
    for c in range(COMPOUNDS):
        occurrences.append(occurrence(f"o-c{c}", f"t-c{c}", form=TaskForm.COMPOUND))
        bindings.append(binding(f"t-c{c}", form=TaskForm.COMPOUND, adopted=f"mi-c{c}"))
        leaves = [f"o-c{c}-l{leaf}" for leaf in range(LEAVES)]
        for leaf, occ in enumerate(leaves):
            occurrences.append(occurrence(occ, f"t-c{c}-l{leaf}"))
            bindings.append(binding(f"t-c{c}-l{leaf}"))
        instances.append(instance(f"mi-c{c}", f"t-c{c}", f"o-c{c}", tuple((f"l{i}", occ) for i, occ in enumerate(leaves))))
        # 每个复合步骤里 18 步排成一串；复合步骤之间并行（最长链约 22，默认深度上限 64）
        orders.extend(order(before, after) for before, after in zip(leaves, leaves[1:]))
    return snapshot(
        occurrences=tuple(occurrences), task_bindings=tuple(bindings), method_instances=tuple(instances),
        adopted_instance_ids=tuple(MethodInstanceId(i.instance_id) for i in instances),
        order_constraints=tuple(orders), data_requirements=(), obligation_coverage=(), required_obligations=(),
    )


def test_a_projection_near_the_default_bound_records_its_latency_and_memory(record_property) -> None:
    network = _wide_network()
    tracemalloc.start()
    started = time.perf_counter()
    projection = network.execution_projection()
    report = validate_execution_projection(projection, DEFAULT_PROJECTION_BUDGET)
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    nodes, edges = len(projection.nodes), len(projection.edges)
    metrics = {"scenario": "S08-near-default-bound", "nodes": nodes, "edges": edges,
               "max_nodes": DEFAULT_PROJECTION_BUDGET.max_nodes, "max_edges": DEFAULT_PROJECTION_BUDGET.max_edges,
               "elapsed_seconds": round(elapsed, 4), "peak_memory_bytes": peak, "sql_statements": 0}
    for key, value in metrics.items():
        record_property(key, value)
    out = os.environ.get("TASKGRAPH_SCALE_METRICS_OUT")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / "projection-scale.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1) + "\n")

    assert 1900 <= nodes <= DEFAULT_PROJECTION_BUDGET.max_nodes and edges <= DEFAULT_PROJECTION_BUDGET.max_edges
    assert not report.problems, [problem.kind for problem in report.problems][:5]
    assert len(report.topological_order or ()) == nodes
    assert elapsed < 60.0  # 只防卡死，不判快慢
