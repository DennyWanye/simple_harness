// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/**
 * 编排回复的公开合同核对（HTN §17.2 L856；Assurance §13.1 L354；推后第 2 批 U02）。
 *
 * - 合同就是 SDK 随包发布的那几份 JSON Schema 文件本身：这里直接 import 仓库里
 *   `sdk/simple-harness-sdk/src/agent_orchestrator/...` 下的文件，不抄副本。Host 出门前按同一批
 *   合同核过一遍（`backend/deskpet/orchestration/projection.py`，U03），两边的动词表同名。
 * - 只实现这几份文件实际用到的 JSON Schema 关键字；碰到没实现的关键字直接报错，不猜。
 * - `ControlChannel` 收到消息先过 `guardIncoming` 再分发：不合合同的回复换成协议错
 *   （`ok:false`、`error_code:"protocol_error"`、一句大白话），数据不往下传，界面保留上次画面。
 * - 只核形状（秩序），不判断内容对错。
 */
import common from "../../../sdk/simple-harness-sdk/src/agent_orchestrator/assurance/contracts/common.schema.json";
import hostResponse from "../../../sdk/simple-harness-sdk/src/agent_orchestrator/assurance/contracts/host-response-v1.schema.json";
import hostReviewResponse from "../../../sdk/simple-harness-sdk/src/agent_orchestrator/assurance/contracts/host-review-response-v1.schema.json";
import hostUseResponse from "../../../sdk/simple-harness-sdk/src/agent_orchestrator/assurance/contracts/host-use-response-v1.schema.json";
import hostError from "../../../sdk/simple-harness-sdk/src/agent_orchestrator/assurance/contracts/host-error-v1.schema.json";
import graphView from "../../../sdk/simple-harness-sdk/src/agent_orchestrator/graph/schemas/taskgraph-view-v1.schema.json";
import graphExplanation from "../../../sdk/simple-harness-sdk/src/agent_orchestrator/graph/schemas/taskgraph-explanation-v1.schema.json";
import graphDiff from "../../../sdk/simple-harness-sdk/src/agent_orchestrator/graph/schemas/taskgraph-diff-v1.schema.json";
import graphConvergence from "../../../sdk/simple-harness-sdk/src/agent_orchestrator/graph/schemas/taskgraph-convergence-view-v2.schema.json";
import graphError from "../../../sdk/simple-harness-sdk/src/agent_orchestrator/graph/schemas/taskgraph-error-v1.schema.json";

type Schema = { [key: string]: unknown };

export const PROTOCOL_ERROR = "protocol_error";
/** Host 与前端同一句（Host 侧见 projection.PROTOCOL_ERROR_TEXT）。 */
export const PROTOCOL_ERROR_TEXT = "收到的数据格式不对，没有显示，请稍后重新读取。";

export const CONTRACT_SCHEMAS = {
  "host-response-v1": hostResponse as Schema,
  "host-review-response-v1": hostReviewResponse as Schema,
  "host-use-response-v1": hostUseResponse as Schema,
  "host-error-v1": hostError as Schema,
  "taskgraph-view-v1": graphView as Schema,
  "taskgraph-explanation-v1": graphExplanation as Schema,
  "taskgraph-diff-v1": graphDiff as Schema,
  "taskgraph-convergence-view-v2": graphConvergence as Schema,
  "taskgraph-error-v1": graphError as Schema,
} as const;
export type ContractName = keyof typeof CONTRACT_SCHEMAS;

/** `$ref` 里按文件名引用的文档（Assurance 合同引用同目录的 common.schema.json）。 */
const DOCUMENTS: Record<string, Schema> = { "common.schema.json": common as Schema };

/** 动词 → 回复合同（与 Host projection.ASSURANCE_REPLIES / TASKGRAPH_REPLIES 同表）。 */
const REPLIES: Record<string, ContractName> = {
  mission_assurance_snapshot: "host-response-v1",
  mission_assurance_review: "host-review-response-v1",
  mission_assurance_use_check: "host-use-response-v1",
  "taskgraph.snapshot": "taskgraph-view-v1",
  "taskgraph.why_not_ready": "taskgraph-explanation-v1",
  "taskgraph.diff": "taskgraph-diff-v1",
  "taskgraph.convergence": "taskgraph-convergence-view-v2",
};

export const supportedKeywords: ReadonlySet<string> = new Set([
  "$schema", "$id", "$defs", "$ref", "type", "const", "enum", "required", "properties", "additionalProperties",
  "items", "minItems", "maxItems", "uniqueItems", "contains", "minContains", "maxContains",
  "minLength", "maxLength", "pattern", "minimum", "maximum", "allOf", "anyOf", "oneOf",
]);

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (isObject(value)) return "{" + Object.keys(value).sort().map((key) => JSON.stringify(key) + ":" + canonical(value[key])).join(",") + "}";
  return JSON.stringify(value);
}

function typeOk(value: unknown, name: string): boolean {
  switch (name) {
    case "object": return isObject(value);
    case "array": return Array.isArray(value);
    case "string": return typeof value === "string";
    case "integer": return typeof value === "number" && Number.isInteger(value);
    case "number": return typeof value === "number" && Number.isFinite(value);
    case "boolean": return typeof value === "boolean";
    case "null": return value === null;
    default: throw new Error(`合同里有未支持的类型 ${name}`);
  }
}

function resolve(ref: string, document: Schema): [Schema, Schema] {
  const [file, pointer = ""] = ref.split("#");
  const target = file ? DOCUMENTS[file] : document;
  if (!target) throw new Error(`合同引用了未登记的文档 ${file}`);
  let node: unknown = target;
  for (const token of pointer.split("/").filter(Boolean)) {
    node = (node as Schema)[token.replace(/~1/g, "/").replace(/~0/g, "~")];
  }
  if (!isObject(node)) throw new Error(`合同引用 ${ref} 找不到`);
  return [node, target];
}

/** 第一处不合合同的位置；合规返回 null。 */
function check(node: Schema, value: unknown, document: Schema, path: string): string | null {
  for (const key of Object.keys(node)) {
    if (!supportedKeywords.has(key)) throw new Error(`合同用了未支持的关键字 ${key}（${path}）`);
  }
  if (typeof node.$ref === "string") {
    const [target, targetDocument] = resolve(node.$ref, document);
    const found = check(target, value, targetDocument, path);
    if (found) return found;
  }
  if ("const" in node && canonical(value) !== canonical(node.const)) return `${path}: 应为固定值`;
  if (Array.isArray(node.enum) && !node.enum.some((item) => canonical(item) === canonical(value))) return `${path}: 不在允许的取值里`;
  if (node.type !== undefined) {
    const names = Array.isArray(node.type) ? node.type as string[] : [node.type as string];
    if (!names.some((name) => typeOk(value, name))) return `${path}: 类型不对`;
  }
  for (const option of (node.allOf as Schema[] | undefined) ?? []) {
    const found = check(option, value, document, path);
    if (found) return found;
  }
  if (Array.isArray(node.anyOf) && !(node.anyOf as Schema[]).some((option) => check(option, value, document, path) === null)) {
    return `${path}: 一个备选都不符合`;
  }
  if (Array.isArray(node.oneOf) && (node.oneOf as Schema[]).filter((option) => check(option, value, document, path) === null).length !== 1) {
    return `${path}: 应恰好符合一个备选`;
  }
  if (typeof value === "string") {
    const length = [...value].length;  // JSON Schema 按字符（码点）计长
    if (typeof node.minLength === "number" && length < node.minLength) return `${path}: 太短`;
    if (typeof node.maxLength === "number" && length > node.maxLength) return `${path}: 太长`;
    if (typeof node.pattern === "string" && !new RegExp(node.pattern, "u").test(value)) return `${path}: 格式不对`;
  }
  if (typeof value === "number") {
    if (typeof node.minimum === "number" && value < node.minimum) return `${path}: 小于下限`;
    if (typeof node.maximum === "number" && value > node.maximum) return `${path}: 大于上限`;
  }
  if (Array.isArray(value)) {
    if (typeof node.minItems === "number" && value.length < node.minItems) return `${path}: 条目太少`;
    if (typeof node.maxItems === "number" && value.length > node.maxItems) return `${path}: 条目太多`;
    if (node.uniqueItems === true && new Set(value.map(canonical)).size !== value.length) return `${path}: 条目重复`;
    if (isObject(node.items)) {
      for (let index = 0; index < value.length; index += 1) {
        const found = check(node.items, value[index], document, `${path}[${index}]`);
        if (found) return found;
      }
    }
    if (isObject(node.contains)) {
      const contains = node.contains;
      const hits = value.filter((item, index) => check(contains, item, document, `${path}[${index}]`) === null).length;
      const least = typeof node.minContains === "number" ? node.minContains : 1;
      if (hits < least) return `${path}: 缺少必需的条目`;
      if (typeof node.maxContains === "number" && hits > node.maxContains) return `${path}: 必需条目过多`;
    }
  }
  if (isObject(value)) {
    const properties = isObject(node.properties) ? node.properties as Record<string, Schema> : {};
    for (const key of (node.required as string[] | undefined) ?? []) {
      if (!(key in value)) return `${path}: 缺少字段 ${key}`;
    }
    if (node.additionalProperties === false) {
      const extra = Object.keys(value).filter((key) => !(key in properties));
      if (extra.length) return `${path}: 多出字段 ${extra.sort().join(", ")}`;
    }
    for (const [key, item] of Object.entries(value)) {
      if (key in properties) {
        const found = check(properties[key], item, document, path ? `${path}.${key}` : key);
        if (found) return found;
      }
    }
  }
  return null;
}

/** 按名核一份合同：合规返回 null，否则返回第一处不合的位置（不含数据值）。 */
export function contractViolation(name: ContractName, value: unknown): string | null {
  const schema = CONTRACT_SCHEMAS[name];
  return check(schema, value, schema, "$");
}

type Message = { type?: unknown; payload?: unknown };

function verbOf(type: unknown): string | null {
  if (typeof type !== "string" || !type.endsWith("_response")) return null;
  const verb = type.slice(0, -"_response".length);
  return verb.startsWith("mission_assurance_") || verb.startsWith("taskgraph.") ? verb : null;
}

/** 这条回复哪里不合合同；不归公开合同管的消息返回 null。 */
function violationOf(verb: string, payload: unknown): string | null {
  if (!isObject(payload) || typeof payload.ok !== "boolean") return "回复外层不完整";
  const reply = REPLIES[verb];
  if (payload.ok && reply) return contractViolation(reply, payload.data);
  if (!payload.ok && payload.assurance_error !== undefined) return contractViolation("host-error-v1", payload.assurance_error);
  if (!payload.ok && payload.taskgraph_error !== undefined) return contractViolation("taskgraph-error-v1", payload.taskgraph_error);
  return null;
}

/**
 * 控制通道收到的一条消息：归公开合同管且不合合同的，换成协议错；其余原样返回（同一个对象）。
 * 协议错只带请求号，数据和原回执都不往下传。
 */
export function guardIncoming<T extends Message>(message: T): T {
  const verb = verbOf(message.type);
  if (!verb) return message;
  const violation = violationOf(verb, message.payload);
  if (violation === null) return message;
  console.warn(`[编排合同] ${String(message.type)} 不合合同：${violation}`);
  const requestId = isObject(message.payload) ? message.payload.request_id : undefined;
  return { ...message, payload: { request_id: requestId, ok: false, error_code: PROTOCOL_ERROR, error: PROTOCOL_ERROR_TEXT } };
}
