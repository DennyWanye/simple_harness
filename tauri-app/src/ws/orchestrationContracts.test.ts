// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/**
 * 推后第 2 批 U02：前端用 SDK 发布的同一批公开 Schema 核编排回复（HTN §17.2 L856）。
 * 正样本用 SDK 自己的执行图合同样例（同一份文件，不另造）；Assurance 正样本照 host-*-v1 手写。
 */
import { describe, expect, it } from "vitest";

import graphExamples from "../../../sdk/simple-harness-sdk/tests/orchestrator/acceptance_assets/taskgraph_schema_examples.json";
import {
  CONTRACT_SCHEMAS, PROTOCOL_ERROR, PROTOCOL_ERROR_TEXT, contractViolation, guardIncoming, supportedKeywords,
} from "./orchestrationContracts";

const HASH = "a".repeat(64);
const snapshot = (extra: Record<string, unknown> = {}) => ({
  schema_version: 1, request_id: "r1", mission_id: "m1", view: "CURRENT", snapshot_seq: 7,
  root_incarnation_id: "root-1", sdk_fingerprint: HASH, host_fingerprint: "b".repeat(64),
  items: [{ kind: "REVIEW", id: "rv-1", history_state: "OFFICIAL", current_use: "STALE", reason_codes: ["SOURCE_CHANGED"],
    evidence_count: 2, artifact_ref: { kind: "artifact", pin: { id: "art-1", revision: 3, content_hash: HASH } } }],
  next_cursor: null, truncated: false, ...extra,
});
const wire = (extra: Record<string, unknown> = {}) =>
  ({ schema_version: 1, request_id: "r1", code: "NOT_FOUND", message: "why", retryable: false, ...extra });
const reply = (type: string, payload: Record<string, unknown>) => ({ type: type + "_response", payload: { request_id: "r1", ...payload } });
const protocolError = (type: string) =>
  ({ type: type + "_response", payload: { request_id: "r1", ok: false, error_code: PROTOCOL_ERROR, error: PROTOCOL_ERROR_TEXT } });

describe("U02 编排回复按公开 Schema 核", () => {
  it("uses the SDK schema files themselves and supports every keyword they use", () => {
    expect(CONTRACT_SCHEMAS["host-response-v1"].$id).toMatch(/assurance\/1\.1\/host-response-v1\.schema\.json$/);
    expect(CONTRACT_SCHEMAS["taskgraph-convergence-view-v2"].$id).toBeTruthy();
    const used = new Set<string>();
    const walk = (node: unknown): void => {
      if (Array.isArray(node)) { node.forEach(walk); return; }
      if (!node || typeof node !== "object") return;
      for (const [key, value] of Object.entries(node)) {
        used.add(key);
        if (key === "properties" || key === "$defs") Object.values(value as object).forEach(walk);
        else if (key !== "const" && key !== "enum") walk(value);
      }
    };
    Object.values(CONTRACT_SCHEMAS).forEach(walk);
    for (const keyword of used) expect(supportedKeywords.has(keyword), keyword).toBe(true);
  });

  it("accepts the SDK's own positive examples for every taskgraph reply contract", () => {
    const examples = graphExamples as Record<string, unknown>;
    for (const name of ["taskgraph-view-v1", "taskgraph-explanation-v1", "taskgraph-diff-v1",
      "taskgraph-convergence-view-v2", "taskgraph-error-v1"] as const) {
      expect(contractViolation(name, examples[name]), name).toBeNull();
    }
  });

  it("refuses unknown fields, foreign enum values, broken bounds and wrong refs", () => {
    expect(contractViolation("host-response-v1", snapshot())).toBeNull();
    expect(contractViolation("host-response-v1", snapshot({ tenant_id: "other" }))).toMatch(/tenant_id/);
    expect(contractViolation("host-response-v1", snapshot({ view: "LIVE" }))).not.toBeNull();
    expect(contractViolation("host-response-v1", snapshot({ snapshot_seq: 1.5 }))).not.toBeNull();
    expect(contractViolation("host-response-v1", snapshot({ sdk_fingerprint: "A".repeat(64) }))).not.toBeNull();
    expect(contractViolation("host-response-v1", snapshot({ items: [{ ...snapshot().items[0],
      artifact_ref: { kind: "review_package", pin: { id: "x", revision: 1, content_hash: HASH } } }] }))).not.toBeNull();
    expect(contractViolation("host-response-v1", snapshot({ items: [{ ...snapshot().items[0], reason_codes: ["A", "A"] }] }))).not.toBeNull();
    expect(contractViolation("host-error-v1", wire())).toBeNull();
    expect(contractViolation("host-error-v1", wire({ code: "SOMETHING_NEW" }))).not.toBeNull();
    const convergence = (graphExamples as Record<string, Record<string, unknown>>)["taskgraph-convergence-view-v2"];
    expect(contractViolation("taskgraph-convergence-view-v2", { ...convergence, debug: {} })).toMatch(/debug/);
    expect(contractViolation("taskgraph-convergence-view-v2", { ...convergence, schema_version: 1 })).not.toBeNull();
  });

  it("passes valid replies through untouched and turns bad ones into a protocol error", () => {
    const good = reply("mission_assurance_snapshot", { ok: true, data: snapshot() });
    expect(guardIncoming(good)).toBe(good);
    expect(guardIncoming(reply("mission_assurance_snapshot", { ok: true, data: snapshot({ tenant_id: "x" }) })))
      .toEqual(protocolError("mission_assurance_snapshot"));
    const refused = reply("mission_assurance_review", { ok: false, error_code: "NOT_FOUND", error: "t", assurance_error: wire() });
    expect(guardIncoming(refused)).toBe(refused);
    expect(guardIncoming(reply("mission_assurance_review", { ok: false, error_code: "NOT_FOUND", error: "t",
      assurance_error: { code: "NOT_FOUND" } }))).toEqual(protocolError("mission_assurance_review"));
    const examples = graphExamples as Record<string, unknown>;
    const why = reply("taskgraph.why_not_ready", { ok: true, data: examples["taskgraph-explanation-v1"] });
    expect(guardIncoming(why)).toBe(why);
    expect(guardIncoming(reply("taskgraph.convergence", { ok: true, data: { schema_version: 2 } })))
      .toEqual(protocolError("taskgraph.convergence"));
    // an error wire on any taskgraph verb is checked, even one without a reply contract
    expect(guardIncoming(reply("taskgraph.execution_snapshot", { ok: false, error_code: "X", error: "t",
      taskgraph_error: { code: "X" } }))).toEqual(protocolError("taskgraph.execution_snapshot"));
    // a response without a boolean ok is not a reply at all
    expect(guardIncoming(reply("taskgraph.convergence", { data: examples["taskgraph-convergence-view-v2"] })))
      .toEqual(protocolError("taskgraph.convergence"));
  });

  it("leaves messages without a public contract alone", () => {
    const other = { type: "mission_get_response", payload: { request_id: "r1", ok: true, data: { anything: 1 } } };
    expect(guardIncoming(other)).toBe(other);
    const exec = { type: "taskgraph.execution_snapshot_response", payload: { request_id: "r1", ok: true, data: { nodes: [] } } };
    expect(guardIncoming(exec)).toBe(exec);
    const push = { type: "mission_changed", payload: { mission_id: "m1" } };
    expect(guardIncoming(push)).toBe(push);
  });
});
