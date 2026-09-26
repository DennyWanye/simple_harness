// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { describe, expect, it, vi } from "vitest";
import { buildElkGraph, flatten, structureKey, type ElkOutput } from "./layoutModel";
import { displayOf, isStalled, parseLiveGraph, stepTitle, type LiveGraph, type LiveNode } from "./model";
import { progressOf } from "./progress";

function node(id: string, extra: Partial<LiveNode> = {}): LiveNode {
  return { occurrence_id: id, task_id: "task-" + id, form: "primitive", parent: null, method: null, task_status: "BLOCKED",
    phase: null, readiness_reason: null, attempt_count: 0, last_event_at: null, ...extra };
}

describe("displayOf：先看状态，终态任务以任务状态为准", () => {
  it.each([
    ["BLOCKED", "等待", "idle"], ["READY", "可调度", "ready"], ["ACTIVE", "运行中", "running"],
    ["VERIFYING", "验证中", "verifying"], ["COMPLETED", "完成", "done"], ["FAILED", "失败", "failed"],
    ["CANCELLED", "取消", "cancelled"],
  ])("叶子任务 %s → %s", (status, label, tone) => {
    expect(displayOf({ form: "primitive", task_status: status, phase: null })).toEqual({ label, tone });
  });

  it.each([
    ["planning_ready", "等待拆分", "idle"], ["refining", "拆分中", "running"], ["waiting_children", "子任务进行中", "running"],
    ["composition_review", "汇总验收中", "verifying"], ["resolution_committed", "完成", "done"],
    ["evidence_or_authority_wait", "等人处理", "person"],
  ])("复合任务阶段 %s → %s", (phase, label, tone) => {
    expect(displayOf({ form: "compound", task_status: "ACTIVE", phase })).toEqual({ label, tone });
  });

  it("任务已取消时，停在'子任务进行中'的阶段事件不算数", () => {
    expect(displayOf({ form: "compound", task_status: "CANCELLED", phase: "waiting_children" }).label).toBe("取消");
  });

  it("复合任务还没有阶段事件时按任务状态显示", () => {
    expect(displayOf({ form: "compound", task_status: "ACTIVE", phase: null }).label).toBe("运行中");
  });

  it("未知值显示'未知'并告警，不报错", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    expect(displayOf({ form: "primitive", task_status: "SOMETHING_NEW", phase: null })).toEqual({ label: "未知", tone: "unknown" });
    expect(displayOf({ form: "compound", task_status: "ACTIVE", phase: "new_phase" }).tone).toBe("unknown");
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});

describe("isStalled", () => {
  it("运行中且超过 10 分钟没有事件才算可能卡住；任务结束后不提示", () => {
    const running = node("a", { task_status: "ACTIVE", last_event_at: 1000 });
    expect(isStalled(running, 1000 + 601, false)).toBe(true);
    expect(isStalled(running, 1000 + 599, false)).toBe(false);
    expect(isStalled(running, 1000 + 601, true)).toBe(false);
    expect(isStalled(node("b", { task_status: "BLOCKED", last_event_at: 0 }), 9999, false)).toBe(false);
  });
});

describe("parseLiveGraph", () => {
  const good = { schema_version: 1, mission_id: "m", source: "htn", plan_revision: 2, through_seq: 9, revisions: [1, 2],
    nodes: [{ ...node("a"), extra_field: 1 }], edges: [{ kind: "order", source: "a", target: "a" }] };
  it("多出的字段忽略", () => {
    expect(parseLiveGraph(good, "m").nodes[0].occurrence_id).toBe("a");
  });
  it.each([
    [{ ...good, mission_id: "other" }], [{ ...good, source: "strict" }], [{ ...good, nodes: [{ occurrence_id: "a" }] }],
    [{ ...good, edges: [{ kind: "refinement", source: "a", target: "b" }] }], [{ ...good, through_seq: -1 }],
  ])("缺字段或格式不对就报错", (bad) => {
    expect(() => parseLiveGraph(bad, "m")).toThrow();
  });
});

function sample(): LiveGraph {
  const nodes: LiveNode[] = [node("root", { form: "compound", task_status: "ACTIVE" })];
  for (const mid of ["m1", "m2"]) {
    nodes.push(node(mid, { form: "compound", parent: "root" }));
    for (let i = 0; i < 8; i += 1) nodes.push(node(`${mid}-${i}`, { parent: mid }));
  }
  nodes.push(node("tail", { parent: "root" }));
  const edges = [
    { kind: "order" as const, source: "m1-0", target: "m1-1" },
    { kind: "order" as const, source: "m1", target: "m2" },
    { kind: "order" as const, source: "m1-7", target: "m2-0" },
    { kind: "data" as const, source: "m1-0", target: "tail" },
  ];
  return { mission_id: "m", source: "htn", plan_revision: 3, through_seq: 1, nodes, edges, revisions: [3] };
}

describe("buildElkGraph", () => {
  it("两层嵌套、20 个节点：父子关系正确，线放在最近公共祖先里，数据依赖默认隐藏", () => {
    const plan = buildElkGraph(sample(), new Set(), false);
    const root = plan.elk.children![0];
    expect(root.id).toBe("root");
    expect(root.children!.map((c) => c.id)).toEqual(["m1", "m2", "tail"]);
    expect(root.children![0].children).toHaveLength(8);
    expect(plan.visible.size).toBe(20);
    expect(root.children![0].edges!.map((e) => e.id)).toEqual(["order:m1-0>m1-1"]);
    expect(root.edges!.map((e) => e.id).sort()).toEqual(["order:m1-7>m2-0", "order:m1>m2"]);
    expect(plan.edges.some((e) => e.kind === "data")).toBe(false);
    expect(buildElkGraph(sample(), new Set(), true).edges.some((e) => e.kind === "data")).toBe(true);
  });

  it("折叠后子任务不画，连到子任务的线改连到折叠框，重复的线合并", () => {
    const plan = buildElkGraph(sample(), new Set(["m1"]), false);
    expect(plan.visible.has("m1-0")).toBe(false);
    expect(plan.edges.map((e) => e.source + ">" + e.target)).toEqual(["m1>m2", "m1>m2-0"]); // m1-7>m2-0 folds into m1>m2-0
    const again = buildElkGraph(sample(), new Set(["m1", "m2"]), false); // both fold: one line left
    expect(again.edges.map((e) => e.source + ">" + e.target)).toEqual(["m1>m2"]);
  });

  it("elk 真能排出来，子节点坐标在父框里", async () => {
    const { default: ELK } = await import("elkjs/lib/elk.bundled.js");
    const plan = buildElkGraph(sample(), new Set(), true);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const placed = flatten((await new ELK().layout(plan.elk as any)) as ElkOutput, plan.groups);
    expect(placed).toHaveLength(20);
    const m1 = placed.find((p) => p.id === "m1")!;
    for (const child of placed.filter((p) => p.parent === "m1")) {
      expect(child.x + child.width).toBeLessThanOrEqual(m1.width);
      expect(child.y + child.height).toBeLessThanOrEqual(m1.height);
    }
    expect(placed.findIndex((p) => p.id === "m1")).toBeLessThan(placed.findIndex((p) => p.id === "m1-0"));
  });

  it("结构签名：同一版本同样折叠时不变", () => {
    const graph = sample();
    const changed = { ...graph, nodes: graph.nodes.map((n) => ({ ...n, task_status: "COMPLETED" })) };
    expect(structureKey(changed, new Set(), false)).toBe(structureKey(graph, new Set(), false));
    expect(structureKey({ ...graph, plan_revision: 4 }, new Set(), false)).not.toBe(structureKey(graph, new Set(), false));
  });
});

describe("progressOf：列表进度条", () => {
  it.each([
    ["CREATED", undefined, 0, { stage: 0, ended: null }],
    ["PLANNING", undefined, 0, { stage: 1, ended: null }],
    ["ACTIVE", "running", 3, { stage: 2, ended: null }],
    ["ACTIVE", "verifying", 3, { stage: 3, ended: null }],
    ["COMPLETED", "delivered", 3, { stage: 4, ended: null }],
    ["FAILED", "failed", 3, { stage: 2, ended: "failed" }],
    ["FAILED", "failed", 0, { stage: 0, ended: "failed" }],
    ["CANCELLED", "cancelled", 2, { stage: 2, ended: "cancelled" }],
  ])("%s / %s / %d 个子任务", (status, ui, total, expected) => {
    expect(progressOf(status as string, ui as string | undefined, total as number)).toEqual(expected);
  });
});

describe("stepTitle：步骤标题说清这一步做什么", () => {
  it("有产出文件时列出文件", () => {
    expect(stepTitle({ key: "prepare", evidence: ["prepare 步骤在工作区产出 01-定位.md，文件内容…", "prepare 步骤在工作区产出 02-菜单.md，至少…"] }, "整个任务目标"))
      .toBe("准备：产出 01-定位.md、02-菜单.md");
    expect(stepTitle({ key: "positioning", evidence: ["positioning 步骤产出 01-定位.md"] }, "")).toBe("positioning：产出 01-定位.md");
  });
  it("没有文件名时用职责原文，去掉重复的步骤名", () => {
    expect(stepTitle({ key: "deliver", evidence: ["deliver 步骤给出最终结论"] }, "")).toBe("交付：给出最终结论");
    expect(stepTitle({ key: "review_2", evidence: [] }, "")).toBe("复核");
  });
  it("没有步骤信息时用任务目标，接续步骤的英文前缀换成中文", () => {
    expect(stepTitle(null, "Continue from an accepted upstream delivery: 写报告")).toBe("接续上一步交付：写报告");
    expect(stepTitle(null, "写报告")).toBe("写报告");
  });
});
