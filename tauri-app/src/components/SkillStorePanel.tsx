// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * P4-S20 Stage C — SkillStorePanel (UI-revamp)
 *
 * Three tabs: Installed / Marketplace / Add by URL.
 * 全部样式走 design tokens；列表卡片化 + 错误分级 (info / warn / error)。
 *
 * 后端 IPC：
 *   skill_marketplace_list / skill_marketplace_list_response
 *   skill_list_installed / skill_list_installed_response
 *   skill_install_from_url / skill_install_pending
 *   skill_install_confirm / skill_install_confirm_response
 *   skill_uninstall / skill_uninstall_response
 *
 * 规格：openspec/specs/skill-marketplace/spec.md
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useDepsChanged } from "../hooks/useDepsChanged";

import { Icon, type IconName } from "./Icon";
import type { ControlChannel } from "../ws/ControlChannel";
import type { MarketplaceSkill, SkillMeta, PermissionCategory } from "../types/skillPlatform";
import {
  badgeStyle,
  bannerStyle,
  buttonStyle,
  dark,
  surfaceModal,
  tabStyle,
} from "../theme/components";
import { tokens } from "../theme/tokens";
import { useConfirm } from "./useConfirm";
import "./SkillStorePanel.styles.css";

type Tab = "installed" | "marketplace" | "add-url";

type AlertLevel = "info" | "warning" | "error";
interface AlertState {
  level: AlertLevel;
  message: string;
}

/** 本面板只消费 send/onMessage —— 收窄类型以便页面宿主（SkillsView）传入
 * 与 CapabilityCenterPanel 相同的 Pick 通道。既有全量 ControlChannel
 * 调用点仍然兼容（结构子类型）。 */
type SkillStoreChannel = Pick<ControlChannel, "send" | "onMessage">;

interface Props {
  open: boolean;
  channel: SkillStoreChannel | null;
  /**
   * 渲染形态（D5 宿主模式）：
   * - "overlay"（默认）：fixed backdrop + 居中 modal（既有行为）；
   * - "page"：工作台视图内嵌 —— 去 backdrop/fixed，填满内容区
   *   （SkillsView 宿主，WB-6）。page 模式无关闭钮，onClose 可省略。
   */
  variant?: "overlay" | "page";
  onClose?: () => void;
  /** Optional bridge back to the default Capability Center. */
  onOpenCapabilityCenter?: () => void;
}

interface StagedSkillMember {
  name: string;
  description?: string;
  manifest?: Record<string, unknown>;
  permission_categories: PermissionCategory[];
  manifest_hash?: string;
  content_hash?: string;
}

interface StagedSkill {
  intent_id: string;
  digest: string;
  decision_nonce: string;
  decision_version: number;
  repository_url: string;
  resolved_commit: string;
  expires_at?: string | number;
  project: {
    project_id: string;
    project_name?: string;
    project_revision: number;
    project_identity?: string;
  };
  members: StagedSkillMember[];
}

const errorMessage = (payload: Record<string, unknown>, fallback: string) => {
  const error = payload.error;
  if (error && typeof error === "object") {
    const structured = error as { code?: unknown; message?: unknown };
    const message = String(structured.message || fallback);
    return structured.code ? `${String(structured.code)}：${message}` : message;
  }
  return String(payload.message || error || payload.reason || fallback);
};

const SENSITIVE_CATS: PermissionCategory[] = ["shell", "skill_install", "read_file_sensitive"];

const TAB_LABEL: Record<Tab, string> = {
  installed: "已安装",
  marketplace: "市场",
  "add-url": "通过 URL 安装",
};

export const SkillStorePanel: React.FC<Props> = ({
  open,
  channel,
  variant = "overlay",
  onClose,
  onOpenCapabilityCenter,
}) => {
  const [tab, setTab] = useState<Tab>("installed");
  const [installed, setInstalled] = useState<SkillMeta[]>([]);
  const [marketplace, setMarketplace] = useState<MarketplaceSkill[]>([]);
  const [alert, setAlert] = useState<AlertState | null>(null);
  const [urlInput, setUrlInput] = useState("");
  const [staged, setStaged] = useState<StagedSkill | null>(null);
  // 打开时（初始页签是"已安装"）立即读列表，所以首次渲染就是加载中。
  const [loading, setLoading] = useState(() => Boolean(open && channel));

  // 监听 backend response
  useEffect(() => {
    if (!open || !channel) return undefined;
    const off = channel.onMessage((msg) => {
      const t = (msg as { type?: string }).type;
      const payload = (msg as { payload?: Record<string, unknown> }).payload || {};
      if (t === "skill_marketplace_list_response") {
        setMarketplace((payload.skills as MarketplaceSkill[]) || []);
        if (payload.error) {
          const raw = String(payload.error);
          // 404 = registry 还没发布 — info 级，不要红
          if (raw.includes("404")) {
            setAlert({
              level: "info",
              message: "官方注册表暂未发布。请使用「通过 URL 安装」直接安装 GitHub 上的技能。",
            });
          } else {
            setAlert({
              level: "warning",
              message: `注册表加载失败：${raw}`,
            });
          }
        }
        setLoading(false);
      } else if (t === "skill_list_installed_response") {
        setInstalled((payload.skills as SkillMeta[]) || []);
        if (payload.error) {
          setAlert({ level: "warning", message: String(payload.error) });
        }
        setLoading(false);
      } else if (t === "skill_install_pending") {
        setLoading(false);
        if (payload.ok) {
          const members = (payload.members as StagedSkillMember[]) || [];
          const intentId = String(payload.intent_id || payload.staging_id || "");
          const digest = String(payload.digest || payload.list_digest || "");
          const decisionNonce = String(payload.decision_nonce || "");
          const decisionVersion = Number(payload.decision_version || 0);
          if (
            !intentId ||
            !digest ||
            !decisionNonce ||
            decisionVersion < 1 ||
            members.length === 0
          ) {
            setAlert({
              level: "error",
              message:
                "skill_install_contract_invalid：安装清单缺少 intent、digest 或成员，未创建确认卡。",
            });
            setStaged(null);
            return;
          }
          setStaged({
            intent_id: intentId,
            digest,
            decision_nonce: decisionNonce,
            decision_version: decisionVersion,
            repository_url: String(payload.repository_url || payload.url || ""),
            resolved_commit: String(payload.resolved_commit || ""),
            expires_at: payload.expires_at as string | number | undefined,
            project: (payload.project as StagedSkill["project"]) || {
              project_id: "",
              project_revision: 0,
            },
            members,
          });
        } else {
          setAlert({
            level: "error",
            message: errorMessage(payload, "安装准备失败"),
          });
        }
      } else if (t === "skill_install_confirm_response") {
        setLoading(false);
        const status = String(payload.status || "");
        const runtimeVerified = payload.runtime_verified === true;
        if (payload.ok && status === "succeeded" && runtimeVerified) {
          setStaged(null);
          setUrlInput("");
          setAlert({
            level: "info",
            message: `安装成功：${String(payload.installed_count || payload.member_count || 0)} 个 Skill 已通过新 Run 验证`,
          });
          channel.send({ type: "skill_list_installed" });
          setTab("installed");
        } else if (payload.ok && status === "denied") {
          setStaged(null);
          setAlert({ level: "info", message: "已取消安装，全局 Skill catalog 未改变。" });
        } else if (payload.ok) {
          setAlert({
            level: "warning",
            message: `安装尚未完成（${status || "处理中"}），通过 runtime 验证前不会显示成功。`,
          });
          const pendingIntentId = String(payload.intent_id || "");
          if (pendingIntentId) {
            channel.send({
              type: "skill_install_status",
              payload: { intent_id: pendingIntentId },
            });
          }
        } else {
          setAlert({
            level: "error",
            message: errorMessage(payload, "安装失败"),
          });
        }
      } else if (t === "skill_install_status_response") {
        setLoading(false);
        const status = String(payload.status || "");
        if (payload.ok && status === "succeeded" && payload.runtime_verified === true) {
          setStaged(null);
          setUrlInput("");
          setAlert({ level: "info", message: "安装成功：所有会话的新 Run 均可使用这些 Skill。" });
          channel.send({ type: "skill_list_installed" });
          setTab("installed");
        } else if (!payload.ok) {
          setAlert({ level: "error", message: errorMessage(payload, "状态查询失败") });
        } else {
          setAlert({ level: "warning", message: `安装状态：${status || "处理中"}` });
        }
      } else if (t === "skill_uninstall_response") {
        setLoading(false);
        if (payload.ok) {
          channel.send({ type: "skill_list_installed" });
          setAlert({
            level: "info",
            message: `已卸载：${payload.name as string}`,
          });
        } else {
          setAlert({
            level: "error",
            message: String(payload.error || "卸载失败"),
          });
        }
      }
    });
    return () => off();
  }, [open, channel]);

  // tab 切换 / 打开时刷新
  // 渲染期：切换后清提示、按页签设加载中；effect 只发请求。
  if (useDepsChanged([tab, open, channel]) && open && channel) {
    setAlert(null);
    setLoading(tab === "installed" || tab === "marketplace");
  }
  useEffect(() => {
    if (!open || !channel) return;
    if (tab === "installed") {
      channel.send({ type: "skill_list_installed" });
    } else if (tab === "marketplace") {
      channel.send({ type: "skill_marketplace_list" });
    }
  }, [tab, open, channel]);

  const installFromUrl = useCallback(
    (url: string) => {
      if (!channel || !url.trim()) return;
      setAlert(null);
      setLoading(true);
      channel.send({
        type: "skill_install_from_url",
        payload: { url: url.trim() },
      });
    },
    [channel],
  );

  const confirmInstall = useCallback(
    (approve: boolean) => {
      if (!channel || !staged) return;
      setLoading(true);
      channel.send({
        type: "skill_install_confirm",
        payload: {
          intent_id: staged.intent_id,
          digest: staged.digest,
          decision_nonce: staged.decision_nonce,
          decision_version: staged.decision_version,
          decision: approve ? "approve" : "deny",
        },
      });
    },
    [channel, staged],
  );

  const [confirmDialog, ask] = useConfirm();
  const uninstall = useCallback(
    async (skill: SkillMeta) => {
      if (!channel) return;
      const name = skill.name;
      // 2026-09-25：不能用 confirm()——桌面 WebView 里它不弹框、直接当"确认"。
      if (
        !(await ask({
          title: "卸载 Skill",
          message: `确认卸载 “${name}” 吗？`,
          confirm_label: "卸载",
        }))
      )
        return;
      setLoading(true);
      channel.send({
        type: "skill_uninstall",
        payload: { name, capability_id: skill.capability_id ?? name },
      });
    },
    [channel, ask],
  );

  const sensitiveBadges = useMemo(() => {
    if (!staged) return [];
    return Array.from(
      new Set(staged.members.flatMap((member) => member.permission_categories || [])),
    ).filter((c) => SENSITIVE_CATS.includes(c));
  }, [staged]);

  if (!open) return null;

  const isPage = variant === "page";

  const container = (
    <div
      role={isPage ? "region" : undefined}
      aria-label={isPage ? "技能商店" : undefined}
      style={
        isPage
          ? pageContainerStyle
          : {
              ...surfaceModal,
              width: 720,
              maxWidth: "94vw",
              maxHeight: "86vh",
              display: "flex",
              flexDirection: "column",
              animation: `bp-pop-in ${tokens.duration.base}ms ${tokens.easing.out}`,
              position: "relative",
            }
      }
      onClick={isPage ? undefined : (e) => e.stopPropagation()}
    >
      {/* Header */}
      <div className="sh-SkillStorePanel-1">
        <div className="sh-SkillStorePanel-2">
          <span className="sh-SkillStorePanel-3">
            <Icon name="store" size={20} />
          </span>
          <div>
            <div className="sh-SkillStorePanel-4">SkillStore</div>
            <h2 id="skill-store-title" className="sh-SkillStorePanel-5">
              技能商店
            </h2>
          </div>
        </div>
        {onOpenCapabilityCenter ? (
          <button type="button" onClick={onOpenCapabilityCenter} className="sh-SkillStorePanel-6">
            返回能力中心
          </button>
        ) : null}
        {!isPage && onClose ? (
          <button
            type="button"
            onClick={onClose}
            className="sh-SkillStorePanel-7"
            aria-label="关闭"
          >
            <Icon name="close" size={16} />
          </button>
        ) : null}
      </div>

      {/* Tabs */}
      <div className="sh-SkillStorePanel-8">
        {(Object.keys(TAB_LABEL) as Tab[]).map((t) => {
          const active = tab === t;
          return (
            <button
              key={t}
              type="button"
              className={`bp-tab ${active ? "bp-tab-active" : ""}`}
              onClick={() => setTab(t)}
              style={tabStyle(active)}
            >
              {TAB_LABEL[t]}
            </button>
          );
        })}
      </div>

      {/* Alert */}
      {alert && (
        <div className="sh-SkillStorePanel-9">
          <div
            style={{
              ...bannerStyle(
                alert.level === "warning" ? "warning" : alert.level === "error" ? "error" : "info",
              ),
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: tokens.space.sm,
            }}
            role={alert.level === "error" ? "alert" : "status"}
          >
            <span>{alert.message}</span>
            <button
              type="button"
              className="bp-btn-ghost sh-SkillStorePanel-10"
              onClick={() => setAlert(null)}
              aria-label="关闭提示"
            >
              <Icon name="close" size={14} />
            </button>
          </div>
        </div>
      )}

      {/* Body */}
      <div className="sh-SkillStorePanel-11">
        {tab === "installed" && (
          <InstalledList skills={installed} loading={loading} onUninstall={uninstall} />
        )}
        {tab === "marketplace" && (
          <MarketplaceList
            skills={marketplace}
            loading={loading}
            onInstall={(s) => installFromUrl(s.source_url)}
          />
        )}
        {tab === "add-url" && (
          <AddByUrl
            value={urlInput}
            onChange={setUrlInput}
            onSubmit={() => installFromUrl(urlInput)}
            disabled={loading}
          />
        )}
      </div>

      {staged && (
        <ConfirmModal
          staged={staged}
          sensitive={sensitiveBadges}
          onApprove={() => confirmInstall(true)}
          onCancel={() => confirmInstall(false)}
        />
      )}
      {confirmDialog}
    </div>
  );

  if (isPage) return container;

  return (
    <div
      className="sh-SkillStorePanel-12"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="skill-store-title"
    >
      {container}
    </div>
  );
};

/**
 * page variant（D5 宿主模式）：无 fixed/backdrop，flex 填满工作台内容区。
 * position:relative 保留 —— ConfirmModal 以 absolute inset 0 覆盖本容器。
 * 背景交给 WorkbenchShell（dark.bgSolid），零新增硬编码色值（T15）。
 */
const pageContainerStyle: React.CSSProperties = {
  flex: 1,
  width: "100%",
  height: "100%",
  minWidth: 0,
  minHeight: 0,
  display: "flex",
  flexDirection: "column",
  overflow: "hidden",
  position: "relative",
  background: "transparent",
  color: dark.text,
  fontFamily: tokens.font.ui,
};

// ---------- Installed list ----------

const EmptyState: React.FC<{ icon?: IconName; title: string; hint?: string }> = ({
  icon,
  title,
  hint,
}) => (
  <div className="sh-SkillStorePanel-13">
    {icon && (
      <div className="sh-SkillStorePanel-14">
        <Icon name={icon} size={26} />
      </div>
    )}
    <div className="sh-SkillStorePanel-15">{title}</div>
    {hint && <div className="sh-SkillStorePanel-16">{hint}</div>}
  </div>
);

const LoadingState: React.FC<{ label?: string }> = ({ label = "加载中…" }) => (
  <div className="sh-SkillStorePanel-17">
    <span className="bp-spinner sh-SkillStorePanel-18" />
    {label}
  </div>
);

const InstalledList: React.FC<{
  skills: SkillMeta[];
  loading: boolean;
  onUninstall: (skill: SkillMeta) => void;
}> = ({ skills, loading, onUninstall }) => {
  if (loading) return <LoadingState />;
  if (!skills.length)
    return (
      <EmptyState
        icon="archive"
        title="暂无已安装技能"
        hint="切换到「市场」或「通过 URL 安装」试试"
      />
    );
  return (
    <div className="sh-SkillStorePanel-19">
      {skills.map((s) => (
        <div key={s.name} className="bp-card sh-SkillStorePanel-20">
          <div className="sh-SkillStorePanel-21">
            <div className="sh-SkillStorePanel-22">
              {s.name}
              {s.version && <span className="sh-SkillStorePanel-23">v{s.version}</span>}
            </div>
            {s.description && <div className="sh-SkillStorePanel-24">{s.description}</div>}
          </div>
          {s.scope === "builtin" ? (
            <span className="sh-SkillStorePanel-25">内置</span>
          ) : (
            <button
              type="button"
              className="bp-btn-secondary sh-SkillStorePanel-26"
              onClick={() => onUninstall(s)}
            >
              卸载
            </button>
          )}
        </div>
      ))}
    </div>
  );
};

// ---------- Marketplace list ----------

const MarketplaceList: React.FC<{
  skills: MarketplaceSkill[];
  loading: boolean;
  onInstall: (s: MarketplaceSkill) => void;
}> = ({ skills, loading, onInstall }) => {
  if (loading) return <LoadingState />;
  if (!skills.length)
    return (
      <EmptyState
        icon="store"
        title="暂无可用技能"
        hint="官方注册表暂未发布，可使用「通过 URL 安装」"
      />
    );
  return (
    <div className="sh-SkillStorePanel-19">
      {skills.map((s) => (
        <div key={s.source_url} className="bp-card sh-SkillStorePanel-27">
          <div className="sh-SkillStorePanel-21">
            <div className="sh-SkillStorePanel-28">
              {s.name}
              {s.author && <span className="sh-SkillStorePanel-29">· {s.author}</span>}
            </div>
            {/* Marketplace cards: clamp to 4 lines so a long description
                (e.g. docx skill ships ~1500 chars of "Triggers include …
                Word doc / .docx / professional documents …") doesn't
                expand the card to fill the entire scroll viewport,
                hiding the rest of the list. InstalledList already does
                this at line ~510. Keep both in sync. */}
            <div className="sh-SkillStorePanel-30" title={s.description}>
              {s.description}
            </div>
            <div className="sh-SkillStorePanel-31">{s.source_url}</div>
            {(s.permission_categories || []).length > 0 && (
              <div className="sh-SkillStorePanel-32">
                {(s.permission_categories || []).map((c) => (
                  <span
                    key={c}
                    style={badgeStyle(SENSITIVE_CATS.includes(c) ? "error" : "warning")}
                  >
                    {c}
                  </span>
                ))}
              </div>
            )}
          </div>
          <button
            type="button"
            className="bp-btn-primary sh-SkillStorePanel-33"
            onClick={() => onInstall(s)}
          >
            安装
          </button>
        </div>
      ))}
    </div>
  );
};

// ---------- Add by URL ----------

const AddByUrl: React.FC<{
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  disabled: boolean;
}> = ({ value, onChange, onSubmit, disabled }) => (
  <div>
    <div className="sh-SkillStorePanel-34">
      支持以下 GitHub 形式：
      <ul className="sh-SkillStorePanel-35">
        <li>github:owner/repo</li>
        <li>github:owner/repo/tree/branch/subpath</li>
        <li>https://github.com/owner/repo</li>
      </ul>
    </div>
    <input
      className="bp-input sh-SkillStorePanel-36"
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder="github:anthropics/skills/tree/main/skills/algorithmic-art"
      onKeyDown={(e) => {
        if (e.key === "Enter" && !disabled && value.trim()) {
          onSubmit();
        }
      }}
    />
    <div className="sh-SkillStorePanel-37">
      <button
        type="button"
        className="bp-btn-primary"
        disabled={disabled || !value.trim()}
        onClick={onSubmit}
        style={buttonStyle("primary", "md", disabled || !value.trim())}
      >
        {disabled ? <span className="bp-spinner" /> : "安装"}
      </button>
    </div>
  </div>
);

// ---------- Confirm modal ----------

const ConfirmModal: React.FC<{
  staged: StagedSkill;
  sensitive: PermissionCategory[];
  onApprove: () => void;
  onCancel: () => void;
}> = ({ staged, sensitive, onApprove, onCancel }) => {
  const dangerous = sensitive.length > 0;
  return (
    <div className="sh-SkillStorePanel-38">
      <div
        style={{
          ...surfaceModal,
          width: 480,
          maxWidth: "92%",
          borderTop: `4px solid ${dangerous ? tokens.color.danger.bg : tokens.color.accent.bg}`,
          padding: tokens.space.lg,
          animation: `bp-pop-in ${tokens.duration.base}ms ${tokens.easing.out}`,
        }}
      >
        <div className="sh-SkillStorePanel-39">确认安装</div>
        <h3 className="sh-SkillStorePanel-40">全局安装 {staged.members.length} 个 Skill</h3>
        <p className="sh-SkillStorePanel-41">
          安装后可在所有会话中使用；实际工具执行仍遵循权限与可用性策略。
        </p>
        <div className="sh-SkillStorePanel-42">
          <div>{staged.repository_url}</div>
          <div>commit: {staged.resolved_commit || "(未提供)"}</div>
          <div>digest: {staged.digest || "(未提供)"}</div>
          {staged.expires_at ? <div>expires: {String(staged.expires_at)}</div> : null}
        </div>
        <div aria-label="待安装 Skill 清单" className="sh-SkillStorePanel-43">
          {staged.members.map((member) => (
            <div
              key={`${member.name}:${member.manifest_hash || member.content_hash || ""}`}
              className="sh-SkillStorePanel-44"
            >
              <strong>{member.name}</strong>
              {member.description ? (
                <div className="sh-SkillStorePanel-45">{member.description}</div>
              ) : null}
              {(member.permission_categories || []).length ? (
                <div className="sh-SkillStorePanel-46">
                  {member.permission_categories.map((category) => (
                    <span
                      key={category}
                      style={badgeStyle(SENSITIVE_CATS.includes(category) ? "error" : "warning")}
                    >
                      {category}
                    </span>
                  ))}
                </div>
              ) : null}
            </div>
          ))}
        </div>
        {dangerous && (
          <div className="sh-SkillStorePanel-47">
            <Icon name="alert" size={15} style={{ marginTop: 1, flexShrink: 0 }} />
            <span>
              该技能请求敏感权限：
              <strong className="sh-SkillStorePanel-48">{sensitive.join(", ")}</strong>
              。请确认来源可信。
            </span>
          </div>
        )}
        <details className="sh-SkillStorePanel-49">
          <summary className="sh-SkillStorePanel-50">查看冻结安装摘要</summary>
          <pre className="sh-SkillStorePanel-51">{JSON.stringify(staged, null, 2)}</pre>
        </details>
        <div className="sh-SkillStorePanel-52">
          <button
            type="button"
            className="bp-btn-secondary sh-SkillStorePanel-53"
            onClick={onCancel}
          >
            取消
          </button>
          <button
            type="button"
            className={dangerous ? "bp-btn-danger" : "bp-btn-primary"}
            onClick={onApprove}
            style={buttonStyle(dangerous ? "danger" : "primary", "md")}
          >
            {dangerous ? "我明白风险，继续安装" : "确认安装"}
          </button>
        </div>
      </div>
    </div>
  );
};

export default SkillStorePanel;
