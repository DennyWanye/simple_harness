// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// 设置车道（set）场景之二：弹窗、启动/引导浮层、项目相关卡片与小部件。
//   add-provider-modal   components/AddProviderModal.tsx（新建空白 / 新建带探测结果+探测失败 / 编辑带保存错误+保存中+探测失败）
//   change-model-modal   code-panel/ChangeModelModal.tsx（思考+快速+可选窗口 / 推理强度+只读窗口 / 无可调参数 / 跟随默认 / 未知模型 / 只有思考）
//   startup-dialogs      components/OnboardingWizard.tsx（欢迎步）+ components/FeedbackPanel.tsx
//                        + components/StartupOverlay.tsx（启动中 / 启动失败带信息 / 启动失败无信息 / 就绪不渲染）
//   project-directory-cards components/ProjectDirectoryCard.tsx（新建无位置 / 新建 Windows 路径 / 新建根目录 / 使用现有+报错）
//   context-ring-banners components/ContextRing.tsx（不可用/仅绑定/旧记录/各档用量/带标签/可点）
//                        + components/ProtectedPathCard.tsx（单条读取 / 多条写入）
import { AddProviderModal } from "../../../src/components/AddProviderModal";
import { ChangeModelModal } from "../../../src/code-panel/ChangeModelModal";
import { useSessionModelsStore } from "../../../src/code-panel/sessionModelsStore";
import { OnboardingWizard } from "../../../src/components/OnboardingWizard";
import { FeedbackPanel } from "../../../src/components/FeedbackPanel";
import { StartupOverlay } from "../../../src/components/StartupOverlay";
import { ProjectDirectoryCard } from "../../../src/components/ProjectDirectoryCard";
import { ContextRing } from "../../../src/components/ContextRing";
import { ProtectedPathCard } from "../../../src/components/ProtectedPathCard";
import { noop, useOnce } from "./galleryUtil";
import { SET_CATALOG, SET_PROVIDERS, snap } from "./set-fixtures";
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
        <ChangeModelModal
          session_id="s-1"
          current_model="kimi-k3"
          current_params={{ thinking: true }}
          onClose={noop}
        />
      </div>
      <div className="set-frame">
        <ChangeModelModal
          session_id="s-2"
          current_model="gpt-5.5"
          current_params={{ effort: "high" }}
          onClose={noop}
        />
      </div>
      <div className="set-frame">
        <ChangeModelModal session_id="s-3" current_model="embed-x" onClose={noop} />
      </div>
      <div className="set-frame">
        <ChangeModelModal
          session_id="s-4"
          current_model={null}
          current_params={{ fast: true }}
          onClose={noop}
        />
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
        <StartupOverlay
          state="starting"
          errorMessage={null}
          onRetry={noop}
          onOpenLogDir={noop}
          onExit={noop}
        />
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
        <StartupOverlay
          state="failed"
          errorMessage={null}
          onRetry={noop}
          onOpenLogDir={noop}
          onExit={noop}
        />
      </div>
      <StartupOverlay
        state="ready"
        errorMessage={null}
        onRetry={noop}
        onOpenLogDir={noop}
        onExit={noop}
      />
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

export function ProjectDirectoryCardsScene() {
  return (
    <div className="set-row">
      <div className="set-col">
        <ProjectDirectoryCard
          request={{ ...dirBase, title: "选择夹具网站的保存位置" }}
          onConfirm={() => true}
        />
        <ProjectDirectoryCard
          request={{
            ...dirBase,
            directory_mode: "create_new",
            parent_directory: "C:\\Users\\fixture\\Projects\\",
          }}
          onConfirm={() => true}
        />
        <ProjectDirectoryCard
          request={{ ...dirBase, directory_mode: "create_new", parent_directory: "/" }}
        />
        <ProjectDirectoryCard
          request={{
            ...dirBase,
            directory_mode: "use_existing",
            parent_directory: "/Users/fixture/code/existing/",
          }}
          error="这个文件夹不可写（夹具）"
          submittedLabel="已确认"
        />
      </div>
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
        <ContextRing
          snapshot={snap({ source: "binding_only", has_measurement: false })}
          showLabel
        />
        <ContextRing
          snapshot={snap({ legacy_incomplete: true } as Partial<ReturnType<typeof snap>>)}
        />
        <ContextRing
          snapshot={snap({ availability: "unavailable" } as Partial<ReturnType<typeof snap>>)}
        />
        <ContextRing
          snapshot={snap({ prompt_tokens: 95_000, cached_tokens: 12_000 })}
          showLabel
          onClick={noop}
        />
        <ContextRing snapshot={snap({ prompt_tokens: 600_000 })} size={22} showLabel />
        <ContextRing snapshot={snap({ prompt_tokens: 800_000 })} showLabel onClick={noop} />
        <ContextRing snapshot={snap({ prompt_tokens: 940_000 })} size={28} showLabel />
        <ContextRing
          snapshot={snap({ prompt_tokens: 990_000, recall_sweet: 0 })}
          showLabel
          style={{ marginLeft: 8 }}
        />
      </div>
      <div className="set-row">
        <div className="set-frame">
          <ProtectedPathCard
            queue={[ppRequest("r1", "read", "/Users/fixture/.ssh/config")]}
            onDecide={noop}
          />
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
