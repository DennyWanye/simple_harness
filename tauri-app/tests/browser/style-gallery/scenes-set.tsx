// 设置车道（set）场景之一：设置页与其各分区。
//   settings-page           views/SettingsView.tsx + components/SettingsPanel.tsx（页面形态，自启开关）
//                           + SettingsProviders.tsx（有数据）+ ModelContextCard.tsx（就绪）
//                           + SettingsSystemSections.tsx / SettingsOrchestrationSections.tsx（各分区有数据）
//   settings-overlay        components/SettingsPanel.tsx（浮层形态）+ 上述分区的空/出错/未授权/手动形态，数据目录被环境变量固定
//   settings-system         components/SettingsSystemSections.tsx：关于与更新（有/无版本号）、自动模式（自动/手动/读取中）、
//                           执行预算（已同步/读取中）、危险区、数据目录（普通/被固定/加载失败/加载中）
//   settings-orchestration  components/SettingsOrchestrationSections.tsx：任务与会话数据（超阈值/未统计/出错/有数据又出错/读取中/无通道）、
//                           本月全局预算（已用/未用/后台没报/出错/无通道）、任务发布目录（已授权/未生效/未授权/出错/刚保存/读取中）
//   settings-skills         components/SettingsOrchestrationSections.tsx：任务技能目录（各状态技能行/空/出错/刚完成/被拒/读取中）
//   settings-providers      components/SettingsProviders.tsx（有数据/出错空列表/读取中）+ components/ModelContextCard.tsx
//                           （项目覆盖/全局覆盖/内置默认/解析失败/读取中/没有默认模型/无通道）
import { SettingsView } from "../../../src/views/SettingsView";
import { SettingsPanel } from "../../../src/components/SettingsPanel";
import { SettingsProviders } from "../../../src/components/SettingsProviders";
import { ModelContextCard } from "../../../src/components/ModelContextCard";
import {
  AutoModeToggle,
  ChatTurnTimeoutSetting,
  DangerZoneSection,
  DataDirSection,
  UpdateSection,
} from "../../../src/components/SettingsSystemSections";
import {
  GlobalBudgetSection,
  PublishDirSection,
  SkillCatalogueSection,
  StorageUsageSection,
} from "../../../src/components/SettingsOrchestrationSections";
import { setTauriAnswer } from "./tauriStub";
import { noop, useOnce } from "./galleryUtil";
import type { Responder } from "./fakeChannel";
import {
  DATA_DIR_PINNED,
  DATA_DIR_PLAIN,
  autoModeResponder,
  budgetResponder,
  fail,
  modelContextResponder,
  never,
  publishDirResponder,
  sequenceAnswer,
  settingsFullResponder,
  settingsSparseResponder,
  silent,
  skillResponder,
  storageResponder,
  timeoutResponder,
  useFakeGetter,
  SET_PROVIDERS,
} from "./set-fixtures";
import "./set-gallery.css";

const AUTOSTART_ON = { ready: true, enabled: true, toggle: noop };

export function SettingsPageScene() {
  useOnce(() => {
    setTauriAnswer("get_data_dir_setting", DATA_DIR_PLAIN);
    setTauriAnswer("plugin:app|version", "0.9.3");
  });
  const getChannel = useFakeGetter(settingsFullResponder);
  return (
    <div className="set-page">
      <SettingsView getChannel={getChannel} secret="fixture" autostart={AUTOSTART_ON} />
    </div>
  );
}

export function SettingsOverlayScene() {
  useOnce(() => {
    setTauriAnswer("get_data_dir_setting", DATA_DIR_PINNED);
  });
  const getChannel = useFakeGetter(settingsSparseResponder);
  return (
    <SettingsPanel
      open
      onClose={noop}
      getChannel={getChannel}
      secret="fixture"
      autostart={{ ready: false, enabled: false, toggle: noop }}
    />
  );
}

/** 用独立假通道挂一个只取 getChannel 的分区组件。 */
function WithChannel({
  responder,
  render,
}: {
  responder: Responder | null;
  render: (getChannel: ReturnType<typeof useFakeGetter>) => React.ReactNode;
}) {
  const getChannel = useFakeGetter(responder);
  return <>{render(getChannel)}</>;
}

export function SettingsSystemScene() {
  useOnce(() => {
    setTauriAnswer("plugin:app|version", sequenceAnswer(["1.2.3", null]));
    setTauriAnswer(
      "get_data_dir_setting",
      sequenceAnswer([DATA_DIR_PLAIN, DATA_DIR_PINNED, fail("没有读取权限（夹具）"), never]),
    );
  });
  return (
    <div className="set-col">
      <UpdateSection />
      <UpdateSection />
      <WithChannel responder={autoModeResponder("auto")} render={(g) => <AutoModeToggle getChannel={g} />} />
      <WithChannel responder={autoModeResponder("manual")} render={(g) => <AutoModeToggle getChannel={g} />} />
      <WithChannel responder={silent} render={(g) => <AutoModeToggle getChannel={g} />} />
      <WithChannel responder={timeoutResponder(15)} render={(g) => <ChatTurnTimeoutSetting getChannel={g} />} />
      <WithChannel responder={silent} render={(g) => <ChatTurnTimeoutSetting getChannel={g} />} />
      <DangerZoneSection />
      <DataDirSection />
      <DataDirSection />
      <DataDirSection />
      <DataDirSection />
    </div>
  );
}

export function SettingsOrchestrationScene() {
  return (
    <div className="set-col">
      <WithChannel responder={storageResponder("measured")} render={(g) => <StorageUsageSection getChannel={g} />} />
      <WithChannel responder={storageResponder("unmeasured")} render={(g) => <StorageUsageSection getChannel={g} />} />
      <WithChannel responder={storageResponder("error")} render={(g) => <StorageUsageSection getChannel={g} />} />
      <WithChannel
        responder={storageResponder("measured-then-error")}
        render={(g) => <StorageUsageSection getChannel={g} />}
      />
      <WithChannel responder={silent} render={(g) => <StorageUsageSection getChannel={g} />} />
      <WithChannel responder={null} render={(g) => <StorageUsageSection getChannel={g} />} />
      <WithChannel responder={budgetResponder("opened")} render={(g) => <GlobalBudgetSection getChannel={g} />} />
      <WithChannel responder={budgetResponder("unopened")} render={(g) => <GlobalBudgetSection getChannel={g} />} />
      <WithChannel responder={budgetResponder("none")} render={(g) => <GlobalBudgetSection getChannel={g} />} />
      <WithChannel responder={budgetResponder("error")} render={(g) => <GlobalBudgetSection getChannel={g} />} />
      <WithChannel responder={null} render={(g) => <GlobalBudgetSection getChannel={g} />} />
      <WithChannel responder={publishDirResponder("enabled")} render={(g) => <PublishDirSection getChannel={g} />} />
      <WithChannel responder={publishDirResponder("invalid")} render={(g) => <PublishDirSection getChannel={g} />} />
      <WithChannel responder={publishDirResponder("unset")} render={(g) => <PublishDirSection getChannel={g} />} />
      <WithChannel responder={publishDirResponder("error")} render={(g) => <PublishDirSection getChannel={g} />} />
      <WithChannel responder={publishDirResponder("saved")} render={(g) => <PublishDirSection getChannel={g} />} />
      <WithChannel responder={silent} render={(g) => <PublishDirSection getChannel={g} />} />
    </div>
  );
}

export function SettingsSkillsScene() {
  return (
    <div className="set-col">
      <WithChannel responder={skillResponder("rows")} render={(g) => <SkillCatalogueSection getChannel={g} />} />
      <WithChannel responder={skillResponder("empty")} render={(g) => <SkillCatalogueSection getChannel={g} />} />
      <WithChannel responder={skillResponder("error")} render={(g) => <SkillCatalogueSection getChannel={g} />} />
      <WithChannel responder={skillResponder("done")} render={(g) => <SkillCatalogueSection getChannel={g} />} />
      <WithChannel responder={skillResponder("refused")} render={(g) => <SkillCatalogueSection getChannel={g} />} />
      <WithChannel responder={silent} render={(g) => <SkillCatalogueSection getChannel={g} />} />
    </div>
  );
}

const providersOk: Responder = (msg) =>
  msg.type === "settings_providers_list_request"
    ? { type: "settings_providers_list_response", payload: { providers: SET_PROVIDERS } }
    : null;
const providersError: Responder = (msg) =>
  msg.type === "settings_providers_list_request"
    ? { type: "settings_providers_error", payload: { reason: "registry_unavailable" } }
    : null;

export function SettingsProvidersScene() {
  return (
    <div className="set-col">
      <WithChannel responder={providersOk} render={(g) => <SettingsProviders getChannel={g} />} />
      <WithChannel responder={providersError} render={(g) => <SettingsProviders getChannel={g} />} />
      <WithChannel responder={silent} render={(g) => <SettingsProviders getChannel={g} />} />
      <WithChannel
        responder={modelContextResponder({ source: "project" })}
        render={(g) => <ModelContextCard getChannel={g} />}
      />
      <WithChannel
        responder={modelContextResponder({ source: "global", window: 1_000_000 })}
        render={(g) => <ModelContextCard getChannel={g} />}
      />
      <WithChannel
        responder={modelContextResponder({ source: "builtin", window: 25_600 })}
        render={(g) => <ModelContextCard getChannel={g} />}
      />
      <WithChannel
        responder={modelContextResponder({ reason: "resolve_error: 夹具解析失败" })}
        render={(g) => <ModelContextCard getChannel={g} />}
      />
      <WithChannel
        responder={modelContextResponder({ replyGet: false })}
        render={(g) => <ModelContextCard getChannel={g} />}
      />
      <WithChannel
        responder={modelContextResponder({ defaultModel: "" })}
        render={(g) => <ModelContextCard getChannel={g} />}
      />
      <WithChannel responder={null} render={(g) => <ModelContextCard getChannel={g} />} />
    </div>
  );
}
