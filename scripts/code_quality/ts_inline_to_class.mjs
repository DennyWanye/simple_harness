// 代码质量整治阶段 9（9I）：把可证明静态的内联样式换成生成的 CSS 类。
// 用法（在仓库根目录运行）：
//   node scripts/code_quality/ts_inline_to_class.mjs census                 盘点：每处 style 的去向与指标算术
//   node scripts/code_quality/ts_inline_to_class.mjs convert <a.tsx> ...    转换指定文件
// 规则（阶段9-9I-设计.md 与其裁决的 8 条执行条件）：
//   - 只转小写标签（DOM/SVG 元素）上的 style；组件元素不动。
//   - 整处可证明静态才转（ts_static_eval.mjs 求值成功，且表达式里的名字都解析到模块顶层声明）；
//     有运行时值的整处不动，不拆一半。
//   - 元素带展开属性、ref、或任一属性（含指向本文件函数的事件处理）里出现 `.style` 的，不转（条件 5）。
//   - className 只拼字符串字面量；表达式 className 的元素不转。
//   - 值含 ! ; { } /* \ < 或键名为 cssFloat、含连字符、以 -- 开头的，整处不转（条件 4）。
//   - 声明文本由 react-dom/server 逐条生成并解码 HTML 实体；值直接来自令牌叶子且与令牌变量值相等时
//     换成 var(--sh-…)（条件 6）。
//   - 每个源文件生成同目录 <名>.styles.css，类名 sh-<名>-<序号>；同一文件里声明相同的共用一个类。
import { readFileSync, writeFileSync, readdirSync, statSync, existsSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";
import { Evaluator, NotStatic, Tok, un } from "./ts_static_eval.mjs";

const APP = path.resolve("tauri-app");
const require = createRequire(path.join(APP, "package.json"));
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

const { TOKEN_CSS_VARS } = await import(pathToFileURL(path.join(APP, "src/theme/tokens.ts")).href);

export const DANGEROUS = /[!;{}\\<]|\/\*/;

export function srcFiles() {
  const out = [];
  const walk = (d) => {
    for (const e of readdirSync(d)) {
      const p = path.join(d, e);
      if (statSync(p).isDirectory()) walk(p);
      else if (p.endsWith(".tsx") && !/\.test\.tsx$/.test(p) && !p.includes("__tests__")) out.push(p);
    }
  };
  walk(path.join(APP, "src"));
  return out.sort();
}

function program() {
  const cfgPath = path.join(APP, "tsconfig.app.json");
  const cfg = ts.getParsedCommandLineOfConfigFile(cfgPath, {}, { ...ts.sys, onUnRecoverableConfigFileDiagnostic: () => {} });
  return ts.createProgram({ rootNames: cfg.fileNames, options: cfg.options });
}

const decode = (s) =>
  s.replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");

/** 一条样式声明的 CSS 文本，由 react-dom/server 生成（属性名转换、补 px、去空白都按 React）。 */
function declText(key, value) {
  const html = renderToStaticMarkup(React.createElement("div", { style: { [key]: value } }));
  const m = /^<div style="([^"]*)"><\/div>$/.exec(html);
  if (!m) return null; // 空值：React 不输出
  const text = decode(m[1]);
  const i = text.indexOf(":");
  return { prop: text.slice(0, i), value: text.slice(i + 1) };
}

/** 求值得到的样式对象 → 声明表；不可转时抛出原因。 */
export function declarations(obj) {
  if (obj == null || typeof obj !== "object" || Array.isArray(obj)) throw new NotStatic("值不是对象");
  const out = [];
  for (const key of Object.keys(obj)) {
    if (key === "cssFloat" || key.includes("-")) throw new NotStatic(`键名 ${key}`);
    const raw = obj[key];
    const v = un(raw);
    if (v !== null && v !== undefined && typeof v === "object") throw new NotStatic(`键 ${key} 的值是对象`);
    if (typeof v === "function") throw new NotStatic(`键 ${key} 的值是函数`);
    if (typeof v === "string" && DANGEROUS.test(v)) throw new NotStatic(`键 ${key} 的值含危险字符`);
    const d = declText(key, v);
    if (!d) continue;
    if (DANGEROUS.test(d.value)) throw new NotStatic(`键 ${key} 的值含危险字符`);
    if (raw instanceof Tok && TOKEN_CSS_VARS[raw.varName] === d.value) d.value = `var(${raw.varName})`;
    out.push(d);
  }
  return out;
}

function isTopLevelDecl(d) {
  if (!d) return false;
  if (ts.isImportSpecifier(d) || ts.isImportClause(d) || ts.isNamespaceImport(d)) return true;
  if (ts.isFunctionDeclaration(d)) return ts.isSourceFile(d.parent);
  let n = d;
  while (n && (ts.isBindingElement(n) || ts.isObjectBindingPattern(n) || ts.isArrayBindingPattern(n))) n = n.parent;
  if (n && ts.isVariableDeclaration(n)) return ts.isSourceFile(n.parent?.parent?.parent);
  return false;
}

/** 表达式里引用的名字是否都是模块顶层声明（局部名字即运行时值）。 */
function freeNamesTopLevel(expr, checker) {
  let bad = null;
  const visit = (n) => {
    if (bad) return;
    if (ts.isTypeNode(n)) return;
    if (ts.isIdentifier(n)) {
      const p = n.parent;
      if ((ts.isPropertyAccessExpression(p) && p.name === n) || (ts.isPropertyAssignment(p) && p.name === n)) return;
      if (n.text === "undefined") return;
      const sym = ts.isShorthandPropertyAssignment(p) ? checker.getShorthandAssignmentValueSymbol(p) : checker.getSymbolAtLocation(n);
      const d = sym?.declarations?.[0];
      if (!isTopLevelDecl(d)) bad = n.text;
      return;
    }
    n.forEachChild(visit);
  };
  visit(expr);
  return bad;
}

function mentionsStyleWrite(node, sf, checker, seen = new Set()) {
  let hit = false;
  const visit = (n) => {
    if (hit) return;
    if (ts.isPropertyAccessExpression(n) && n.name.text === "style") { hit = true; return; }
    if (ts.isIdentifier(n) && !seen.has(n.text)) {
      const d = checker.getSymbolAtLocation(n)?.declarations?.[0];
      if (d && d.getSourceFile() === sf && (ts.isFunctionDeclaration(d) || (ts.isVariableDeclaration(d) && d.initializer &&
          (ts.isArrowFunction(d.initializer) || ts.isFunctionExpression(d.initializer) || ts.isCallExpression(d.initializer))))) {
        seen.add(n.text);
        if (mentionsStyleWrite(d, sf, checker, seen)) { hit = true; return; }
      }
    }
    n.forEachChild(visit);
  };
  visit(node);
  return hit;
}

/**
 * 已有 className 的拼接方式：
 *   literal  字符串字面量（含 {"…"} 与无替换模板），直接在末尾加类名；
 *   template 模板字面量，在模板末尾加类名（模板求值恒为字符串）；
 *   static   表达式可静态求出非空字符串（如 INTERACTIVE_CLASS），包成 `${表达式} 类名`。
 * 其余（可能是 undefined 的运行时表达式）不转。
 */
function classMode(a, sf, checker, ev, file) {
  const init = a.initializer;
  if (!init) throw Object.assign(new Error("空 className"), { cat: "排除" });
  if (ts.isStringLiteral(init)) return { kind: "literal", node: init };
  const e = init.expression;
  if (e && (ts.isStringLiteral(e) || ts.isNoSubstitutionTemplateLiteral(e))) return { kind: "literal", node: e };
  if (e && ts.isTemplateExpression(e)) return { kind: "template", node: e };
  if (e && !freeNamesTopLevel(e, checker)) {
    try {
      const v = un(ev.eval(e, EMPTY_ENV, ev.module(file)));
      if (typeof v === "string" && v.trim()) return { kind: "static", node: e };
    } catch (err) {
      if (!(err instanceof NotStatic)) throw err;
    }
  }
  throw Object.assign(new Error("表达式 className"), { cat: "排除" });
}

const EMPTY_ENV = { lookup: () => ({ found: false }) };

/** 一个文件里每处 style 属性的去向。 */
export function analyzeFile(file, checker, ev, sfOverride) {
  const sf = sfOverride ?? ev.module(file).sf;
  const sites = [];
  const visit = (n) => {
    if (ts.isJsxAttribute(n) && n.name.getText(sf) === "style" && n.initializer && ts.isJsxExpression(n.initializer) && n.initializer.expression) {
      const attrs = n.parent;
      const el = attrs.parent;
      const tag = el.tagName.getText(sf);
      const site = { file, attr: n, el, tag, line: sf.getLineAndCharacterOfPosition(n.getStart()).line + 1, expr: n.initializer.expression };
      sites.push(site);
      const literal = ts.isObjectLiteralExpression(site.expr);
      site.form = literal ? "对象字面量" : ts.isIdentifier(site.expr) ? "标识符" : ts.isCallExpression(site.expr) ? "函数调用" : "其他";
      try {
        if (!/^[a-z]/.test(tag) || tag.includes(".")) throw Object.assign(new Error("组件元素"), { cat: "组件元素" });
        for (const a of attrs.properties) {
          if (ts.isJsxSpreadAttribute(a)) throw Object.assign(new Error("带展开属性"), { cat: "排除" });
          const name = a.name.getText(sf);
          if (name === "ref") throw Object.assign(new Error("带 ref"), { cat: "排除" });
          if (a !== n && a.initializer && mentionsStyleWrite(a.initializer, sf, checker))
            throw Object.assign(new Error(`属性 ${name} 里写 .style`), { cat: "排除" });
          if (name === "className") site.classMode = classMode(a, sf, checker, ev, file);
        }
        const bad = freeNamesTopLevel(site.expr, checker);
        if (bad) throw Object.assign(new Error(`运行时值 ${bad}`), { cat: "运行时值" });
        let value;
        try {
          value = ev.eval(site.expr, EMPTY_ENV, ev.module(file));
        } catch (e) {
          if (e instanceof NotStatic) throw Object.assign(new Error(`求值不了：${e.message}`), { cat: "运行时值" });
          throw e;
        }
        try {
          site.decls = declarations(value);
        } catch (e) {
          if (e instanceof NotStatic) throw Object.assign(new Error(e.message), { cat: "危险值" });
          throw e;
        }
        site.cat = "转换";
      } catch (e) {
        if (!e.cat) throw e;
        site.cat = e.cat;
        site.reason = e.message;
      }
    }
    n.forEachChild(visit);
  };
  visit(sf);
  return sites;
}

function metricCount(text) {
  return (text.match(/style=\{\{/g) ?? []).length + (text.match(/style=\{(?!\{)/g) ?? []).length;
}

function census() {
  const prog = program();
  const checker = prog.getTypeChecker();
  const ev = new Evaluator(APP);
  const byCat = new Map();
  const reasons = new Map();
  let metric = 0;
  const perFile = [];
  for (const f of srcFiles()) {
    const sf = prog.getSourceFile(f);
    metric += metricCount(sf.getFullText());
    const sites = analyzeFile(f, checker, ev, sf);
    let conv = 0;
    for (const s of sites) {
      const k = `${s.cat}｜${s.form}`;
      byCat.set(k, (byCat.get(k) ?? 0) + 1);
      if (s.cat !== "转换") {
        const r = `${s.cat}：${process.env.DETAIL ? s.reason : s.reason.replace(/运行时值 .*/, "运行时值").replace(/未知名字 .*/, "未知名字")}`;
        reasons.set(r, (reasons.get(r) ?? 0) + 1);
      } else conv++;
      if (s.cat === "排除" || s.cat === "危险值") console.log(`  ${s.cat} ${path.relative(APP, f)}:${s.line} ${s.reason}`);
    }
    perFile.push([conv, sites.length, path.relative(APP, f)]);
  }
  // 测试文件里的 style 也进指标
  const walkTests = (d) => {
    for (const e of readdirSync(d)) {
      const p = path.join(d, e);
      if (statSync(p).isDirectory()) walkTests(p);
      else if (/\.(ts|tsx)$/.test(p) && !srcFiles().includes(p)) metric += metricCount(readFileSync(p, "utf8"));
    }
  };
  walkTests(path.join(APP, "src"));
  console.log("\n去向（类别｜写法）：");
  for (const [k, v] of [...byCat].sort()) console.log(`  ${k}: ${v}`);
  console.log("\n不转的原因：");
  for (const [k, v] of [...reasons].sort((a, b) => b[1] - a[1]).slice(0, 40)) console.log(`  ${v}  ${k}`);
  const conv = [...byCat].filter(([k]) => k.startsWith("转换")).reduce((s, [, v]) => s + v, 0);
  console.log(`\n指标（style={{ 加 style={变量}，含测试）：现 ${metric}；可转 ${conv}；转后 ${metric - conv}`);
  console.log("\n按文件（可转/总数）：");
  for (const [c, t, f] of perFile.filter(([, t]) => t).sort((a, b) => b[1] - a[1])) console.log(`  ${c}/${t} ${f}`);
}

/** 生成的 CSS 文件内容。 */
function cssText(rel, classes) {
  const rules = [...classes].map(([key, name]) => {
    const decls = JSON.parse(key);
    return `.${name} {\n${decls.map((d) => `  ${d.prop}: ${d.value};`).join("\n")}\n}`;
  });
  return `/* 生成文件，不要手改：${rel} 里静态内联样式的等价类（9I）。\n   重新生成：node scripts/code_quality/ts_inline_to_class.mjs convert ${rel} */\n\n${rules.join("\n\n")}\n`;
}

function convertFile(file, prog, ev) {
  const checker = prog.getTypeChecker();
  const rel = path.relative(APP, file);
  const base = path.basename(file, ".tsx");
  const sf = prog.getSourceFile(file);
  const text = sf.getFullText();
  const sites = analyzeFile(file, checker, ev, sf).filter((s) => s.cat === "转换");
  if (!sites.length) return { rel, converted: 0 };
  const classes = new Map();
  const edits = [];
  for (const s of sites) {
    let cls = null;
    if (s.decls.length) {
      const key = JSON.stringify(s.decls);
      if (!classes.has(key)) classes.set(key, `sh-${base}-${classes.size + 1}`);
      cls = classes.get(key);
    }
    const removeStyle = () => edits.push([s.attr.getFullStart(), s.attr.getEnd(), ""]);
    if (!cls) { removeStyle(); continue; }
    const mode = s.classMode;
    if (!mode) { edits.push([s.attr.getStart(), s.attr.getEnd(), `className="${cls}"`]); continue; }
    removeStyle();
    const node = mode.node;
    if (mode.kind === "literal") {
      const q = node.getText(sf)[0];
      const inner = node.text ? `${node.text} ${cls}` : cls;
      if (inner.includes(q)) throw new Error(`${rel}: className 字面量含引号`);
      edits.push([node.getStart(), node.getEnd(), `${q}${inner}${q}`]);
    } else if (mode.kind === "template") {
      edits.push([node.getEnd() - 1, node.getEnd() - 1, ` ${cls}`]);
    } else {
      edits.push([node.getStart(), node.getEnd(), `\`\${${node.getText(sf)}} ${cls}\``]);
    }
  }
  const imports = sf.statements.filter((st) => ts.isImportDeclaration(st));
  const at = imports.length ? imports[imports.length - 1].getEnd() : 0;
  if (classes.size) edits.push([at, at, `\nimport "./${base}.styles.css";`]);
  let out = text;
  for (const [a, b, t] of edits.sort((x, y) => y[0] - x[0] || y[1] - x[1])) out = out.slice(0, a) + t + out.slice(b);
  writeFileSync(file, out);
  if (classes.size) writeFileSync(path.join(path.dirname(file), `${base}.styles.css`), cssText(rel, classes));
  return { rel, converted: sites.length, classes: classes.size };
}

/** 删掉转换后不再使用的本文件声明与导入（tsc 原本零未用告警，新出现的都来自本次转换）。 */
function dropUnused(files) {
  const cfg = ts.getParsedCommandLineOfConfigFile(path.join(APP, "tsconfig.app.json"), {}, { ...ts.sys, onUnRecoverableConfigFileDiagnostic: () => {} });
  const versions = new Map();
  const host = {
    getScriptFileNames: () => cfg.fileNames,
    getScriptVersion: (f) => String(versions.get(f) ?? 0),
    getScriptSnapshot: (f) => (ts.sys.fileExists(f) ? ts.ScriptSnapshot.fromString(ts.sys.readFile(f)) : undefined),
    getCurrentDirectory: () => APP,
    getCompilationSettings: () => cfg.options,
    getDefaultLibFileName: (o) => ts.getDefaultLibFilePath(o),
    fileExists: ts.sys.fileExists, readFile: ts.sys.readFile, readDirectory: ts.sys.readDirectory,
    directoryExists: ts.sys.directoryExists, getDirectories: ts.sys.getDirectories,
  };
  const ls = ts.createLanguageService(host, ts.createDocumentRegistry());
  const removed = [];
  for (const f of files) {
    for (let round = 0; round < 6; round++) {
      let changed = false;
      for (const fixId of ["unusedIdentifier_delete", "unusedIdentifier_deleteImports"]) {
        const fix = ls.getCombinedCodeFix({ type: "file", fileName: f }, fixId, {}, {});
        for (const c of fix.changes) {
          if (c.fileName !== f) throw new Error(`清理改到了别的文件 ${c.fileName}`);
          let t = readFileSync(f, "utf8");
          for (const tc of [...c.textChanges].sort((a, b) => b.span.start - a.span.start)) {
            removed.push(`${path.relative(APP, f)}: ${t.slice(tc.span.start, tc.span.start + tc.span.length).trim().split("\n")[0].slice(0, 90)}`);
            t = t.slice(0, tc.span.start) + tc.newText + t.slice(tc.span.start + tc.span.length);
          }
          writeFileSync(f, t);
          versions.set(f, (versions.get(f) ?? 0) + 1);
          changed = true;
        }
      }
      if (!changed) break;
    }
  }
  return removed;
}

function convert(files) {
  const prog = program();
  const ev = new Evaluator(APP);
  const bases = new Map();
  for (const f of srcFiles()) {
    const b = path.basename(f, ".tsx");
    if (bases.has(b)) bases.set(b, "重名");
    else bases.set(b, f);
  }
  const abs = files.map((f) => path.resolve(f));
  for (const f of abs) if (bases.get(path.basename(f, ".tsx")) === "重名") throw new Error(`文件简名重名，类名前缀不唯一：${f}`);
  const results = abs.map((f) => convertFile(f, prog, ev));
  const removed = dropUnused(abs.filter((_, i) => results[i].converted));
  for (const r of results) console.log(`  ${r.rel}: 转换 ${r.converted} 处，生成类 ${r.classes ?? 0} 个`);
  if (removed.length) console.log("清理掉的未用声明与导入：\n" + removed.map((x) => "  " + x).join("\n"));
}

const [cmd, ...rest] = process.argv.slice(2);
if (cmd === "census") census();
else if (cmd === "convert") convert(rest);
else if (cmd) { console.error(`未知命令 ${cmd}`); process.exit(2); }
