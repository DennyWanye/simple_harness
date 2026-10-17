// 代码质量整治阶段 9（9G）：控制通道 dispatch 拆成处理函数表的核对。
// 用法：node scripts/code_quality/ts_dispatch_check.mjs [ref]   （在仓库根目录运行，默认 ref=HEAD）
// 原：ref 里 tauri-app/src/code-panel/controlWs.ts 的 dispatch 里的 switch，取每个 case 标签与分支体。
// 现：工作区 controlWs.ts 的 CONTROL_HANDLERS 表（标签 → 函数名），到 src/code-panel/controlWs*.ts 找同名函数的函数体。
// 比对：标签全集相同（消息类型快照）；每个标签的分支体与函数体按语法树逐项相同。
// 规整：分支体若只有一个块就展开；分支级 break（不在循环、内层 switch、函数里）记作 return；末尾的 break 去掉；注释与排版不计。
import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(new URL("../../tauri-app/package.json", import.meta.url));
const ts = require("typescript");
const ref = process.argv[2] || "HEAD";
const DIR = "tauri-app/src/code-panel";
const FILE = `${DIR}/controlWs.ts`;
const parse = (name, text) => ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true);

function serialize(stmts, sf, caseBreaks) {
  const items = [];
  const walk = (n) => {
    if (caseBreaks?.has(n) || (ts.isReturnStatement(n) && !n.expression)) { items.push("ReturnStatement"); return; }
    const kind = ts.SyntaxKind[n.kind];
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n) || ts.isTemplateHead(n) ||
        ts.isTemplateMiddle(n) || ts.isTemplateTail(n)) { items.push(`${kind} ${JSON.stringify(n.text)}`); return; }
    const kids = [];
    n.forEachChild((c) => { kids.push(c); });
    // 没有子节点时：标识符与字面量记文本；空块、关键字等只记种类（空块的文本含注释与缩进）。
    const textual = ts.isIdentifier(n) || ts.isPrivateIdentifier(n) || ts.isNumericLiteral(n) ||
      ts.isBigIntLiteral(n) || ts.isRegularExpressionLiteral(n);
    if (kids.length === 0) items.push(textual ? `${kind} ${n.getText(sf)}` : kind);
    else { items.push(kind); kids.forEach(walk); }
  };
  stmts.forEach(walk);
  return items.join("\n");
}

function oldBodies(text) {
  const sf = parse(FILE, text);
  const dispatch = sf.statements.find((s) => ts.isFunctionDeclaration(s) && s.name?.text === "dispatch");
  const sw = dispatch.body.statements.find((s) => ts.isSwitchStatement(s));
  const out = new Map();
  let labels = [];
  for (const cl of sw.caseBlock.clauses) {
    if (ts.isDefaultClause(cl)) { labels = []; continue; }
    labels.push(JSON.parse(cl.expression.getText(sf)));
    if (cl.statements.length === 0) continue;
    const breaks = new Set();
    const visit = (n, nested) => {
      if (ts.isBreakStatement(n) && !n.label && !nested) breaks.add(n);
      const inner = ts.isIterationStatement(n, false) || ts.isSwitchStatement(n);
      if (ts.isFunctionLike(n)) { n.forEachChild((c) => visit(c, true)); return; }
      n.forEachChild((c) => visit(c, nested || inner));
    };
    cl.statements.forEach((s) => visit(s, false));
    let stmts = [...cl.statements];
    if (stmts.length === 1 && ts.isBlock(stmts[0])) stmts = [...stmts[0].statements];
    const last = stmts[stmts.length - 1];
    if (last && breaks.has(last)) stmts.pop();
    const body = serialize(stmts, sf, breaks);
    labels.forEach((l) => out.set(l, body));
    labels = [];
  }
  return out;
}

function newBodies() {
  const files = readdirSync(DIR).filter((f) => /^controlWs.*\.ts$/.test(f) && !f.includes(".test."));
  const fns = new Map();
  let table = null;
  for (const f of files) {
    const sf = parse(f, readFileSync(`${DIR}/${f}`, "utf8"));
    for (const st of sf.statements) {
      if (ts.isFunctionDeclaration(st) && st.name && st.body) {
        if (fns.has(st.name.text)) throw new Error(`函数重名：${st.name.text}`);
        fns.set(st.name.text, serialize([...st.body.statements], sf, null));
      }
      if (f === "controlWs.ts" && ts.isVariableStatement(st))
        for (const d of st.declarationList.declarations)
          if (ts.isIdentifier(d.name) && d.name.text === "CONTROL_HANDLERS") table = d.initializer;
    }
  }
  if (!table) throw new Error("找不到 CONTROL_HANDLERS");
  const arr = table.arguments[0];
  const out = new Map();
  for (const el of arr.elements) {
    const [label, fn] = el.elements;
    const name = fn.text;
    if (!fns.has(name)) throw new Error(`表里的函数找不到：${name}`);
    if (out.has(label.text)) throw new Error(`标签重复：${label.text}`);
    out.set(label.text, fns.get(name));
  }
  return out;
}

const before = oldBodies(execFileSync("git", ["show", `${ref}:${FILE}`], { encoding: "utf8" }));
const after = newBodies();
const problems = [];
for (const l of before.keys()) if (!after.has(l)) problems.push(`消失的消息类型：${l}`);
for (const l of after.keys()) if (!before.has(l)) problems.push(`新增的消息类型：${l}`);
for (const [l, b] of before) {
  if (!after.has(l) || after.get(l) === b) continue;
  const x = b.split("\n"), y = after.get(l).split("\n");
  let i = 0;
  while (i < x.length && x[i] === y[i]) i++;
  problems.push(`分支体变了：${l}（第 ${i + 1} 项：原 ${x[i] ?? "<无>"}；现 ${y[i] ?? "<无>"}）`);
}
console.log(`消息类型：原 ${before.size}，现 ${after.size}`);
problems.forEach((p) => console.log(p));
console.log(problems.length ? "分发表核对：不通过" : "分发表核对：通过");
process.exit(problems.length ? 1 : 0);
