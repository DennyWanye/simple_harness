// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import { describe, expect, it } from "vitest";
import { cleanText, execDisplay, mergePages, parseExecutionPage, stepDisplay, stepProgress, stepTitle } from "./model";
import { buildElkGraph, homesOf, processEdges } from "./layoutModel";
import { M, snapshot } from "./fixture";

const view = () => mergePages([parseExecutionPage(snapshot(), M)]);

describe("parseExecutionPage", () => {
  it("结构来自严格执行图：父子来自拆分边，步骤名与职责来自方法；执行过程节点带种类", () => {
    const v = view();
    expect(v.plan_revision).toBe(1);
    expect(v.structure.find((n) => n.occurrence_id === "b")).toMatchObject({ parent: "root", step: { key: "publish" }, step_index: 1 });
    expect(v.structureEdges).toEqual([{ kind: "order", source: "a", target: "b" }, { kind: "data", source: "a", target: "b" }]);
    expect(v.nodes.map((n) => n.kind)).toContain("repair_request");
  });

  it("任务不对、种类未知就报错；后续页没有结构", () => {
    expect(() => parseExecutionPage(snapshot({ mission_id: "other" }), M)).toThrow();
    expect(() => parseExecutionPage(snapshot({}, { nodes: [{ node_id: "x", kind: "magic" }] }), M)).toThrow();
    expect(parseExecutionPage(snapshot({ graph: null }), M).first).toBeNull();
  });

  it("合并分页：只留两端都在的边", () => {
    const first = parseExecutionPage(snapshot({ complete: false, next_cursor: "c" }, {
      nodes: [(snapshot().execution_nodes as Record<string, unknown>[])[2]],
      edges: [{ kind: "attempt_of", source: "attempt:a1", target: "a", target_layer: "structure" }] }), M);
    const second = parseExecutionPage(snapshot({ graph: null }, {
      nodes: [(snapshot().execution_nodes as Record<string, unknown>[])[3]],
      edges: [{ kind: "review_of", source: "check:r-a1", target: "attempt:a1", target_layer: "execution" },
        { kind: "review_of", source: "check:r-a1", target: "attempt:gone", target_layer: "execution" }] }), M);
    const merged = mergePages([first, second]);
    expect(merged.nodes.map((n) => n.node_id)).toEqual(["attempt:a1", "check:r-a1"]);
    expect(merged.edges.map((e) => e.kind)).toEqual(["attempt_of", "review_of"]);
  });
});

describe("显示", () => {
  it("执行、审阅、规划、修补的中文标题与状态", () => {
    const v = view();
    const by = (id: string) => execDisplay(v.nodes.find((n) => n.node_id === id)!);
    expect(by("attempt:b1")).toEqual({ title: "第 1 次执行", status: { label: "未通过", tone: "failed" } });
    expect(by("check:r-b1").status).toEqual({ label: "不通过", tone: "failed" });
    expect(by("planning:p2").title).toBe("规划：修补计划");
    expect(by("repair_request:rr").title).toBe("修补请求");
    expect(by("attempt:b2").status.label).toBe("执行中");
  });

  it("步骤标题用方法步骤职责；一句话进展用最近一次执行交的说明", () => {
    const v = view();
    const b = v.structure.find((n) => n.occurrence_id === "b")!;
    expect(stepTitle(b.step, "")).toBe("发布：产出 NOTES.md");
    expect(stepProgress(b, v.nodes.filter((n) => n.kind === "attempt" && n.raw.occurrence_id === "b"))).toBe("");
    const a = v.structure.find((n) => n.occurrence_id === "a")!;
    expect(stepProgress(a, v.nodes.filter((n) => n.node_id === "attempt:a1"))).toBe("写好了 NOTES.md");
    expect(cleanText("见 (ev-0123456789ab) 与 task-0123456789abcdef0123 完成")).toBe("见 与 完成");
  });

  it("系统英文原因换成大白话；进展行不写就绪原因；在等的「可调度」步骤写「等待」", () => {
    expect(cleanText("critic verdict unusable: Assurance review awaits original-call reconciliation"))
      .toBe("审阅调用被打断，要等核对原调用结果，这次审阅作废");
    expect(cleanText("critic verdict unusable: bad json")).toBe("审阅结论无法使用：bad json");
    const root = (phase: string) => ({ ...view().structure.find((n) => n.occurrence_id === "root")!, phase, readiness: "NEEDS_REFINEMENT" });
    expect(cleanText("The frozen applicability snapshot and deployment policy selected this method deterministically."))
      .toBe("系统按适用条件和部署策略直接选定了方法");
    expect(stepProgress(root("resolution_committed"), [])).toBe("");
    expect(stepProgress(root("waiting_children"), [])).toBe("");
    const b = view().structure.find((n) => n.occurrence_id === "b")!;
    expect(stepDisplay({ ...b, phase: "READY", readiness: "WAITING_ORDER" })).toEqual({ label: "等待", tone: "idle" });
    expect(stepDisplay({ ...b, phase: "READY", readiness: "READY_CANDIDATE" }).label).toBe("可调度");
  });
});

describe("执行过程放在哪、怎么连", () => {
  it("每次执行放进自己的步骤框；审阅跟着执行；修补请求与修补规划跟着出事的执行；拆分规划放在根", () => {
    const homes = homesOf(view());
    expect(homes.get("attempt:b1")).toBe("b");
    expect(homes.get("check:r-b1")).toBe("b");
    expect(homes.get("repair_request:rr")).toBe("b");
    expect(homes.get("planning:p2")).toBe("b");
    expect(homes.get("planning:p1")).toBe("root");
    expect(homes.get("plan_revision:1")).toBe("root");
  });

  it("失败回路：执行→审阅不通过→修补请求→规划→再执行；没有从步骤指回自己的边", () => {
    const v = view();
    const edges = processEdges(v.nodes, v.edges).map((e) => `${e.kind}:${e.source}>${e.target}`);
    expect(edges).toEqual(expect.arrayContaining([
      "process:attempt:b1>check:r-b1", "process:check:r-b1>repair_request:rr",
      "process:repair_request:rr>planning:p2", "rework:planning:p2>attempt:b2",
    ]));
    // 有规划决定的再执行不再另画一条"返工"线
    expect(edges.filter((e) => e.endsWith(">attempt:b2"))).toEqual(["rework:planning:p2>attempt:b2"]);
    expect(edges.some((e) => { const [s, t] = e.split(":").slice(1).join(":").split(">"); return s === t; })).toBe(false);
  });

  it("没有修补请求的返工：从上一次的审阅直接连到下一次执行", () => {
    const v = view();
    const nodes = v.nodes.filter((n) => !["repair_request:rr", "planning:p2"].includes(n.node_id));
    const edges = v.edges.filter((e) => e.kind !== "retry_authorized");
    expect(processEdges(nodes, edges).map((e) => `${e.kind}:${e.source}>${e.target}`)).toContain("rework:check:r-b1>attempt:b2");
  });

  it("有执行过程的步骤成为框；只看结构时没有执行节点；折叠的复合步骤不带子节点", () => {
    const v = view();
    const plan = buildElkGraph(v, new Set(), false, true);
    expect(plan.groups.has("b") && plan.groups.has("root")).toBe(true);
    expect(plan.visible.has("attempt:b2")).toBe(true);
    const structureOnly = buildElkGraph(v, new Set(), false, false);
    expect([...structureOnly.visible].sort()).toEqual(["a", "b", "root"]);
    expect(structureOnly.edges).toEqual([{ kind: "order", source: "a", target: "b" }]);
    const folded = buildElkGraph(v, new Set(["root"]), false, true);
    expect([...folded.visible]).toEqual(["root"]);
    expect(folded.edges).toEqual([]);
  });
});
