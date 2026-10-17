// 用 Vite 预览服务器提供画廊产物：node style-gallery-serve.mjs <输出目录>
import { preview } from "vite";
import path from "node:path";

const outDir = path.resolve(process.argv[2]);
const server = await preview({
  root: path.dirname(outDir),
  configFile: false,
  logLevel: "warn",
  build: { outDir },
  preview: { host: "127.0.0.1", port: 0, strictPort: false },
});
const { port } = server.httpServer.address();
console.log(`GALLERY_URL=http://127.0.0.1:${port}/tests/browser/style-gallery.html`);
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => server.httpServer.close(() => process.exit(0)));
