// 代码质量整治阶段 9：把前端模块里的若干顶层定义原样搬到同目录另一个模块，全 src 导入方改到新位置。
// 用法：node scripts/code_quality/ts_move_defs.mjs <源文件> <目标文件> 名字1,名字2,... [--dry-run]
//   文件路径相对仓库根（如 tauri-app/src/components/SettingsPanel.tsx）；目标不存在就新建，必须与源同目录。
// 规则（与 Python 版 move_defs.py 同口径）：
// - 搬走的定义用到源模块里留下的定义 → 拒绝（成环或要转出），请把依赖一并列上。
// - 搬走的定义用到的源模块导入，按原样复制到目标（只留用到的名字；类型导入保持 import type）。
// - 源模块其余部分还用到搬走的名字 → 源模块从目标导入（类型用 import type）；源模块因此不再用到的导入删掉。
// - 全 src 里 `import { 名字 } from "<源>"` 改成从目标导入；`export { 名字 } from "<源>"` 同样改写。
// - `vi.mock("<源>")` 之类按路径打桩的文件若提到搬走的名字，列出来并拒绝（打桩落点会变）。
// 搬后用 ts_move_check.mjs 核对定义逐字相同，再跑 prettier。
import { readFileSync, writeFileSync, existsSync, readdirSync, statSync } from "node:fs";
import { dirname, relative, resolve, basename, join } from "node:path";
import { createRequire } from "node:module";

const require = createRequire(new URL("../../tauri-app/package.json", import.meta.url));
const ts = require("typescript");
const [srcArg, dstArg, namesArg, ...rest] = process.argv.slice(2);
const dry = rest.includes("--dry-run");
if (!srcArg || !dstArg || !namesArg) { console.error("用法见文件头"); process.exit(2); }
const REPO = resolve(new URL("../..", import.meta.url).pathname);
const SRC = resolve(REPO, srcArg), DST = resolve(REPO, dstArg);
if (dirname(SRC) !== dirname(DST)) { console.error("目标必须与源同目录"); process.exit(2); }
const names = new Set(namesArg.split(",").map((s) => s.trim()).filter(Boolean));
const parse = (file, text) => ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true,
  file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);

const srcText = readFileSync(SRC, "utf8");
const sf = parse(SRC, srcText);

function bound(st) {
  if ((ts.isFunctionDeclaration(st) || ts.isClassDeclaration(st) || ts.isInterfaceDeclaration(st) ||
       ts.isTypeAliasDeclaration(st) || ts.isEnumDeclaration(st)) && st.name) return [st.name.text];
  if (ts.isVariableStatement(st)) {
    const out = [];
    for (const d of st.declarationList.declarations) {
      const walk = (n) => { if (ts.isIdentifier(n)) out.push(n.text); else n.forEachChild(walk); };
      walk(d.name);
    }
    return out;
  }
  return [];
}
const isTypeDecl = (st) => ts.isInterfaceDeclaration(st) || ts.isTypeAliasDeclaration(st);

// 引用的标识符（去掉属性名、成员名、JSX 属性名等非引用位置）
function refs(node) {
  const out = new Set();
  const visit = (n) => {
    if (ts.isIdentifier(n)) {
      const p = n.parent;
      const skip =
        (ts.isPropertyAccessExpression(p) && p.name === n) ||
        ((ts.isPropertyAssignment(p) || ts.isMethodDeclaration(p) || ts.isPropertyDeclaration(p) ||
          ts.isPropertySignature(p) || ts.isMethodSignature(p) || ts.isGetAccessor(p) ||
          ts.isSetAccessor(p) || ts.isEnumMember(p)) && p.name === n) ||
        (ts.isBindingElement(p) && p.propertyName === n) ||
        (ts.isJsxAttribute(p) && p.name === n) ||
        (ts.isQualifiedName(p) && p.right === n) ||
        // `export { X } from "mod"` 转出别的模块，不引用本模块的 X
        (ts.isExportSpecifier(p) && p.parent.parent.moduleSpecifier !== undefined) ||
        (ts.isLabeledStatement(p) && p.label === n);
      if (!skip) out.add(n.text);
    }
    n.forEachChild(visit);
  };
  visit(node);
  return out;
}

const moved = [], kept = [], imports = [];
for (const st of sf.statements) {
  if (ts.isImportDeclaration(st)) { imports.push(st); continue; }
  const b = bound(st);
  if (b.some((x) => names.has(x))) {
    if (!b.every((x) => names.has(x))) { console.error(`语句还绑定了 ${b.filter((x) => !names.has(x))}，一并列上`); process.exit(1); }
    moved.push(st);
  } else kept.push(st);
}
const found = new Set(moved.flatMap(bound));
const missing = [...names].filter((n) => !found.has(n));
if (missing.length) { console.error(`源模块顶层找不到：${missing}`); process.exit(1); }

const keptDefined = new Set(kept.flatMap(bound));
const movedUses = new Set(); moved.forEach((st) => refs(st).forEach((x) => movedUses.add(x)));
const keptUses = new Set(); kept.forEach((st) => refs(st).forEach((x) => keptUses.add(x)));
const needRest = [...movedUses].filter((x) => keptDefined.has(x) && !names.has(x));
if (needRest.length) { console.error(`搬走的定义用到留下的定义：${needRest}，一并列上或换一组`); process.exit(1); }
const restUsesMoved = [...names].filter((x) => keptUses.has(x));
const typeNames = new Set(moved.filter(isTypeDecl).flatMap(bound));

// 导入名字 → 是否被搬走代码 / 留下代码用到
function importParts(st) {
  const c = st.importClause;
  const out = [];
  if (!c) return out;
  if (c.name) out.push({ local: c.name.text, kind: "default" });
  if (c.namedBindings) {
    if (ts.isNamespaceImport(c.namedBindings)) out.push({ local: c.namedBindings.name.text, kind: "ns" });
    else for (const e of c.namedBindings.elements)
      out.push({ local: e.name.text, imported: (e.propertyName ?? e.name).text, kind: "named", typeOnly: e.isTypeOnly });
  }
  return out;
}
function renderImport(st, parts) {
  const spec = st.moduleSpecifier.getText(sf);
  const c = st.importClause;
  const typeKw = c.isTypeOnly ? "type " : "";
  const def = parts.find((p) => p.kind === "default");
  const ns = parts.find((p) => p.kind === "ns");
  const named = parts.filter((p) => p.kind === "named");
  const segs = [];
  if (def) segs.push(def.local);
  if (ns) segs.push(`* as ${ns.local}`);
  if (named.length) segs.push(`{ ${named.map((p) => `${p.typeOnly ? "type " : ""}${p.imported !== p.local ? `${p.imported} as ` : ""}${p.local}`).join(", ")} }`);
  return `import ${typeKw}${segs.join(", ")} from ${spec};`;
}
const dstImports = [], srcImports = [];
for (const st of imports) {
  const parts = importParts(st);
  if (!st.importClause) { srcImports.push(st.getFullText(sf).trimStart()); continue; }  // 副作用导入留在源
  const forDst = parts.filter((p) => movedUses.has(p.local));
  const forSrc = parts.filter((p) => keptUses.has(p.local));
  if (forDst.length) dstImports.push(renderImport(st, forDst));
  // 整条保留的导入连同它上方的注释一起留下（第一条的前导注释已在 header 里）
  const whole = st === imports[0] ? st.getText(sf) : srcText.slice(st.getFullStart(), st.getEnd()).replace(/^\n+/, "");
  if (forSrc.length) srcImports.push(forSrc.length === parts.length ? whole : renderImport(st, forSrc));
}
const dstSpec = "./" + basename(DST).replace(/\.(tsx?)$/, "");
if (restUsesMoved.length) {
  const vals = restUsesMoved.filter((n) => !typeNames.has(n));
  const types = restUsesMoved.filter((n) => typeNames.has(n));
  if (vals.length) srcImports.push(`import { ${vals.join(", ")} } from "${dstSpec}";`);
  if (types.length) srcImports.push(`import type { ${types.join(", ")} } from "${dstSpec}";`);
}

// 语句全文（含前导注释）
const text = (st) => srcText.slice(st.getFullStart(), st.getEnd()).replace(/^\s*\n/, "");
const header = srcText.slice(0, imports.length ? imports[0].getStart(sf) : (kept[0] ?? moved[0]).getStart(sf));
// 许可证头（SPDX 注释）原样带到新文件
const license = (header.match(/^(?:\/\/ SPDX[^\n]*\n)+/) ?? [""])[0];
const exportize = (s, st) => {
  const mods = ts.canHaveModifiers(st) ? ts.getModifiers(st) ?? [] : [];
  if (mods.some((m) => m.kind === ts.SyntaxKind.ExportKeyword)) return s;
  // 搬到新模块后要被源模块或导入方引用 → 加 export（ts_move_check 去掉 export 修饰后比较）
  const lead = s.match(/^(\s*(?:\/\/[^\n]*\n|\/\*[\s\S]*?\*\/\s*)*)/)[0];
  return lead + "export " + s.slice(lead.length);
};
const movedBody = moved.map((st) => exportize(text(st).trim(), st)).join("\n\n");
const existing = existsSync(DST) ? readFileSync(DST, "utf8") : "";
const newDst = existing
  ? existing.trimEnd() + "\n\n" + movedBody + "\n"
  : (license ? license + "\n" : "") + (dstImports.length ? dstImports.join("\n") + "\n\n" : "") + movedBody + "\n";
if (existing && dstImports.length) console.error("注意：目标已存在，导入请手工合并：\n" + dstImports.join("\n"));
const keptBody = kept.map((st) => text(st)).join("\n");
const newSrc = header + srcImports.join("\n") + "\n\n" + keptBody.replace(/^\n+/, "") + "\n";

// 全 src 导入方
const TREE = resolve(REPO, "tauri-app");
const files = [];
const walkDir = (d) => { for (const e of readdirSync(d)) { const p = join(d, e);
  if (e === "node_modules" || e.startsWith(".")) continue;
  if (statSync(p).isDirectory()) walkDir(p); else if (/\.(ts|tsx)$/.test(e)) files.push(p); } };
walkDir(join(TREE, "src")); if (existsSync(join(TREE, "tests"))) walkDir(join(TREE, "tests"));
const resolveSpec = (from, spec) => {
  if (!spec.startsWith(".")) return null;
  const base = resolve(dirname(from), spec);
  for (const c of [base, base + ".ts", base + ".tsx", join(base, "index.ts"), join(base, "index.tsx")]) if (c === SRC) return SRC;
  return null;
};
const edits = [], problems = [];
for (const f of files) {
  if (f === SRC || f === DST) continue;
  const t = readFileSync(f, "utf8");
  if (!t.includes(basename(SRC).replace(/\.(tsx?)$/, ""))) continue;
  const s2 = parse(f, t);
  const repl = [];
  const visit = (n) => {
    if ((ts.isImportDeclaration(n) || ts.isExportDeclaration(n)) && n.moduleSpecifier && ts.isStringLiteral(n.moduleSpecifier)
        && resolveSpec(f, n.moduleSpecifier.text) === SRC) {
      const els = ts.isImportDeclaration(n)
        ? (n.importClause?.namedBindings && ts.isNamedImports(n.importClause.namedBindings) ? n.importClause.namedBindings.elements : [])
        : (n.exportClause && ts.isNamedExports(n.exportClause) ? n.exportClause.elements : []);
      const hit = els.filter((e) => names.has((e.propertyName ?? e.name).text));
      if (ts.isImportDeclaration(n) && n.importClause?.namedBindings && ts.isNamespaceImport(n.importClause.namedBindings))
        problems.push(`${relative(REPO, f)}: 命名空间导入源模块，请手工处理`);
      if (hit.length) {
        const keep = els.filter((e) => !hit.includes(e));
        const spec = n.moduleSpecifier.text;
        const newSpec = spec.replace(/[^/]+$/, basename(DST).replace(/\.(tsx?)$/, ""));
        const kw = ts.isImportDeclaration(n) ? "import" : "export";
        const tOnly = ts.isImportDeclaration(n) ? n.importClause.isTypeOnly : n.isTypeOnly;
        const fmt = (e) => e.getText(s2);
        const def = ts.isImportDeclaration(n) && n.importClause.name ? n.importClause.name.text : null;
        const parts = [];
        if (keep.length || def) parts.push(`${kw} ${tOnly ? "type " : ""}${def ? def + (keep.length ? ", " : "") : ""}${keep.length ? `{ ${keep.map(fmt).join(", ")} }` : ""} from "${spec}";`);
        parts.push(`${kw} ${tOnly ? "type " : ""}{ ${hit.map(fmt).join(", ")} } from "${newSpec}";`);
        repl.push([n.getStart(s2), n.getEnd(), parts.join("\n")]);
      }
    }
    // 按路径打桩或动态导入源模块：只有该文件提到了搬走的名字才拒绝（落点会变）；没提到就与搬移无关。
    if (ts.isCallExpression(n) && n.arguments.length && ts.isStringLiteral(n.arguments[0])
        && resolveSpec(f, n.arguments[0].text) === SRC && /mock|import/i.test(n.expression.getText(s2))
        && [...names].some((x) => new RegExp(`\\b${x}\\b`).test(t)))
      problems.push(`${relative(REPO, f)}:${s2.getLineAndCharacterOfPosition(n.getStart(s2)).line + 1} 按路径打桩或动态导入源模块，且提到了搬走的名字`);
    n.forEachChild(visit);
  };
  visit(s2);
  if (repl.length) edits.push([f, t, repl]);
}
if (problems.length) { console.error("需要先处理：\n  " + problems.join("\n  ")); process.exit(1); }
console.log(`搬 ${names.size} 个名字；目标导入 ${dstImports.length} 条；源模块回导入 ${restUsesMoved.length} 个；改写导入方 ${edits.length} 个文件`);
if (dry) { edits.forEach(([f]) => console.log("  " + relative(REPO, f))); process.exit(0); }
writeFileSync(DST, newDst);
writeFileSync(SRC, newSrc);
for (const [f, t, repl] of edits) {
  let out = t;
  for (const [a, b, s] of repl.sort((x, y) => y[0] - x[0])) out = out.slice(0, a) + s + out.slice(b);
  writeFileSync(f, out);
}
console.log("完成。接着跑：node scripts/code_quality/ts_move_check.mjs、prettier、tsc -b --noEmit");
