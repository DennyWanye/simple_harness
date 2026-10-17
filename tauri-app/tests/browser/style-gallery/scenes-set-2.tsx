// 设置车道（set）场景之二：弹窗、启动/引导浮层、项目相关卡片与小部件。
//   add-provider-modal   components/AddProviderModal.tsx（新建空白 / 新建带探测结果+探测失败 / 编辑带保存错误+保存中+探测失败）
//   change-model-modal   code-panel/ChangeModelModal.tsx（思考+快速+可选窗口 / 推理强度+只读窗口 / 无可调参数 / 跟随默认 / 未知模型 / 只有思考）
//   startup-dialogs      components/OnboardingWizard.tsx（欢迎步）+ components/FeedbackPanel.tsx
//                        + components/StartupOverlay.tsx（启动中 / 启动失败带信息 / 启动失败无信息 / 就绪不渲染）
//   project-inspector    components/ProjectInspector.tsx（项目缺失+执行目录 / 可用+分离头 / 加载中 / 普通会话 / 无 Git / 未选会话）
//                        + components/ProjectPickerDialog.tsx（项目 / 带默认目录）
//                        + components/ProjectDirectoryCard.tsx（新建无位置 / 新建 Windows 路径 / 新建根目录 / 使用现有+报错）
//   context-ring-banners components/ContextRing.tsx（不可用/仅绑定/旧记录/各档用量/带标签/可点）
//                        + components/ModelDownloadBanner.tsx（检查中/下载中有进度/下载中未知总量/出错）
//                        + components/ProtectedPathCard.tsx（单条读取 / 多条写入）
import { AddProviderModal } from "../../../src/components/AddProviderModal";
import { ChangeModelModal } from "../../../src/code-panel/ChangeModelModal";
import { useSessionModelsStore } from "../../../src/code-panel/sessionModelsStore";
import { OnboardingWizard } from "../../../src/components/OnboardingWizard";
import { FeedbackPanel } from "../../../src/components/FeedbackPanel";
import { StartupOverlay } from "../../../src/components/StartupOverlay";
import { ProjectInspector } from "../../../src/components/ProjectInspector";
import { ProjectPickerDialog } from "../../../src/components/ProjectPickerDialog";
import { ProjectDirectoryCard } from "../../../src/components/ProjectDirectoryCard";
import { ContextRing } from "../../../src/components/ContextRing";
import { ModelDownloadBanner } from "../../../src/components/ModelDownloadBanner";
import { ProtectedPathCard } from "../../../src/components/ProtectedPathCard";
import { useSessionsStore } from "../../../src/stores/sessionsStore";
import type { Responder } from "./fakeChannel";
import { noop, useOnce } from "./galleryUtil";
import {
  SET_CATALOG,
  SET_PROJECT_INSPECTIONS,
  SET_PROJECT_PAGES,
  SET_PROVIDERS,
  snap,
  useFakeGetter,
} from "./set-fixtures";
import "./set-gallery.css";

const neverBundle = () => new Promise<never>(() => undefined);
const okConnection = async () => ({ ok: true });

export function AddProviderModalScene() {
  return (
    <div className="set-row">
      <div className="set-frame">
        <AddProviderModal editing={null} onClose={noop} onSave={noop} onProbeModels={noop} />
      </div>
      <div className="set-frame">
        <AddProviderModal
          editing={null}
          onClose={noop}
          onSave={noop}
          onProbeModels={noop}
          probedModels={["deepseek-v4-flash", "deepseek-v4-pro", "gpt-5.5"]}
          probeError="All connection attempts failed"
          probing
        />
      </div>
      <div className="set-frame">
        <AddProviderModal
          editing={SET_PROVIDERS[0]}
          onClose={noop}
          onSave={noop}
          onProbeModels={noop}
          probeError="HTTP 401：密钥无效（夹具）"
          saveError="保存失败：配置版本冲突（夹具）"
          saving
        />
      </div>
      <div className="set-frame">
        <AddProviderModal editing={SET_PROVIDERS[2]} onClose={noop} onSave={noop} />
      </div>
    </div>
  );
}

export function ChangeModelModalScene() {
  useOnce(() => useSessionModelsStore.getState().set_catalog(SET_CATALOG, "live", "kimi-k3"));
  return (
    <div className="set-row">
      <div className="set-frame">
        <ChangeModelModal session_id="s-1" current_model="kimi-k3" current_params={{ thinking: true }} onClose={noop} />
      </div>
      <div className="set-frame">
        <ChangeModelModal session_id="s-2" current_model="gpt-5.5" current_params={{ effort: "high" }} onClose={noop} />
      </div>
      <div className="set-frame">
        <ChangeModelModal session_id="s-3" current_model="embed-x" onClose={noop} />
      </div>
      <div className="set-frame">
        <ChangeModelModal session_id="s-4" current_model={null} current_params={{ fast: true }} onClose={noop} />
      </div>
      <div className="set-frame">
        <ChangeModelModal session_id="s-5" current_model="custom-unknown" onClose={noop} />
      </div>
      <div className="set-frame">
        <ChangeModelModal session_id="s-6" current_model="think-only" onClose={noop} />
      </div>
    </div>
  );
}

export function StartupDialogsScene() {
  return (
    <div className="set-row">
      <div className="set-frame">
        <OnboardingWizard onTestConnection={okConnection} onComplete={noop} onSkip={noop} />
      </div>
      <div className="set-frame">
        <FeedbackPanel onBuildBundle={neverBundle} onClose={noop} />
      </div>
      <div className="set-frame">
        <StartupOverlay state="starting" errorMessage={null} onRetry={noop} onOpenLogDir={noop} onExit={noop} />
      </div>
      <div className="set-frame">
        <StartupOverlay
          state="failed"
          errorMessage={"后台进程退出（夹具）\nTraceback: 夹具第二行"}
          onRetry={noop}
          onOpenLogDir={noop}
          onExit={noop}
        />
      </div>
      <div className="set-frame">
        <StartupOverlay state="failed" errorMessage={null} onRetry={noop} onOpenLogDir={noop} onExit={noop} />
      </div>
      <StartupOverlay state="ready" errorMessage={null} onRetry={noop} onOpenLogDir={noop} onExit={noop} />
    </div>
  );
}

const dirBase = {
  session_id: "s-dir",
  run_id: "run-1",
  request_id: "req-1",
  decision_id: "dec-1",
  nonce: "n-1",
  version: 1,
  title: "",
  required_action: "choose_directory",
  wait_kind: "user_content" as const,
  wait_ref: "wait-1",
  project_name: "夹具网站",
  folder_name: "fixture-site",
  project_kind: "网站项目",
};

export function ProjectInspectorScene() {
  useOnce(() =>
    useSessionsStore.setState({
      project_session_pages: SET_PROJECT_PAGES,
      project_inspections: SET_PROJECT_INSPECTIONS,
    } as unknown as Partial<ReturnType<typeof useSessionsStore.getState>>),
  );
  return (
    <div className="set-row">
      {["s-missing", "s-ok", "s-loading", "s-free", "s-nogit", ""].map((sid) => (
        <div className="set-rail" key={sid || "none"}>
          <ProjectInspector activeSid={sid} onSwitchSid={noop} />
        </div>
      ))}
      <div className="set-frame">
        <ProjectPickerDialog open onClose={noop} onProjectSelected={noop} />
      </div>
      <div className="set-frame">
        <ProjectPickerDialog open onClose={noop} onProjectSelected={noop} onDefaultSelected={noop} />
      </div>
      <ProjectPickerDialog open={false} onClose={noop} onProjectSelected={noop} />
      <div className="set-col">
        <ProjectDirectoryCard request={{ ...dirBase, title: "选择夹具网站的保存位置" }} onConfirm={() => true} />
        <ProjectDirectoryCard
          request={{ ...dirBase, directory_mode: "create_new", parent_directory: "C:\\Users\\fixture\\Projects\\" }}
          onConfirm={() => true}
        />
        <ProjectDirectoryCard request={{ ...dirBase, directory_mode: "create_new", parent_directory: "/" }} />
        <ProjectDirectoryCard
          request={{ ...dirBase, directory_mode: "use_existing", parent_directory: "/Users/fixture/code/existing/" }}
          error="这个文件夹不可写（夹具）"
          submittedLabel="已确认"
        />
      </div>
    </div>
  );
}

const provision =
  (payload: Record<string, unknown>): Responder =>
  (msg) =>
    msg.type === "model_provision_status" ? { type: "model_provision_status_response", payload } : null;

function Banner({ responder }: { responder: Responder }) {
  const getChannel = useFakeGetter(responder);
  return (
    <div className="set-frame-short">
      <ModelDownloadBanner getChannel={getChannel} />
    </div>
  );
}

const ppRequest = (id: string, op: "read" | "write", path: string) => ({
  request_id: id,
  session_id: "s",
  path,
  op,
  tool: op === "read" ? "read_file" : "write_file",
  reason: "凭证或密钥（夹具）",
});

export function ContextRingBannersScene() {
  return (
    <div className="set-col">
      <div className="set-row">
        <ContextRing snapshot={null} />
        <ContextRing snapshot={null} showLabel />
        <ContextRing snapshot={snap({ source: "binding_only", has_measurement: false })} showLabel />
        <ContextRing snapshot={snap({ legacy_incomplete: true } as Partial<ReturnType<typeof snap>>)} />
        <ContextRing snapshot={snap({ availability: "unavailable" } as Partial<ReturnType<typeof snap>>)} />
        <ContextRing snapshot={snap({ prompt_tokens: 95_000, cached_tokens: 12_000 })} showLabel onClick={noop} />
        <ContextRing snapshot={snap({ prompt_tokens: 600_000 })} size={22} showLabel />
        <ContextRing snapshot={snap({ prompt_tokens: 800_000 })} showLabel onClick={noop} />
        <ContextRing snapshot={snap({ prompt_tokens: 940_000 })} size={28} showLabel />
        <ContextRing snapshot={snap({ prompt_tokens: 990_000, recall_sweet: 0 })} showLabel style={{ marginLeft: 8 }} />
      </div>
      <Banner responder={provision({ state: "checking" })} />
      <Banner
        responder={provision({
          state: "downloading",
          index: 1,
          total: 2,
          current: "bge-m3",
          downloaded_bytes: 1_200_000_000,
          total_bytes: 2_300_000_000,
        })}
      />
      <Banner responder={provision({ state: "downloading", index: 2, total: 2, downloaded_bytes: 0, total_bytes: 0 })} />
      <Banner responder={provision({ state: "error", error: "连接 hf-mirror 超时（夹具）" })} />
      <Banner responder={provision({ state: "ready" })} />
      <div className="set-row">
        <div className="set-frame">
          <ProtectedPathCard queue={[ppRequest("r1", "read", "/Users/fixture/.ssh/config")]} onDecide={noop} />
        </div>
        <div className="set-frame">
          <ProtectedPathCard
            queue={[
              ppRequest("r2", "write", "/Users/fixture/.aws/credentials"),
              ppRequest("r3", "read", "/Users/fixture/.netrc"),
              ppRequest("r4", "read", "/Users/fixture/.npmrc"),
            ]}
            onDecide={noop}
          />
        </div>
        <ProtectedPathCard queue={[]} onDecide={noop} />
      </div>
    </div>
  );
}
