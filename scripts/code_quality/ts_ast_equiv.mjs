// 代码质量整治：前端格式化的语法树等价检查。
// 用法：node scripts/code_quality/ts_ast_equiv.mjs [ref]   （在仓库根目录运行，默认 ref=HEAD）
// 用 tauri-app 里装的 typescript 解析改动前后的文件，去掉空白、注释与多余括号后比较节点树。
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
const require = createRequire(new URL("../../tauri-app/package.json", import.meta.url));
const ts = require("typescript");
const ref = process.argv[2] || "HEAD";
const files = execFileSync("git", ["diff", "--name-only", "--diff-filter=M", ref, "--", "tauri-app/src"], { encoding: "utf8" })
  .split("\n").filter((f) => /\.(ts|tsx)$/.test(f));
function jsxTextValue(raw) {
  // React/Babel 规则：按行切分，首行去尾空白、末行去首空白、中间行两头去空白，丢掉空行，用一个空格连接
  const lines = raw.split(/\r\n|\n|\r/);
  const out = [];
  lines.forEach((line, i) => {
    let l = line.replace(/\t/g, " ");
    if (i !== 0) l = l.replace(/^ +/, "");
    if (i !== lines.length - 1) l = l.replace(/ +$/, "");
    if (l) out.push(l);
  });
  return out.join(" ");
}
function norm(node, sf) {
  while (ts.isParenthesizedExpression(node) || ts.isParenthesizedTypeNode?.(node)) node = node.expression ?? node.type;
  const kind = ts.SyntaxKind[node.kind];
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return `${kind}(${JSON.stringify(node.text)})`;
  if (ts.isNumericLiteral(node) || ts.isIdentifier(node) || ts.isPrivateIdentifier(node)) return `${kind}(${node.text})`;
  if (ts.isJsxElement(node) || ts.isJsxFragment(node)) {
    // JSX 子节点：按 React 的空白规则求文本语义，{" "} 视为一个空格，相邻文本拼接后比较
    const open = ts.isJsxElement(node) ? norm(node.openingElement, sf) : "Frag";
    const parts = []; let text = null;
    const flush = () => { if (text !== null && text !== "") parts.push(`T(${JSON.stringify(text)})`); text = null; };
    for (const c of node.children) {
      let t = null;
      if (ts.isJsxText(c)) t = jsxTextValue(c.text);
      else if (ts.isJsxExpression(c) && c.expression && ts.isStringLiteral(c.expression)) t = c.expression.text;
      if (t !== null) { text = (text ?? "") + t; continue; }
      if (ts.isJsxExpression(c) && !c.expression) continue;
      flush(); parts.push(norm(c, sf));
    }
    flush();
    return `${kind}[${open},${parts.join(",")}]`;
  }
  if (ts.isJsxText(node)) { const t = jsxTextValue(node.text); return t ? `T(${JSON.stringify(t)})` : ""; }
  if (ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) return `${kind}(${JSON.stringify(node.text)})`;
  const kids = [];
  node.forEachChild((c) => { const s = norm(c, sf); if (s) kids.push(s); });
  return `${kind}[${kids.join(",")}]`;
}
let same = 0; const diff = [];
for (const f of files) {
  const kindOf = f.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const oldSrc = execFileSync("git", ["show", `${ref}:${f}`], { encoding: "utf8", maxBuffer: 64 << 20 });
  const newSrc = readFileSync(f, "utf8");
  const a = norm(ts.createSourceFile(f, oldSrc, ts.ScriptTarget.Latest, true, kindOf));
  const b = norm(ts.createSourceFile(f, newSrc, ts.ScriptTarget.Latest, true, kindOf));
  if (a === b) same++; else diff.push(f);
}
for (const f of diff) console.log(`不等价：${f}`);
console.log(`TS 语法树等价：${same} 个文件等价，${diff.length} 个需人工确认（共改动 ${files.length} 个）`);
process.exit(diff.length ? 1 : 0);
