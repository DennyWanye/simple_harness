// 代码质量整治阶段 9：前端"只改类型"核对（阶段 9 裁决执行条件 7 的纯类型提交）。
// 用法：node scripts/code_quality/ts_emit_check.mjs [ref]   （在仓库根目录运行，默认 ref=HEAD）
// 取 ref 与工作区里改动的 tauri-app 下 .ts/.tsx 文件，两边都用 TypeScript 去掉类型（transpileModule，去注释），
// 逐文件按语法树比对产物 JS（不受换行、缩进、引号风格影响）。只改类型时产物必须逐字相同；新增文件只允许产物为空模块（纯类型文件）。
import { execFileSync } from "node:child_process";
import { readFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(new URL("../../tauri-app/package.json", import.meta.url));
const ts = require("typescript");
const ref = process.argv[2] || "HEAD";
const git = (args) => execFileSync("git", args, { encoding: "utf8" });
const files = [
  ...new Set(
    [
      ...git(["diff", "--name-only", ref, "--", "tauri-app/src", "tauri-app/tests"]).split("\n"),
      ...git(["ls-files", "--others", "--exclude-standard", "--", "tauri-app/src", "tauri-app/tests"]).split("\n"),
    ].filter((f) => /\.(ts|tsx)$/.test(f) && !f.endsWith(".d.ts")),
  ),
];
const options = {
  target: ts.ScriptTarget.ESNext,
  module: ts.ModuleKind.ESNext,
  jsx: ts.JsxEmit.Preserve,
  removeComments: true,
};
// 产物解析成语法树后序列化（节点种类 + 叶子文本，每项一行），与换行、缩进、引号风格无关。
// 字符串与模板片段比字面值；JSX 文本按 JSX 空白规则规整（逐行去首尾空白、丢空行、以空格连接）。
function jsxText(t) {
  return t
    .split(/\r?\n/)
    .map((l, i, all) => (i === 0 ? l.replace(/\s+$/, all.length > 1 ? "" : "$&") : l.trim()))
    .filter((l, i, all) => l !== "" || all.length === 1)
    .join(" ");
}
function emit(text, file) {
  const out = ts.transpileModule(text, { compilerOptions: options, fileName: file }).outputText;
  const sf = ts.createSourceFile(file.replace(/\.tsx?$/, ".jsx"), out, ts.ScriptTarget.Latest, true, ts.ScriptKind.JSX);
  const items = [];
  const walk = (node) => {
    // 括号不进序列：语法树本身已表达分组，去类型后留下的多余括号不改语义。
    if (ts.isParenthesizedExpression(node)) return walk(node.expression);
    // 简写属性 { a } 与 { a: a } 等价，统一成后者。
    if (ts.isShorthandPropertyAssignment(node) && !node.objectAssignmentInitializer) {
      const id = `Identifier ${node.name.text}`;
      items.push("PropertyAssignment", id, id);
      return;
    }
    const kind = ts.SyntaxKind[node.kind];
    if (ts.isJsxText(node)) {
      const v = node.containsOnlyTriviaWhiteSpaces ? "" : jsxText(node.text);
      if (v !== "") items.push(`${kind} ${JSON.stringify(v)}`);
      return;
    }
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isTemplateHead(node) ||
        ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) {
      items.push(`${kind} ${JSON.stringify(node.text)}`);
      return;
    }
    const kids = [];
    ts.forEachChild(node, (c) => { kids.push(c); });
    if (kids.length === 0) items.push(`${kind} ${node.getText(sf)}`);
    else { items.push(kind); kids.forEach(walk); }
  };
  walk(sf);
  return items.join("\n");
}
const EMPTY = new Set([emit("", "x.ts"), emit("export {};", "x.ts")]);

let bad = 0;
for (const f of files) {
  let old = null;
  try {
    old = execFileSync("git", ["show", `${ref}:${f}`], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] });
  } catch {
    old = null;
  }
  const now = existsSync(f) ? readFileSync(f, "utf8") : null;
  if (now === null) {
    if (!EMPTY.has(emit(old, f))) { console.log(`删除了有运行时代码的文件：${f}`); bad++; }
    continue;
  }
  const a = old === null ? "" : emit(old, f);
  const b = emit(now, f);
  if (old === null ? !EMPTY.has(b) : a !== b) {
    bad++;
    const al = a.split("\n"), bl = b.split("\n");
    let i = 0;
    while (i < al.length && al[i] === bl[i]) i++;
    console.log(`产物变了：${f}（第 ${i + 1} 行）\n  原：${al[i] ?? "<无>"}\n  现：${bl[i] ?? "<无>"}`);
  }
}
console.log(`核对文件 ${files.length} 个`);
console.log(bad === 0 ? "只改类型核对：通过" : "只改类型核对：不通过");
process.exit(bad === 0 ? 0 : 1);
