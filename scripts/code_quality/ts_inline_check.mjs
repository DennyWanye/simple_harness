// 代码质量整治阶段 9（9I）：内联样式转类的静态核对（裁决条件 3，不循环）。
// 用法（在仓库根目录运行）：node scripts/code_quality/ts_inline_check.mjs [--ref 提交] [--canary] [文件...]
//   默认 ref=HEAD；不给文件时取工作区相对 ref 改过、且旁边有 <名>.styles.css 的 .tsx。
// 原内联一侧（与转换工具无关）：从 ref 导出整个 tauri-app 副本，在副本里为每个文件生成探针模块
//   （原文件里探针表达式传递用到的导入与顶层声明 + 导出原 style 表达式的数组），用 vitest
//   （jsdom，与单测同一套配置）真正执行求值；
//   再按 React 客户端的规则（react-dom-client 的 setValueForStyle：跳过空值、数字按 React 自己的
//   无单位属性表补 px、字符串去首尾空白）得到"属性→值"有序表。无单位属性表直接从 react-dom 源码读出。
// 生成一侧：postcss 解析 <名>.styles.css，把整值为 var(--sh-…令牌变量) 的换回 tokens.ts 的变量值。
// 两表逐条（含顺序）相同才通过。另查：
//   - 两版 JSX 元素一一对应，除 style 与 className 外属性语法树相同；className 只多出生成类；
//   - 文件其余部分语法树相同（只许删掉转换后不再引用的模块级常量、非导出函数与导入，加 .styles.css 导入）；
//   - 生成的值不含 ! ; { } /* \ <。
// --canary：在内存里改坏一个值、改坏一处转义，必须各报出一条，否则核对工具本身不合格。
import { execFileSync, spawnSync } from "node:child_process";
import { readFileSync, writeFileSync, existsSync, mkdtempSync, symlinkSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const ROOT = path.resolve(".");
const APP = path.join(ROOT, "tauri-app");
const require = createRequire(path.join(APP, "package.json"));
const ts = require("typescript");
const postcss = require("postcss");

const args = process.argv.slice(2);
let ref = "HEAD";
let canary = false;
const files = [];
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--ref") ref = args[++i];
  else if (args[i] === "--canary") canary = true;
  else files.push(path.relative(ROOT, path.resolve(args[i])));
}
if (!files.length) {
  const changed = execFileSync("git", ["diff", "--name-only", ref, "--", "tauri-app/src"], { encoding: "utf8" }).split("\n");
  for (const f of changed) if (f.endsWith(".tsx") && existsSync(f.replace(/\.tsx$/, ".styles.css"))) files.push(f);
}
const { TOKEN_CSS_VARS } = await import(pathToFileURL(path.join(APP, "src/theme/tokens.ts")).href);
const DANGEROUS = /[!;{}\\<]|\/\*/;
// 前端直接引入的 SDK 契约 JSON（tauri-app 之外），导出副本时一并带上。
const SDK_ASSETS = ["sdk/simple-harness-sdk/src/agent_orchestrator/assurance/contracts", "sdk/simple-harness-sdk/src/agent_orchestrator/graph/schemas", "sdk/simple-harness-sdk/tests/orchestrator/acceptance_assets"];
const problems = [];
const parse = (name, text) => ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);

// ---- 语法树序列化（注释与排版不计） ----
function serialize(node, sf, skip) {
  const items = [];
  const walk = (n) => {
    if (skip?.(n)) return;
    const kind = ts.SyntaxKind[n.kind];
    if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n) || ts.isTemplateHead(n) || ts.isTemplateMiddle(n) ||
        ts.isTemplateTail(n) || ts.isJsxText(n)) {
      const t = ts.isJsxText(n) ? n.text.replace(/\s+/g, " ").trim() : n.text;
      if (!ts.isJsxText(n) || t) items.push(`${kind} ${JSON.stringify(t)}`);
      return;
    }
    if (ts.isParenthesizedExpression(n)) { walk(n.expression); return; }
    if (ts.isJsxElement(n) || ts.isJsxFragment(n)) {
      items.push(kind);
      walk(ts.isJsxElement(n) ? n.openingElement : n.openingFragment);
      jsxChildren(n.children, sf).forEach((c) => (typeof c === "string" ? items.push(`JsxText ${JSON.stringify(c)}`) : walk(c)));
      walk(ts.isJsxElement(n) ? n.closingElement : n.closingFragment);
      return;
    }
    const kids = [];
    n.forEachChild((c) => { kids.push(c); });
    const textual = ts.isIdentifier(n) || ts.isPrivateIdentifier(n) || ts.isNumericLiteral(n) || ts.isBigIntLiteral(n) || ts.isRegularExpressionLiteral(n);
    if (!kids.length) items.push(textual ? `${kind} ${n.getText(sf)}` : kind);
    else { items.push(kind); kids.forEach(walk); }
  };
  walk(node);
  return items.join("\n");
}

/**
 * JSX 子节点按 React 的空白规则规整（与 Babel 的 cleanJSXElementLiteralChild 相同）：文本按行去首尾空白、
 * 去掉空行、行间用一个空格连接；{"…"} 字符串表达式当文本；相邻文本合并。prettier 改写 {" "} 与换行不影响结果。
 */
function jsxChildren(children, sf) {
  const out = [];
  const pushText = (t) => {
    if (!t) return;
    if (typeof out[out.length - 1] === "string") out[out.length - 1] += t;
    else out.push(t);
  };
  for (const c of children) {
    if (ts.isJsxText(c)) {
      const lines = c.text.split(/\r\n|\n|\r/);
      let last = 0;
      lines.forEach((l, i) => { if (/[^ \t]/.test(l)) last = i; });
      let str = "";
      lines.forEach((line, i) => {
        let t = line.replace(/\t/g, " ");
        if (i !== 0) t = t.replace(/^[ ]+/, "");
        if (i !== lines.length - 1) t = t.replace(/[ ]+$/, "");
        if (t) str += t + (i !== last ? " " : "");
      });
      pushText(str);
    } else if (ts.isJsxExpression(c) && c.expression && (ts.isStringLiteral(c.expression) || ts.isNoSubstitutionTemplateLiteral(c.expression))) {
      pushText(c.expression.text);
    } else if (ts.isJsxExpression(c) && !c.expression) {
      continue; // {/* 注释 */}
    } else out.push(c);
  }
  return out;
}

const attrsOf = (el) => (ts.isJsxElement(el) ? el.openingElement : el).attributes.properties;
const attrNamed = (el, name) => attrsOf(el).find((a) => ts.isJsxAttribute(a) && a.name.getText() === name);
function jsxElements(sf) {
  const out = [];
  const visit = (n) => {
    if (ts.isJsxSelfClosingElement(n) || ts.isJsxOpeningElement(n)) out.push(n);
    n.forEachChild(visit);
  };
  visit(sf);
  return out;
}
const styleExpr = (el) => {
  const a = attrsOf(el).find((x) => ts.isJsxAttribute(x) && x.name.getText() === "style");
  return a?.initializer && ts.isJsxExpression(a.initializer) ? a.initializer.expression : null;
};
/** 表达式位置上的标识符（不含属性名、JSX 属性名、小写标签名这类不引用变量的位置）。 */
const identifiersIn = (sf) => {
  const s = new Set();
  const visit = (n) => {
    if (ts.isIdentifier(n)) {
      const p = n.parent;
      const notRef = (ts.isPropertyAccessExpression(p) && p.name === n) || (ts.isPropertyAssignment(p) && p.name === n) ||
        (ts.isJsxAttribute(p) && p.name === n) || (ts.isPropertySignature(p) && p.name === n) ||
        (ts.isBindingElement(p) && p.propertyName === n) || (ts.isQualifiedName(p) && p.right === n) ||
        ((ts.isJsxOpeningElement(p) || ts.isJsxClosingElement(p) || ts.isJsxSelfClosingElement(p)) && p.tagName === n && /^[a-z]/.test(n.text));
      if (!notRef) s.add(n.text);
    }
    n.forEachChild(visit);
  };
  visit(sf);
  return s;
};

/** className 的变化只许是"多出一个生成类"；返回新增的类名（无变化返回 null）。 */
function addedClass(oldAttr, newAttr, osf, nsf, where) {
  const val = (a) => {
    if (!a) return null;
    const i = a.initializer;
    if (ts.isStringLiteral(i)) return { lit: i.text };
    const e = i.expression;
    if (ts.isStringLiteral(e) || ts.isNoSubstitutionTemplateLiteral(e)) return { lit: e.text };
    return { expr: e };
  };
  const o = val(oldAttr), n = val(newAttr);
  if (!o && !n) return null;
  if (!n) { problems.push(`${where}：className 被删了`); return null; }
  if (!o) {
    if (n.lit && /^sh-[A-Za-z0-9]+-\d+$/.test(n.lit)) return n.lit;
    problems.push(`${where}：新加的 className 不是单个生成类`);
    return null;
  }
  if (o.lit !== undefined && n.lit !== undefined) {
    if (o.lit === n.lit) return null;
    const m = n.lit.startsWith(o.lit) ? n.lit.slice(o.lit.length).match(/^ ?(sh-[A-Za-z0-9]+-\d+)$/) : null;
    if (m && (o.lit === "" ? !n.lit.startsWith(" ") : n.lit[o.lit.length] === " ")) return m[1];
    problems.push(`${where}：字面量 className 的变化不只是加生成类（${JSON.stringify(o.lit)} → ${JSON.stringify(n.lit)}）`);
    return null;
  }
  if (o.expr && n.expr && serialize(o.expr, osf) === serialize(n.expr, nsf)) return null;
  // 模板：末尾多出 " 类名"；静态表达式：包成 `${原表达式} 类名`
  if (n.expr && ts.isTemplateExpression(n.expr)) {
    const spans = n.expr.templateSpans;
    const tail = spans[spans.length - 1].literal.text;
    const m = tail.match(/ (sh-[A-Za-z0-9]+-\d+)$/);
    if (m) {
      if (o.expr && ts.isTemplateExpression(o.expr)) {
        const os = o.expr.templateSpans;
        const sameHead = o.expr.head.text === n.expr.head.text && os.length === spans.length;
        const sameSpans = sameHead && os.every((s, i) => serialize(s.expression, osf) === serialize(spans[i].expression, nsf) &&
          (i < os.length - 1 ? s.literal.text === spans[i].literal.text : s.literal.text + ` ${m[1]}` === tail));
        if (sameSpans) return m[1];
      } else if (o.expr && n.expr.head.text === "" && spans.length === 1 && tail === ` ${m[1]}` &&
                 serialize(o.expr, osf) === serialize(spans[0].expression, nsf)) {
        return m[1];
      }
    }
  }
  problems.push(`${where}：表达式 className 的变化不只是加生成类`);
  return null;
}

// ---- 逐文件比对语法树，收集被转的原 style 表达式 ----
const plan = [];
for (const rel of files) {
  const oldText = execFileSync("git", ["show", `${ref}:${rel}`], { encoding: "utf8" });
  const newText = readFileSync(rel, "utf8");
  const osf = parse(rel, oldText), nsf = parse(rel, newText);
  const oe = jsxElements(osf), ne = jsxElements(nsf);
  const sites = [];
  const skipNodes = new Set();
  if (oe.length !== ne.length) { problems.push(`${rel}：JSX 元素数不同（${oe.length} → ${ne.length}）`); continue; }
  oe.forEach((o, i) => {
    const n = ne[i];
    const where = `${rel}:${osf.getLineAndCharacterOfPosition(o.getStart()).line + 1}`;
    if (o.tagName.getText(osf) !== n.tagName.getText(nsf)) { problems.push(`${where}：标签不同`); return; }
    // 嵌套在属性里的 JSX（如 banner={<div style=…/>}）也是被比对的元素，它们的 style/className 各自核对
    const styleOrClass = (x) => ts.isJsxAttribute(x) && ["style", "className"].includes(x.name.getText());
    const rest = (el, sf) => attrsOf(el).filter((a) => !styleOrClass(a)).map((a) => serialize(a, sf, styleOrClass)).join("\n--\n");
    if (rest(o, osf) !== rest(n, nsf)) problems.push(`${where}：style/className 以外的属性变了`);
    for (const a of attrsOf(o)) if (ts.isJsxAttribute(a) && ["style", "className"].includes(a.name.getText(osf))) skipNodes.add(a);
    for (const a of attrsOf(n)) if (ts.isJsxAttribute(a) && ["style", "className"].includes(a.name.getText(nsf))) skipNodes.add(a);
    const os = styleExpr(o), ns = styleExpr(n);
    const cls = addedClass(attrNamed(o, "className"), attrNamed(n, "className"), osf, nsf, where);
    if (os && ns) {
      if (serialize(os, osf) !== serialize(ns, nsf)) problems.push(`${where}：留下的 style 变了`);
      if (cls) problems.push(`${where}：留着 style 却加了生成类`);
    } else if (os && !ns) {
      sites.push({ where, expr: oldText.slice(os.getStart(osf), os.getEnd()), cls });
    } else if (!os && ns) problems.push(`${where}：新出现 style`);
    else if (cls) problems.push(`${where}：没有 style 却加了生成类`);
  });
  // 文件其余部分：导入与被删的模块级常量单独查，其余语句逐条相同
  const newIds = identifiersIn(nsf);
  const declNames = (st) => st.declarationList.declarations.flatMap((d) => {
    const out = [];
    const c = (b) => (ts.isIdentifier(b) ? out.push(b.text) : b.elements.forEach((e) => !ts.isOmittedExpression(e) && c(e.name)));
    c(d.name);
    return out;
  });
  const newTopNames = new Set(nsf.statements.filter(ts.isVariableStatement).flatMap(declNames));
  const keepOld = osf.statements.filter((st) => {
    if (ts.isImportDeclaration(st)) return false;
    if (ts.isFunctionDeclaration(st) && st.name && !nsf.statements.some((x) => ts.isFunctionDeclaration(x) && x.name?.text === st.name.text)) {
      const exported = st.modifiers?.some((m) => m.kind === ts.SyntaxKind.ExportKeyword);
      if (exported) problems.push(`${rel}：删掉了导出函数 ${st.name.text}`);
      if (newIds.has(st.name.text)) problems.push(`${rel}：删掉的函数 ${st.name.text} 在新文件里仍有同名引用`);
      return false;
    }
    if (ts.isVariableStatement(st)) {
      const names = declNames(st);
      if (names.every((x) => !newTopNames.has(x))) {
        const isConst = (st.declarationList.flags & ts.NodeFlags.Const) !== 0;
        const exported = st.modifiers?.some((m) => m.kind === ts.SyntaxKind.ExportKeyword);
        if (!isConst || exported) problems.push(`${rel}：删掉了非 const 或导出的模块级声明 ${names.join(",")}`);
        for (const x of names) if (newIds.has(x)) problems.push(`${rel}：删掉的常量 ${x} 在新文件里仍有同名引用`);
        return false;
      }
    }
    return true;
  });
  const keepNew = nsf.statements.filter((st) => !ts.isImportDeclaration(st));
  const skip = (n) => skipNodes.has(n);
  if (keepOld.length !== keepNew.length) problems.push(`${rel}：顶层语句数不同（去掉导入与被删常量后 ${keepOld.length} → ${keepNew.length}）`);
  else keepOld.forEach((st, i) => {
    const x = serialize(st, osf, skip).split("\n"), y = serialize(keepNew[i], nsf, skip).split("\n");
    if (x.join("\n") === y.join("\n")) return;
    let k = 0;
    while (k < x.length && x[k] === y[k]) k++;
    problems.push(`${rel}:${osf.getLineAndCharacterOfPosition(st.getStart()).line + 1}：顶层语句的语法树变了（第 ${k + 1} 项：原 ${x[k] ?? "<无>"}；现 ${y[k] ?? "<无>"}）`);
  });
  // 导入：新文件的每条导入都来自旧文件（可少说明符），或是本文件的 .styles.css；删掉的名字不许再出现
  const base = path.basename(rel, ".tsx");
  const oldImports = osf.statements.filter(ts.isImportDeclaration);
  const specOf = (st) => st.moduleSpecifier.text;
  const namesOf = (st) => {
    const c = st.importClause;
    if (!c) return [];
    const out = c.name ? [c.name.text] : [];
    if (c.namedBindings) out.push(...(ts.isNamedImports(c.namedBindings) ? c.namedBindings.elements.map((e) => e.name.text) : [c.namedBindings.name.text]));
    return out;
  };
  const newImportNames = new Set();
  for (const st of nsf.statements.filter(ts.isImportDeclaration)) {
    if (!st.importClause && specOf(st) === `./${base}.styles.css`) continue;
    namesOf(st).forEach((x) => newImportNames.add(x));
    const match = oldImports.find((o) => specOf(o) === specOf(st) && namesOf(st).every((x) => namesOf(o).includes(x)) &&
      Boolean(o.importClause?.isTypeOnly) === Boolean(st.importClause?.isTypeOnly));
    if (!match) problems.push(`${rel}：新文件里有旧文件没有的导入 ${specOf(st)}`);
  }
  for (const o of oldImports) {
    if (!o.importClause) {
      if (!nsf.statements.some((st) => ts.isImportDeclaration(st) && !st.importClause && specOf(st) === specOf(o)))
        problems.push(`${rel}：删掉了副作用导入 ${specOf(o)}`);
      continue;
    }
    for (const x of namesOf(o)) if (!newImportNames.has(x) && newIds.has(x)) problems.push(`${rel}：删掉的导入名 ${x} 在新文件里仍被引用`);
  }
  if (sites.length) plan.push({ rel, sites });
}

/**
 * 探针模块：只保留探针表达式（传递地）用到的导入说明符与顶层声明，按原顺序；其余顶层语句
 * （挂载根节点、注册监听之类的副作用）不带。名字按标识符文本收集（宁多勿少）。
 */
function probeModule(rel, text, exprs) {
  const sf = parse(rel, text);
  const exprSf = parse("probe.tsx", exprs.map((e) => `(${e});`).join("\n"));
  const declares = new Map();
  const names = (st) => {
    if (ts.isVariableStatement(st)) {
      const out = [];
      const c = (b) => (ts.isIdentifier(b) ? out.push(b.text) : b.elements.forEach((e) => !ts.isOmittedExpression(e) && c(e.name)));
      st.declarationList.declarations.forEach((d) => c(d.name));
      return out;
    }
    if ((ts.isFunctionDeclaration(st) || ts.isClassDeclaration(st) || ts.isEnumDeclaration(st)) && st.name) return [st.name.text];
    return [];
  };
  sf.statements.forEach((st) => names(st).forEach((x) => declares.set(x, st)));
  const importOf = new Map();
  for (const st of sf.statements.filter(ts.isImportDeclaration)) {
    const c = st.importClause;
    if (!c || c.isTypeOnly) continue;
    if (c.name) importOf.set(c.name.text, { st, kind: "default" });
    if (c.namedBindings && ts.isNamespaceImport(c.namedBindings)) importOf.set(c.namedBindings.name.text, { st, kind: "ns" });
    if (c.namedBindings && ts.isNamedImports(c.namedBindings))
      for (const e of c.namedBindings.elements) if (!e.isTypeOnly) importOf.set(e.name.text, { st, kind: "named", e });
  }
  const keep = new Set();
  const usedImports = new Set();
  const queue = [];
  const scan = (node) => { const v = (n) => { if (ts.isIdentifier(n)) queue.push(n.text); n.forEachChild(v); }; v(node); };
  scan(exprSf);
  const seen = new Set();
  while (queue.length) {
    const x = queue.pop();
    if (seen.has(x)) continue;
    seen.add(x);
    if (importOf.has(x)) usedImports.add(x);
    const st = declares.get(x);
    if (st && !keep.has(st)) { keep.add(st); scan(st); }
  }
  const lines = [];
  for (const st of sf.statements.filter(ts.isImportDeclaration)) {
    const c = st.importClause;
    if (!c || c.isTypeOnly) continue;
    const spec = st.moduleSpecifier.getText(sf);
    const parts = [];
    if (c.name && usedImports.has(c.name.text)) parts.push(c.name.text);
    if (c.namedBindings && ts.isNamespaceImport(c.namedBindings) && usedImports.has(c.namedBindings.name.text))
      parts.push(`* as ${c.namedBindings.name.text}`);
    if (c.namedBindings && ts.isNamedImports(c.namedBindings)) {
      const named = c.namedBindings.elements.filter((e) => !e.isTypeOnly && usedImports.has(e.name.text)).map((e) => e.getText(sf));
      if (named.length) parts.push(`{ ${named.join(", ")} }`);
    }
    if (parts.length) lines.push(`import ${parts.join(", ")} from ${spec};`);
  }
  for (const st of sf.statements) if (keep.has(st)) lines.push(st.getText(sf));
  lines.push(`export const __shProbe = [\n${exprs.map((e) => `  (${e}),`).join("\n")}\n];`);
  return lines.join("\n") + "\n";
}

// ---- 原内联一侧：在 ref 副本里真正执行求值 ----
const tmp = mkdtempSync(path.join(process.env.TMPDIR ?? os.tmpdir(), "inline-check-"));
const before = {};
try {
  const tar = execFileSync("git", ["archive", ref, "tauri-app", ...SDK_ASSETS], { maxBuffer: 1 << 30 });
  execFileSync("tar", ["-x", "-C", tmp], { input: tar });
  const app = path.join(tmp, "tauri-app");
  symlinkSync(path.join(APP, "node_modules"), path.join(app, "node_modules"));
  const probes = [];
  plan.forEach(({ rel, sites }, k) => {
    const inApp = path.relative("tauri-app", rel);
    const probe = path.join(app, inApp.replace(/\.tsx$/, `.__probe${k}.tsx`));
    const orig = readFileSync(path.join(tmp, rel), "utf8");
    writeFileSync(probe, probeModule(rel, orig, sites.map((s) => s.expr)));
    probes.push([rel, "./" + path.relative(path.join(app, "src"), probe).replace(/\.tsx$/, "")]);
  });
  const out = path.join(tmp, "probe-out.json");
  writeFileSync(path.join(app, "src", "__inline_probe.test.tsx"), `
import { test } from "vitest";
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const src = readFileSync(require.resolve("react-dom/package.json").replace(/package\.json$/, "cjs/react-dom-client.development.js"), "utf8");
const m = /unitlessNumbers = new Set\\(\\s*"([^"]+)"\\.split/.exec(src);
if (!m) throw new Error("react-dom 里找不到无单位属性表");
const unitless = new Set(m[1].split(" "));
const hyphen = (k: string) => k === "cssFloat" ? "float" : k.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase()).replace(/^ms-/, "-ms-");
// React 客户端 setValueForStyle 的取值规则（逐属性 CSSOM 赋值）
function clientDecls(obj: Record<string, unknown>) {
  const out: [string, string][] = [];
  for (const key in obj) {
    if (!Object.prototype.hasOwnProperty.call(obj, key)) continue;
    const v = obj[key];
    if (v == null || typeof v === "boolean" || v === "") continue;
    if (key.startsWith("--")) { out.push([key, String(v)]); continue; }
    const value = typeof v === "number" && v !== 0 && !unitless.has(key) ? v + "px" : ("" + v).trim();
    out.push([hyphen(key), value]);
  }
  return out;
}
test("inline probe", async () => {
  const result: Record<string, unknown> = {};
${probes.map(([rel, imp]) => `  result[${JSON.stringify(rel)}] = ((await import(${JSON.stringify(imp)})).__shProbe as Record<string, unknown>[]).map(clientDecls);`).join("\n")}
  writeFileSync(${JSON.stringify(out)}, JSON.stringify(result));
}, 120000);
`);
  if (probes.length) {
    const r = spawnSync(path.join(APP, "node_modules/.bin/vitest"), ["run", "src/__inline_probe.test.tsx"], { cwd: app, encoding: "utf8" });
    if (r.status !== 0) {
      console.log(r.stdout.slice(-4000), r.stderr.slice(-2000));
      throw new Error("原内联求值失败");
    }
    Object.assign(before, JSON.parse(readFileSync(out, "utf8")));
  }
} finally {
  rmSync(tmp, { recursive: true, force: true });
}

// ---- 生成一侧：postcss 读 .styles.css ----
function generated(rel) {
  const cssFile = rel.replace(/\.tsx$/, ".styles.css");
  const map = new Map();
  if (!existsSync(cssFile)) return map;
  postcss.parse(readFileSync(cssFile, "utf8")).walkRules((rule) => {
    const decls = [];
    rule.walkDecls((d) => {
      const m = /^var\((--sh-[a-z0-9-]+)\)$/.exec(d.value);
      const value = m && TOKEN_CSS_VARS[m[1]] !== undefined ? TOKEN_CSS_VARS[m[1]] : d.value;
      if (d.important) problems.push(`${cssFile}：${rule.selector} 有 !important`);
      decls.push([d.prop, value]);
    });
    for (const s of rule.selectors) map.set(s.trim().replace(/^\./, ""), decls);
  });
  return map;
}

function compareAll(genFor) {
  const found = [];
  let n = 0;
  for (const { rel, sites } of plan) {
    const gen = genFor(rel);
    const orig = before[rel];
    sites.forEach((s, i) => {
      n++;
      const a = orig[i];
      const b = s.cls ? gen.get(s.cls) : [];
      if (!b) { found.push(`${s.where}：生成类 ${s.cls} 不存在`); return; }
      for (const [, v] of b) if (DANGEROUS.test(v)) found.push(`${s.where}：生成值含危险字符 ${v}`);
      if (JSON.stringify(a) !== JSON.stringify(b)) {
        let k = 0;
        while (k < a.length && k < b.length && a[k][0] === b[k][0] && a[k][1] === b[k][1]) k++;
        found.push(`${s.where}：声明不同（第 ${k + 1} 条：原 ${JSON.stringify(a[k] ?? null)}；类 ${JSON.stringify(b[k] ?? null)}）`);
      }
    });
  }
  return { found, n };
}

const { found, n } = compareAll(generated);
problems.push(...found);
console.log(`文件 ${files.length} 个；被转元素 ${n} 处`);

if (canary) {
  const results = [];
  const tamper = (fn) => compareAll((rel) => {
    const g = generated(rel);
    const copy = new Map([...g].map(([k, v]) => [k, v.map((d) => [...d])]));
    fn(copy);
    return copy;
  }).found.length;
  let done1 = false;
  results.push(["改坏一个值", tamper((g) => { for (const d of g.values()) if (!done1 && d.length) { d[0][1] += "1"; done1 = true; } })]);
  let done2 = false;
  results.push(["改坏一处转义（引号写成 &quot;）", tamper((g) => {
    for (const d of g.values()) for (const x of d) if (!done2 && x[1].includes('"')) { x[1] = x[1].replace('"', "&quot;"); done2 = true; }
    if (!done2) for (const d of g.values()) if (!done2 && d.length) { d[d.length - 1][1] = `"${d[d.length - 1][1]}"`; done2 = true; }
  })]);
  for (const [name, c] of results) console.log(`金丝雀 ${name}：报出 ${c} 条`);
  if (results.some(([, c]) => c === 0)) problems.push("金丝雀没有报出：核对工具不合格");
}

problems.forEach((p) => console.log("  " + p));
console.log(problems.length ? "静态核对：不通过" : "静态核对：通过");
process.exit(problems.length ? 1 : 0);
