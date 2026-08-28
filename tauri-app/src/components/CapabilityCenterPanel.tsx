// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import {
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
} from "react";

import type { IncomingMessage } from "../types/messages";
import {
  buildCapabilityMutationMessage,
  buildCapabilityOperationActionMessage,
  isCapabilityListResponse,
  isCapabilityOperationEventMessage,
  isCapabilityOperationsResponse,
  reduceCapabilityOperationEvent,
  type CapabilityAuthorizationMode,
  type CapabilityCategory,
  type CapabilityDescriptor,
  type CapabilityOperation,
  type CapabilityOperationAction,
} from "../types/capabilities";
import type { ControlChannel } from "../ws/ControlChannel";
import { dark } from "../theme/components";
import { CapabilityOperationCard } from "./CapabilityOperationCard";

type CapabilityChannel = Pick<ControlChannel, "send" | "onMessage">;
type Tab = "capabilities" | "operations";
type CategoryFilter = "all" | CapabilityCategory;
const EMPTY_CAPABILITIES: CapabilityDescriptor[] = [];
const EMPTY_OPERATIONS: CapabilityOperation[] = [];

function capabilityMatchesFilters(
  capability: CapabilityDescriptor,
  category: CategoryFilter,
  query: string,
): boolean {
  if (category !== "all" && !capability.categories.includes(category)) {
    return false;
  }
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return true;
  return [
    capability.name,
    capability.description,
    capability.capability_id,
    capability.source.label,
  ]
    .filter(Boolean)
    .some((value) => String(value).toLocaleLowerCase().includes(needle));
}

interface Props {
  open: boolean;
  channel: CapabilityChannel | null;
  sessionId?: string | null;
  /**
   * 渲染形态（D5 宿主模式）：
   * - "overlay"（默认）：既有浮层 —— fixed backdrop + 居中 dialog；
   * - "page"：工作台视图内嵌 —— 去 backdrop/fixed，填满内容区
   *   （SkillsView 宿主，WB-6）。page 模式无关闭钮，onClose 可省略。
   */
  variant?: "overlay" | "page";
  onClose?: () => void;
  onOpenLegacySkillStore: () => void;
  initialCapabilities?: CapabilityDescriptor[];
  initialOperations?: CapabilityOperation[];
}

const CATEGORY_LABEL: Record<CapabilityCategory, string> = {
  instruction: "说明",
  tool: "工具",
  mcp: "MCP",
  pack: "能力包",
};

const HEALTH_LABEL: Record<CapabilityDescriptor["health"], string> = {
  healthy: "健康",
  degraded: "降级",
  unavailable: "不可用",
  validating: "验证中",
  unknown: "待确认",
};

const HEALTH_COLOR: Record<CapabilityDescriptor["health"], string> = {
  healthy: "#059669",
  degraded: "#d97706",
  unavailable: "#dc2626",
  validating: "#0284c7",
  unknown: "#64748b",
};

function readCachedAuthorizationMode(): CapabilityAuthorizationMode {
  try {
    return localStorage.getItem("deskpet.auto_mode") === "true"
      ? "auto"
      : "manual";
  } catch {
    return "manual";
  }
}

export function CapabilityCenterPanel({
  open,
  channel,
  sessionId = null,
  variant = "overlay",
  onClose,
  onOpenLegacySkillStore,
  initialCapabilities = EMPTY_CAPABILITIES,
  initialOperations = EMPTY_OPERATIONS,
}: Props) {
  const [tab, setTab] = useState<Tab>("capabilities");
  const [capabilities, setCapabilities] =
    useState<CapabilityDescriptor[]>(initialCapabilities);
  const [operations, setOperations] =
    useState<CapabilityOperation[]>(initialOperations);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<CategoryFilter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(
    initialCapabilities[0]?.capability_id ?? null,
  );
  const [authorizationMode, setAuthorizationMode] =
    useState<CapabilityAuthorizationMode>(readCachedAuthorizationMode);
  const [notice, setNotice] = useState<string | null>(null);

  // One initial snapshot request + the existing push stream. There is no
  // interval, background polling loop, or second job/progress protocol.
  useEffect(() => {
    if (!open || !channel) return undefined;
    channel.send({
      type: "capability_list",
      payload: { session_id: sessionId || null },
    });
    channel.send({ type: "capability_operations_list", payload: {} });
    channel.send({ type: "permission_auto_mode_get", payload: {} });

    const off = channel.onMessage((message: IncomingMessage) => {
      // Capability wire messages are being added compatibly and may arrive
      // before older IncomingMessage unions know their literals.
      const capabilityMessage: unknown = message;
      if (isCapabilityListResponse(capabilityMessage)) {
        setCapabilities(capabilityMessage.payload.capabilities);
        setSelectedId((current) =>
          current &&
          capabilityMessage.payload.capabilities.some(
            (item) => item.capability_id === current,
          )
            ? current
            : capabilityMessage.payload.capabilities[0]?.capability_id ?? null,
        );
        return;
      }
      if (isCapabilityOperationsResponse(capabilityMessage)) {
        setOperations(capabilityMessage.payload.operations);
        return;
      }
      if (isCapabilityOperationEventMessage(capabilityMessage)) {
        setOperations((current) =>
          reduceCapabilityOperationEvent(current, capabilityMessage.payload),
        );
        return;
      }
      if (message.type === "permission_auto_mode_response") {
        const mode = message.payload.enabled ? "auto" : "manual";
        setAuthorizationMode(mode);
        try {
          localStorage.setItem(
            "deskpet.auto_mode",
            String(message.payload.enabled),
          );
        } catch {
          // The backend response remains authoritative if storage is disabled.
        }
      }
    });
    return off;
  }, [open, channel, sessionId]);

  const visibleCapabilities = useMemo(() => {
    return capabilities.filter((capability) =>
      capabilityMatchesFilters(capability, category, query),
    );
  }, [capabilities, category, query]);

  // Filtering must never leave the detail pane pointing at an item that is no
  // longer present in the visible list. Persist the fallback selection so the
  // list's aria state and the detail pane share one source of truth even when
  // the capability snapshot arrives asynchronously after the filter changes.
  useEffect(() => {
    setSelectedId((current) =>
      current &&
      visibleCapabilities.some((item) => item.capability_id === current)
        ? current
        : visibleCapabilities[0]?.capability_id ?? null,
    );
  }, [visibleCapabilities]);

  const selected =
    visibleCapabilities.find((item) => item.capability_id === selectedId) ?? null;

  const sendOperationAction = (
    action: CapabilityOperationAction,
    operation: CapabilityOperation,
  ) => {
    if (!channel) {
      setNotice("控制通道未连接，暂时不能执行操作。");
      return;
    }
    channel.send(buildCapabilityOperationActionMessage(action, operation));
    setNotice(`已提交${action === "cancel" ? "取消" : action === "retry" ? "重试" : action === "rollback" ? "回滚" : "卸载"}请求。`);
  };

  const sendCapabilityMutation = (
    action: "install" | "activate" | "repair" | "uninstall",
    capability: CapabilityDescriptor,
  ) => {
    if (!channel) {
      setNotice("控制通道未连接，暂时不能执行操作。");
      return;
    }
    channel.send(buildCapabilityMutationMessage(action, capability.capability_id));
    setNotice(
      authorizationMode === "auto"
        ? "已按 Auto 模式直接提交；操作卡会保留授权审计状态。"
        : "请求已提交；若需要敏感权限，Simple Harness 会按 Manual 模式确认。",
    );
  };

  if (!open) return null;

  const isPage = variant === "page";

  const panel = (
      <section
        role={isPage ? "region" : "dialog"}
        aria-modal={isPage ? undefined : "true"}
        aria-label="能力中心"
        style={isPage ? pagePanelStyle : panelStyle}
      >
        <header style={headerStyle}>
          <div>
            <h2 style={{ margin: 0, fontSize: 18 }}>能力中心</h2>
            <p style={subtitleStyle}>
              查看可用能力、运行健康与安装/生成进度
            </p>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              onClick={onOpenLegacySkillStore}
              style={secondaryButtonStyle}
            >
              打开旧 Skill Store
            </button>
            {!isPage && onClose ? (
              <button
                type="button"
                aria-label="关闭能力中心"
                onClick={onClose}
                style={iconButtonStyle}
              >
                ×
              </button>
            ) : null}
          </div>
        </header>

        <div style={statusBarStyle}>
          <span>
            当前授权：{" "}
            <strong>
              {authorizationMode === "auto"
                ? "Auto（直接执行）"
                : "Manual（按需确认）"}
            </strong>
          </span>
          <span>
            进度来源：工作流推送
          </span>
          {!channel ? <span style={{ color: "#fbbf24" }}>离线快照</span> : null}
        </div>

        <nav aria-label="能力中心视图" style={tabsStyle}>
          <button
            type="button"
            aria-selected={tab === "capabilities"}
            onClick={() => setTab("capabilities")}
            style={tabButtonStyle(tab === "capabilities")}
          >
            能力 ({capabilities.length})
          </button>
          <button
            type="button"
            aria-selected={tab === "operations"}
            onClick={() => setTab("operations")}
            style={tabButtonStyle(tab === "operations")}
          >
            操作 ({operations.length})
          </button>
        </nav>

        {notice ? (
          <div role="status" style={noticeStyle}>
            <span>{notice}</span>
            <button
              type="button"
              aria-label="关闭提示"
              onClick={() => setNotice(null)}
              style={inlineDismissStyle}
            >
              ×
            </button>
          </div>
        ) : null}

        {tab === "capabilities" ? (
          <div style={bodyGridStyle}>
            <aside style={listPaneStyle}>
              <label style={fieldLabelStyle}>
                搜索
                <input
                  aria-label="搜索能力"
                  value={query}
                  onChange={(event) => {
                    const nextQuery = event.target.value;
                    setQuery(nextQuery);
                    setSelectedId(
                      capabilities.find((capability) =>
                        capabilityMatchesFilters(
                          capability,
                          category,
                          nextQuery,
                        ),
                      )?.capability_id ?? null,
                    );
                  }}
                  placeholder="名称、来源或 ID"
                  style={inputStyle}
                />
              </label>
              <label style={fieldLabelStyle}>
                类别
                <select
                  aria-label="能力类别"
                  value={category}
                  onChange={(event) => {
                    const nextCategory = event.target.value as CategoryFilter;
                    setCategory(nextCategory);
                    setSelectedId(
                      capabilities.find((capability) =>
                        capabilityMatchesFilters(
                          capability,
                          nextCategory,
                          query,
                        ),
                      )?.capability_id ?? null,
                    );
                  }}
                  style={inputStyle}
                >
                  <option value="all">全部</option>
                  <option value="instruction">说明</option>
                  <option value="tool">工具</option>
                  <option value="mcp">MCP</option>
                  <option value="pack">能力包</option>
                </select>
              </label>

              <div
                data-testid="capability-list"
                style={{
                  display: "grid",
                  // minmax(0,1fr)：隐式轨道默认以行内 nowrap 文本的
                  // min-content 为下限，min 尺寸(800×560)下会撑破列宽
                  // 出横向滚动条（WBUI-DEF-S03-01）
                  gridTemplateColumns: "minmax(0, 1fr)",
                  gap: 7,
                  overflowY: "auto",
                }}
              >
                {visibleCapabilities.map((capability) => (
                  <button
                    key={capability.capability_id}
                    type="button"
                    aria-pressed={selectedId === capability.capability_id}
                    onClick={() => setSelectedId(capability.capability_id)}
                    style={capabilityListItemStyle(
                      selectedId === capability.capability_id,
                    )}
                  >
                    <span style={{ minWidth: 0, textAlign: "left" }}>
                      <strong style={ellipsisStyle}>{capability.name}</strong>
                      <span style={listMetaStyle}>
                        {capability.version} · {capability.source.label}
                      </span>
                    </span>
                    <span
                      style={{
                        color: HEALTH_COLOR[capability.health],
                        fontSize: 11,
                      }}
                    >
                      {HEALTH_LABEL[capability.health]}
                    </span>
                  </button>
                ))}
                {visibleCapabilities.length === 0 ? (
                  <div style={emptyStyle}>没有符合筛选条件的能力。</div>
                ) : null}
              </div>
            </aside>

            <main style={detailPaneStyle}>
              {selected ? (
                <CapabilityDetail
                  capability={selected}
                  onMutation={sendCapabilityMutation}
                />
              ) : (
                <div style={emptyStyle}>选择一项能力查看详情。</div>
              )}
            </main>
          </div>
        ) : (
          <main
            data-testid="capability-operation-list"
            style={operationListStyle}
          >
            {operations.map((operation) => (
              <CapabilityOperationCard
                key={operation.operation_id}
                operation={operation}
                onAction={sendOperationAction}
              />
            ))}
            {operations.length === 0 ? (
              <div style={emptyStyle}>目前没有能力安装或生成操作。</div>
            ) : null}
          </main>
        )}
      </section>
  );

  if (isPage) return panel;

  return (
    <div
      role="presentation"
      style={backdropStyle}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose?.();
      }}
    >
      {panel}
    </div>
  );
}

function CapabilityDetail({
  capability,
  onMutation,
}: {
  capability: CapabilityDescriptor;
  onMutation: (
    action: "install" | "activate" | "repair" | "uninstall",
    capability: CapabilityDescriptor,
  ) => void;
}) {
  return (
    // 同 WBUI-DEF-S03-01：轨道钉死 minmax(0,1fr)，否则明细 dl 的
    // min-content(≈192px) 在 min 尺寸下顶破详情列，健康文案逐行硬裁
    <article
      data-testid="capability-detail"
      style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 14 }}
    >
      <header>
        <h3 style={{ margin: 0 }}>{capability.name}</h3>
        <p style={{ ...subtitleStyle, marginTop: 5 }}>
          {capability.description || "暂无说明"}
        </p>
      </header>
      <dl style={detailGridStyle}>
        <dt>类别</dt>
        <dd>
          {capability.categories.map((item) => CATEGORY_LABEL[item]).join("、")}
        </dd>
        <dt>版本</dt>
        <dd>{capability.version}</dd>
        <dt>来源</dt>
        <dd>{capability.source.label}</dd>
        <dt>Scope</dt>
        <dd>{capability.scope}</dd>
        <dt>健康</dt>
        <dd style={{ color: HEALTH_COLOR[capability.health] }}>
          {HEALTH_LABEL[capability.health]}
          {capability.health_summary ? ` · ${capability.health_summary}` : ""}
        </dd>
      </dl>
      {capability.manifest ? (
        <section
          data-testid="capability-manifest-detail"
          style={{
            display: "grid",
            gap: 10,
            padding: 12,
            borderRadius: 10,
            background: dark.inset,
            border: `1px solid ${dark.insetBorder}`,
          }}
        >
          <h4 style={{ margin: 0 }}>清单详情</h4>
          <dl style={detailGridStyle}>
            <dt>兼容 Simple Harness</dt>
            <dd>{capability.manifest.compatibility.deskpet}</dd>
            <dt>操作系统</dt>
            <dd>{capability.manifest.compatibility.os.join("、") || "未声明"}</dd>
            <dt>架构</dt>
            <dd>
              {capability.manifest.compatibility.architectures.join("、") ||
                "未声明"}
            </dd>
            <dt>Python</dt>
            <dd>{capability.manifest.compatibility.python}</dd>
            <dt>权限</dt>
            <dd>{capability.manifest.permissions.join("、") || "无"}</dd>
            <dt>Effect</dt>
            <dd>{capability.manifest.effects.join("、") || "无"}</dd>
          </dl>
          <div>
            <strong>Skills</strong>
            <div style={manifestValueStyle}>
              {capability.manifest.entries.skills
                .map((item) => item.path)
                .join("、") || "无"}
            </div>
          </div>
          <div>
            <strong>Tools</strong>
            {capability.manifest.entries.tools.length ? (
              <ul style={manifestListStyle}>
                {capability.manifest.entries.tools.map((tool) => (
                  <li key={`${tool.id}:${tool.provider_name}`}>
                    <code>{tool.provider_name}</code> · {tool.runtime} ·{" "}
                    {tool.execution_profile}
                    <div style={manifestValueStyle}>
                      入口 {tool.entry} · Schema {tool.schema} · 健康检查{" "}
                      {tool.healthcheck}
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <div style={manifestValueStyle}>无</div>
            )}
          </div>
          <div>
            <strong>MCP Servers</strong>
            <div style={manifestValueStyle}>
              {capability.manifest.entries.mcp_servers
                .map((item) => `${item.id} (${item.config_ref})`)
                .join("、") || "无"}
            </div>
          </div>
          <div>
            <strong>依赖</strong>
            <div style={manifestValueStyle}>
              Python：
              {capability.manifest.dependencies.python.join("、") || "无"}
              <br />
              命令：
              {capability.manifest.dependencies.commands
                .map((item) => `${item.name} ${item.version}`)
                .join("、") || "无"}
            </div>
          </div>
          <details>
            <summary style={{ cursor: "pointer", fontWeight: 600 }}>
              文件哈希 ({capability.manifest.files.length})
            </summary>
            <ul style={manifestListStyle}>
              {capability.manifest.files.map((file) => (
                <li key={file.path}>
                  <div>{file.path}</div>
                  <code style={{ overflowWrap: "anywhere" }}>{file.sha256}</code>
                </li>
              ))}
            </ul>
          </details>
          <div style={manifestValueStyle}>
            卸载：停止服务{" "}
            {capability.manifest.uninstall.stop_servers ? "是" : "否"}；
            无引用时移除环境{" "}
            {capability.manifest.uninstall
              .remove_environment_when_unreferenced
              ? "是"
              : "否"}
          </div>
          <div style={manifestValueStyle}>
            Manifest SHA-256：
            <code style={{ overflowWrap: "anywhere" }}>
              {capability.manifest.manifest_hash}
            </code>
          </div>
        </section>
      ) : null}
      {capability.available_actions?.length ? (
        <footer style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {capability.available_actions.map((action) => (
            <button
              key={action}
              type="button"
              onClick={() => onMutation(action, capability)}
              style={{
                ...secondaryButtonStyle,
                color: action === "uninstall" ? "#fca5a5" : dark.text,
              }}
            >
              {action === "install"
                ? "安装"
                : action === "activate"
                  ? "启用"
                  : action === "repair"
                    ? "修复"
                    : "卸载"}
            </button>
          ))}
        </footer>
      ) : null}
    </article>
  );
}

const backdropStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: 9997,
  display: "grid",
  placeItems: "center",
  padding: 20,
  background: "rgba(15,23,42,0.48)",
  backdropFilter: "blur(3px)",
};
const panelStyle: CSSProperties = {
  width: "min(920px, 96vw)",
  height: "min(680px, 92vh)",
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
  border: `1px solid ${dark.borderStrong}`,
  borderRadius: 14,
  background: dark.bg,
  color: dark.text,
  boxShadow: "0 24px 70px rgba(0,0,0,0.48)",
};
/**
 * page variant（D5 宿主模式）：无 fixed/backdrop，flex 填满工作台内容区；
 * 背景交给 WorkbenchShell（dark.bgSolid），零新增硬编码色值（T15）。
 */
const pagePanelStyle: CSSProperties = {
  flex: 1,
  width: "100%",
  height: "100%",
  minWidth: 0,
  minHeight: 0,
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
  background: "transparent",
  color: dark.text,
};
const headerStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "space-between",
  gap: 12,
  padding: "17px 18px 12px",
};
const subtitleStyle: CSSProperties = {
  margin: "3px 0 0",
  color: dark.textMuted,
  fontSize: 12,
  lineHeight: 1.5,
};
const statusBarStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  justifyContent: "space-between",
  gap: 8,
  padding: "8px 18px",
  borderTop: `1px solid ${dark.hairline}`,
  borderBottom: `1px solid ${dark.hairline}`,
  background: dark.inset,
  color: dark.textMuted,
  fontSize: 11,
};
const tabsStyle: CSSProperties = {
  display: "flex",
  gap: 4,
  padding: "9px 18px 0",
};
const tabButtonStyle = (active: boolean): CSSProperties => ({
  border: 0,
  borderBottom: active ? "2px solid #2563eb" : "2px solid transparent",
  padding: "7px 10px",
  background: "transparent",
  color: active ? "#67e8f9" : dark.textMuted,
  cursor: "pointer",
  fontWeight: active ? 700 : 500,
});
const noticeStyle: CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  gap: 8,
  margin: "8px 18px 0",
  padding: "7px 9px",
  border: "1px solid rgba(96,165,250,0.30)",
  borderRadius: 7,
  background: "rgba(37,99,235,0.14)",
  color: "#bfdbfe",
  fontSize: 11,
};
const bodyGridStyle: CSSProperties = {
  minHeight: 0,
  flex: 1,
  display: "grid",
  // 左列下限 200px：250px 会在 min 窗口(800×560)把详情值列挤到 ~47px，
  // CJK/长词被逐字断行。列表行自带 ellipsis，200px 仍可读。
  gridTemplateColumns: "minmax(200px, 0.38fr) minmax(0, 0.62fr)",
  gap: 0,
  marginTop: 8,
  borderTop: `1px solid ${dark.hairline}`,
};
const listPaneStyle: CSSProperties = {
  minHeight: 0,
  display: "flex",
  flexDirection: "column",
  gap: 10,
  padding: 14,
  borderRight: `1px solid ${dark.hairline}`,
  background: dark.inset,
};
const detailPaneStyle: CSSProperties = {
  minWidth: 0,
  minHeight: 0,
  overflowY: "auto",
  padding: 20,
  background: "rgba(5,8,15,0.24)",
};
const operationListStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  display: "grid",
  // 与能力列表同类：钉死轨道下限，防操作卡片长 token 在 min 尺寸下撑宽
  gridTemplateColumns: "minmax(0, 1fr)",
  alignContent: "start",
  gap: 10,
  overflowY: "auto",
  padding: 18,
  marginTop: 8,
  borderTop: `1px solid ${dark.hairline}`,
};
const fieldLabelStyle: CSSProperties = {
  display: "grid",
  gap: 4,
  color: dark.textMuted,
  fontSize: 11,
  fontWeight: 600,
};
const inputStyle: CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  border: `1px solid ${dark.border}`,
  borderRadius: 7,
  padding: "7px 8px",
  background: dark.card,
  color: dark.text,
  font: "inherit",
};
const capabilityListItemStyle = (active: boolean): CSSProperties => ({
  width: "100%",
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  padding: "9px 10px",
  border: active ? "1px solid #60a5fa" : `1px solid ${dark.cardBorder}`,
  borderRadius: 8,
  background: active ? "rgba(37,99,235,0.18)" : dark.card,
  color: dark.text,
  cursor: "pointer",
});
const ellipsisStyle: CSSProperties = {
  display: "block",
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  fontSize: 12,
};
const listMetaStyle: CSSProperties = {
  display: "block",
  marginTop: 2,
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
  color: dark.textMuted,
  fontSize: 10,
};
const detailGridStyle: CSSProperties = {
  display: "grid",
  // 1fr 的隐式 min 是 auto（= 值列最长单词宽）；min 尺寸下改用
  // minmax(0,1fr) 让值列可收窄，配合 overflowWrap 断长 token
  gridTemplateColumns: "72px minmax(0, 1fr)",
  gap: "9px 12px",
  margin: 0,
  fontSize: 12,
  overflowWrap: "anywhere",
};
const manifestValueStyle: CSSProperties = {
  marginTop: 3,
  color: dark.textMuted,
  fontSize: 11,
  lineHeight: 1.55,
  overflowWrap: "anywhere",
};
const manifestListStyle: CSSProperties = {
  display: "grid",
  gap: 6,
  margin: "5px 0 0",
  paddingLeft: 20,
  color: dark.textMuted,
  fontSize: 11,
  lineHeight: 1.5,
};
const emptyStyle: CSSProperties = {
  padding: 18,
  border: `1px dashed ${dark.borderStrong}`,
  borderRadius: 8,
  color: dark.textFaint,
  textAlign: "center",
  fontSize: 12,
};
const secondaryButtonStyle: CSSProperties = {
  border: `1px solid ${dark.border}`,
  borderRadius: 7,
  padding: "6px 9px",
  background: dark.card,
  color: dark.text,
  cursor: "pointer",
  fontSize: 11,
};
const iconButtonStyle: CSSProperties = {
  width: 30,
  height: 30,
  border: `1px solid ${dark.border}`,
  borderRadius: 7,
  background: dark.card,
  color: dark.textMuted,
  cursor: "pointer",
  fontSize: 20,
  lineHeight: 1,
};
const inlineDismissStyle: CSSProperties = {
  border: 0,
  padding: 0,
  background: "transparent",
  color: "inherit",
  cursor: "pointer",
};
