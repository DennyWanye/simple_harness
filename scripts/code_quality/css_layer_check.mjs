// 代码质量整治阶段 9（9I）：层叠层检查（裁决条件 2）。
// 用法（在仓库根目录运行）：node scripts/code_quality/css_layer_check.mjs <产物目录>
//   产物目录：tauri-app 的 vite build 输出（含 index.html 与 assets/*.css）。
// 检查：
//   1. 产物 CSS 的每条顶层规则：要么在 @layer sh-base 里，要么是选择器全为生成类 .sh-<文件简名>-<序号> 的规则。
//   2. 产物 index.html 没有 <style>。
//   3. 源码 JSX 里没有 <style> 元素（运行时 <style> 只许 applyTheme 注入的 sh-theme）。
//   4. sh-theme 的内容（tokens.ts 的 THEME_CSS）只含自定义属性与 color-scheme。
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const APP = path.resolve("tauri-app");
const require = createRequire(path.join(APP, "package.json"));
const postcss = require("postcss");
const ts = require("typescript");
const dist = path.resolve(process.argv[2] ?? "");
const GENERATED = /^\.sh-[A-Za-z0-9]+-\d+$/;
const problems = [];

const walk = (d, pred, out = []) => {
  for (const e of readdirSync(d)) {
    const p = path.join(d, e);
    if (statSync(p).isDirectory()) walk(p, pred, out);
    else if (pred(p)) out.push(p);
  }
  return out;
};

let layered = 0;
let generated = 0;
for (const f of walk(dist, (p) => p.endsWith(".css"))) {
  const root = postcss.parse(readFileSync(f, "utf8"), { from: f });
  for (const node of root.nodes) {
    const where = `${path.relative(dist, f)}:${node.source?.start?.line ?? "?"}`;
    if (node.type === "comment") continue;
    if (node.type === "atrule" && node.name === "layer" && node.params.trim() === "sh-base") { layered++; continue; }
    if (node.type === "rule" && node.selectors.every((s) => GENERATED.test(s.trim()))) { generated++; continue; }
    const text = node.type === "rule" ? node.selector : `@${node.name} ${node.params}`;
    problems.push(`产物 CSS 有层外规则 ${where}：${text.slice(0, 80)}`);
  }
}
for (const f of walk(dist, (p) => p.endsWith(".html")))
  if (/<style[\s>]/i.test(readFileSync(f, "utf8"))) problems.push(`产物 HTML 有 <style>：${path.relative(dist, f)}`);

for (const f of walk(path.join(APP, "src"), (p) => p.endsWith(".tsx") && !/\.test\.tsx$/.test(p))) {
  const sf = ts.createSourceFile(f, readFileSync(f, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const visit = (n) => {
    if ((ts.isJsxOpeningElement(n) || ts.isJsxSelfClosingElement(n)) && n.tagName.getText(sf) === "style")
      problems.push(`源码 JSX 有 <style>：${path.relative(APP, f)}:${sf.getLineAndCharacterOfPosition(n.getStart()).line + 1}`);
    n.forEachChild(visit);
  };
  visit(sf);
}

const { THEME_CSS } = await import(pathToFileURL(path.join(APP, "src/theme/tokens.ts")).href);
postcss.parse(THEME_CSS).walkDecls((d) => {
  if (!d.prop.startsWith("--") && d.prop !== "color-scheme") problems.push(`sh-theme 含非变量声明：${d.prop}`);
});

console.log(`产物 CSS：sh-base 层块 ${layered} 个，生成类规则 ${generated} 条`);
problems.forEach((p) => console.log("  " + p));
console.log(problems.length ? "层叠层检查：不通过" : "层叠层检查：通过");
process.exit(problems.length ? 1 : 0);
