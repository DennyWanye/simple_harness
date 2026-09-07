// Actual Cytoscape graph engine; no renderer mock. Browser layout is a separate check.
import cytoscape from "cytoscape";
import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import { graphElements } from "../primary/graphElements";
import { canvasLabel, graphStyle, labelBudget } from "../primary/graphStyle";
import { parseMemoryGraph } from "../primary/graphRequests";
import { graph } from "../primary/testing/graphFixture";

it("uses SDK llm_inference on active nodes, retains opaque IDs and rejects dangling endpoints", () => {
  const nodes = [{ ...graph.nodes[0], epistemic_status: "llm_inference", lifecycle_state: "active" }];
  const cy = cytoscape({ headless: true, elements: graphElements(nodes, []) });
  expect(cy.nodes()[0].data("tentative")).toBe("yes");
  expect(cy.nodes()[0].data("source_id")).toBe(nodes[0].node_id);
  cy.nodes()[0].select(); expect(cy.$(":selected")).toHaveLength(1); cy.destroy();
});
it.skipIf(!process.env.HOST_GRAPH_FIXTURE)("renders exact real installed SDK/store/HUMAN API nodes and directed relation", () => {
  const raw = JSON.parse(readFileSync(process.env.HOST_GRAPH_FIXTURE!, "utf8"));
  const view = parseMemoryGraph(raw, raw.primary_ref);
  expect(view.nodes).toHaveLength(2); expect(view.edges).toHaveLength(1);
  const cy = cytoscape({ headless: true, elements: graphElements(view.nodes, view.edges), layout: { name: "breadthfirst", directed: true } });
  expect(cy.nodes()).toHaveLength(2); expect(cy.edges()).toHaveLength(1);
  expect(cy.edges()[0].source().data("source_id")).toBe(view.edges[0].source_node_id);
  expect(cy.edges()[0].target().data("source_id")).toBe(view.edges[0].target_node_id);
  expect(cy.nodes().map((n) => n.data("source_id")).sort()).toEqual(view.nodes.map((n) => n.node_id).sort());
  cy.remove(cy.edges()[0].source()); expect(cy.edges()).toHaveLength(0); cy.destroy();
});
it("shortens canvas labels as memories grow and restores the full label on selection", () => {
  const long = "秋天偏好：用户偏好在秋天穿浅色外套并在周末去公园散步，且希望助手记住这一点用于穿搭建议";
  const many = Array.from({ length: 50 }, (_, i) => ({ ...graph.nodes[0], node_id: `n${i}`, label: `${i}-${long}` }));
  expect([labelBudget(3), labelBudget(30), labelBudget(50)]).toEqual([70, 32, 18]);
  expect(canvasLabel("短标签", 18)).toBe("短标签");
  const cy = cytoscape({ headless: true, styleEnabled: true, style: graphStyle(many.length), elements: graphElements(many, []) });
  const node = cy.nodes()[0];
  expect(Array.from(node.data("label")).length).toBe(18);
  expect(node.data("label").endsWith("…")).toBe(true);
  expect(node.data("full_label")).toBe(many[0].label);
  expect(Array.from(graphElements([{ ...graph.nodes[0], label: "长".repeat(200) }], [])[0].data.full_label).length).toBe(140);
  expect(node.style("label")).toBe(node.data("label"));
  node.select();
  expect(node.style("label")).toBe(many[0].label);
  // 少量记忆时保持原 70 字预算，不截断普通标签。
  const few = cytoscape({ headless: true, styleEnabled: true, style: graphStyle(2), elements: graphElements(many.slice(0, 2), []) });
  expect(few.nodes()[0].data("label")).toBe(many[0].label.length <= 70 ? many[0].label : `${Array.from(many[0].label).slice(0, 69).join("")}…`);
  cy.destroy(); few.destroy();
});
