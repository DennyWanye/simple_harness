// Separate fixture server; never starts the application entry point or backend.
import { createServer } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
const root = fileURLToPath(new URL("../../", import.meta.url));
const server = await createServer({
  root, configFile: false, plugins: [
    {
      name: "layout-network-boundary",
      enforce: "pre",
      resolveId(id) { if (/\/controlWs$/.test(id)) return "\0layout-control-ws"; },
      load(id) { if (id === "\0layout-control-ws") return `export const controlWS = {
        state: () => "connected", send: () => false, send_command: () => false,
        on_message: () => () => {}, on_state_change: () => () => {}
      };`; },
      configureServer(server) {
        server.middlewares.use("/layout", async (_req, res) => {
          res.setHeader("Content-Type", "text/html");
          res.end(await server.transformIndexHtml("/layout", '<div id="root"></div><script type="module" src="/tests/browser/primary-permission.fixture.tsx"></script>'));
        });
      },
    }, react(),
  ],
  server: { host: "127.0.0.1", port: 0, hmr: false },
});
await server.listen();
console.log(`LAYOUT_URL=http://127.0.0.1:${server.httpServer.address().port}/layout`);
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, async () => { await server.close(); process.exit(0); });
