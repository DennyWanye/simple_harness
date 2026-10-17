// 样式画廊的产物构建（9I 核对用）：node style-gallery-build.mjs <应用目录> <输出目录>
// 应用目录可以是工作区的 tauri-app，也可以是从某个提交导出的副本（node_modules 用符号链接）。
// 与发版构建相同的插件与 Vite 默认值（含 Lightning CSS 压缩与默认目标）。
import { build } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

const [appDir, outDir] = process.argv.slice(2).map((p) => path.resolve(p));
await build({
  root: appDir,
  configFile: false,
  plugins: [react()],
  logLevel: "warn",
  base: "./",
  build: {
    outDir,
    emptyOutDir: true,
    rollupOptions: { input: path.join(appDir, "tests/browser/style-gallery.html") },
  },
});
console.log(`GALLERY_BUILT=${outDir}`);
