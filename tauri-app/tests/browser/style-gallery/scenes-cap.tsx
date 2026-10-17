// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

// cap 车道场景（一）：技能商店、能力中心、能力操作卡。
//   skill-store                → src/components/SkillStorePanel.tsx（page 形态：已安装列表、空态、提示 info/warning/error）
//   skill-store-confirm        → src/components/SkillStorePanel.tsx（安装确认弹层：敏感权限 / 普通两种）
//   skill-store-overlay        → src/components/SkillStorePanel.tsx（overlay 形态 + 关闭钮 + 加载中）
//   capability-center          → src/views/SkillsView.tsx、src/components/CapabilityCenterPanel.tsx（有数据、清单详情）
//   capability-center-states   → src/components/CapabilityCenterPanel.tsx（Manual 模式、离线快照、空清单、overlay 空列表）
//   capability-operation-cards → src/components/CapabilityOperationCard.tsx（各状态、紧凑形态、长文本折叠、无按钮）
// 说明：技能商店的"市场 / 通过 URL 安装"页签和能力中心的"操作"页签只能点击切换，画廊不模拟点击，渲染不到。
import { useState } from "react";
import { CapabilityCenterPanel } from "../../../src/components/CapabilityCenterPanel";
import { CapabilityOperationCard } from "../../../src/components/CapabilityOperationCard";
import { SkillStorePanel } from "../../../src/components/SkillStorePanel";
import { SkillsView } from "../../../src/views/SkillsView";
import {
  capabilityChannel,
  capabilityOperations,
  compactOperations,
  emptyManifestCapability,
  godotCapability,
  installedSkills,
  otherCapabilities,
  skillStoreChannel,
  stagedPendingDangerous,
  stagedPendingSafe,
} from "./cap-fixtures";
import { noop, useOnce } from "./galleryUtil";
import "./cap-gallery.css";

export function SkillStoreScene() {
  const [channels] = useState(() => ({
    list: skillStoreChannel(installedSkills),
    empty: skillStoreChannel([], [], "注册表加载失败（夹具）"),
    info: skillStoreChannel(installedSkills, [
      { type: "skill_install_confirm_response", payload: { ok: true, status: "denied" } },
    ]),
    error: skillStoreChannel(installedSkills, [
      {
        type: "skill_install_pending",
        payload: { ok: false, error: { code: "repo_unreachable", message: "仓库无法访问（夹具）" } },
      },
    ]),
  }));
  return (
    <>
      <div className="cap-frame">
        <SkillStorePanel open variant="page" channel={channels.list} onOpenCapabilityCenter={noop} />
      </div>
      <div className="cap-frame-short">
        <SkillStorePanel open variant="page" channel={channels.empty} />
      </div>
      <div className="cap-frame">
        <SkillStorePanel open variant="page" channel={channels.info} />
      </div>
      <div className="cap-frame">
        <SkillStorePanel open variant="page" channel={channels.error} />
      </div>
    </>
  );
}

export function SkillStoreConfirmScene() {
  const [channels] = useState(() => ({
    dangerous: skillStoreChannel(installedSkills, [{ type: "skill_install_pending", payload: stagedPendingDangerous }]),
    safe: skillStoreChannel(installedSkills, [{ type: "skill_install_pending", payload: stagedPendingSafe }]),
  }));
  return (
    <>
      <div className="cap-frame">
        <SkillStorePanel open variant="page" channel={channels.dangerous} />
      </div>
      <div className="cap-frame">
        <SkillStorePanel open variant="page" channel={channels.safe} />
      </div>
    </>
  );
}

export function SkillStoreOverlayScene() {
  // 不回已安装列表：保持加载中。
  const [channel] = useState(() => skillStoreChannel(null));
  return <SkillStorePanel open channel={channel} onClose={noop} onOpenCapabilityCenter={noop} />;
}

export function CapabilityCenterScene() {
  const [channel] = useState(() =>
    capabilityChannel([godotCapability, ...otherCapabilities], capabilityOperations, true),
  );
  return (
    <div className="cap-frame">
      <SkillsView channel={channel} sessionId="session-1" />
    </div>
  );
}

export function CapabilityCenterStatesScene() {
  useOnce(() => {
    try {
      localStorage.setItem("deskpet.auto_mode", "true");
    } catch {
      // 存储不可用时组件按 auto 处理，结果相同。
    }
  });
  const [channels] = useState(() => ({
    manual: capabilityChannel(null, [], false),
    empty: capabilityChannel([], [], true),
  }));
  return (
    <>
      <div className="cap-frame">
        <CapabilityCenterPanel
          open
          variant="page"
          channel={channels.manual}
          onOpenLegacySkillStore={noop}
          initialCapabilities={otherCapabilities}
        />
      </div>
      <div className="cap-frame">
        <CapabilityCenterPanel
          open
          variant="page"
          channel={null}
          onOpenLegacySkillStore={noop}
          initialCapabilities={[emptyManifestCapability]}
        />
      </div>
      <CapabilityCenterPanel open channel={channels.empty} onClose={noop} onOpenLegacySkillStore={noop} />
    </>
  );
}

export function CapabilityOperationCardsScene() {
  return (
    <div className="cap-column">
      {capabilityOperations.map((operation) => (
        <CapabilityOperationCard key={operation.operation_id} operation={operation} onAction={noop} />
      ))}
      {compactOperations.map((operation) => (
        <CapabilityOperationCard key={operation.operation_id} operation={operation} onAction={noop} compact />
      ))}
      <CapabilityOperationCard operation={capabilityOperations[0]} />
    </div>
  );
}
