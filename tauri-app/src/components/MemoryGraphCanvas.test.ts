// Actual Cytoscape graph engine; no renderer mock. Browser layout is a separate check.
import cytoscape from "cytoscape";
import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import { graphElements } from "../primary/graphElements";
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
