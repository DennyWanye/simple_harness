// 样式画廊（9I 内联样式转类的浏览器核对夹具）。无后端、无服务商、无真实凭据；数据全是固定夹具。
// 打开方式：style-gallery.html?scene=<场景名>&theme=dark|light；不带 scene 时列出全部场景名。
// 核对脚本：scripts/code_quality/webkit_style_compare.py（WebKit + 产物构建，逐元素比对计算样式）。
import "./style-gallery/freezeTime";
import { Component, useEffect, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "../../src/index.css";
import { applyTheme } from "../../src/theme/applyTheme";
import { SCENES } from "./style-gallery/scenes";

interface GalleryState {
  scenes: string[];
  ready: boolean;
  errors: string[];
}
declare global {
  interface Window {
    __gallery: GalleryState;
  }
}

const params = new URLSearchParams(location.search);
document.documentElement.setAttribute("data-theme", params.get("theme") === "light" ? "light" : "dark");
applyTheme();
window.__gallery = { scenes: Object.keys(SCENES), ready: false, errors: [] };
window.addEventListener("error", (e) => window.__gallery.errors.push(String(e.message)));
window.addEventListener("unhandledrejection", (e) => window.__gallery.errors.push(String(e.reason)));

class Boundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null };
  static getDerivedStateFromError(error: unknown) {
    return { error: String(error) };
  }
  componentDidCatch(error: unknown) {
    window.__gallery.errors.push(`渲染出错：${String(error)}`);
  }
  render() {
    return this.state.error ? <pre className="gallery-error">{this.state.error}</pre> : this.props.children;
  }
}

function Ready() {
  useEffect(() => {
    requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(() => (window.__gallery.ready = true), 300)));
  }, []);
  return null;
}

const name = params.get("scene");
const Scene = name ? SCENES[name] : undefined;
createRoot(document.getElementById("root")!).render(
  Scene ? (
    <Boundary>
      <div className="gallery-stage">
        <Scene />
      </div>
      <Ready />
    </Boundary>
  ) : (
    <ul>
      {Object.keys(SCENES).map((s) => (
        <li key={s}>{s}</li>
      ))}
      <Ready />
    </ul>
  ),
);
