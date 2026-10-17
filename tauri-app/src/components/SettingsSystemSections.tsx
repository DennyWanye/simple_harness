// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { useConfirm } from "./useConfirm";
import { formatUpdaterError } from "./updaterError";
import type { IncomingMessage } from "../types/messages";
import type { ControlChannel } from "../ws/ControlChannel";
import {
  clampChatTurnTimeoutMinutes,
  buildChatTurnTimeoutGetMessage,
  buildChatTurnTimeoutSetMessage,
  buildAutoModeGetMessage,
  buildAutoModeSetMessage,
  readAutoModePolicy,
  withNextLaunchDataDirPreference,
  isDataDirPreferenceNoop,
  CHAT_TURN_TIMEOUT_MAX_MINUTES,
  CHAT_TURN_TIMEOUT_MIN_MINUTES,
  formatMb,
} from "./settingsMessages";
import type { AuthorizationPolicyView, DataDirSetting } from "./settingsMessages";
import "./SettingsSystemSections.styles.css";

// ----------------------------------------------------------------------
// 关于与更新 (2026-06-05) — 手动"检查更新"入口。
//
// 底层走 tauri-plugin-updater：check() 拉 latest.json 比版本号；有新版本
// 就 downloadAndInstall() 下载验签安装。Windows 下安装步骤执行时 app 会被
// 安装器自动关闭（官方限制），装完拉起新版本，所以这里不需要手动 relaunch。
//
// 启动时的静默检查在 hooks/useUpdateChecker.ts；本组件是用户主动点的入口。
// 两者用的是同一套 updater 配置（tauri.conf.json > plugins.updater）。
// 错误文案翻译在 ./updaterError（抽出来便于纯函数单测）。
// ----------------------------------------------------------------------

type UpdatePhase = "idle" | "checking" | "latest" | "available" | "downloading" | "done" | "error";

export function UpdateSection() {
  const [version, setVersion] = useState<string>("");
  const [phase, setPhase] = useState<UpdatePhase>("idle");
  const [errMsg, setErrMsg] = useState<string>("");
  const [newVersion, setNewVersion] = useState<string>("");
  const [notes, setNotes] = useState<string>("");
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  // check() 返回的 Update 对象，留到用户点"下载并安装"时用。
  // 用 any 是因为只在 Tauri 运行时存在，避免给非 Tauri 预览构建引类型依赖。
  const [pending, setPending] = useState<{
    downloadAndInstall: (cb?: (e: unknown) => void) => Promise<void>;
  } | null>(null);

  // 读当前版本号展示（来自 tauri.conf.json 的 version）。
  useEffect(() => {
    let alive = true;
    import("@tauri-apps/api/app")
      .then((m) => m.getVersion())
      .then((v) => {
        if (alive) setVersion(v);
      })
      .catch(() => {
        /* 非 Tauri 预览构建 / 读不到版本 —— 不显示即可 */
      });
    return () => {
      alive = false;
    };
  }, []);

  const onCheck = useCallback(async () => {
    setPhase("checking");
    setErrMsg("");
    try {
      const mod = await import("@tauri-apps/plugin-updater").catch(() => null);
      if (!mod) {
        // 预览构建无 Tauri —— 直接当作"已是最新"，避免误报错误。
        setPhase("latest");
        return;
      }
      const update = await mod.check();
      if (!update) {
        setPhase("latest");
        return;
      }
      setNewVersion(update.version);
      setNotes(update.body ?? "");
      setPending(update as unknown as typeof pending);
      setPhase("available");
    } catch (err) {
      console.info("[updater] manual check failed:", err);
      setErrMsg(formatUpdaterError(err));
      setPhase("error");
    }
  }, []);

  const onInstall = useCallback(async () => {
    if (!pending) return;
    setPhase("downloading");
    setProgress({ done: 0, total: 0 });
    try {
      let total = 0;
      let done = 0;
      await pending.downloadAndInstall((e: unknown) => {
        const ev = e as { event?: string; data?: { contentLength?: number; chunkLength?: number } };
        if (ev.event === "Started") {
          total = ev.data?.contentLength ?? 0;
          setProgress({ done: 0, total });
        } else if (ev.event === "Progress") {
          done += ev.data?.chunkLength ?? 0;
          setProgress({ done, total });
        } else if (ev.event === "Finished") {
          setProgress({ done: total, total });
        }
      });
      // Windows 上一般走不到这里：安装步骤会自动退出 app。
      setPhase("done");
    } catch (err) {
      console.info("[updater] download/install failed:", err);
      setErrMsg(formatUpdaterError(err));
      setPhase("error");
    }
  }, [pending]);

  const pct =
    progress && progress.total > 0
      ? Math.min(100, Math.round((progress.done / progress.total) * 100))
      : null;

  return (
    <section className="sh-SettingsSystemSections-1">
      <h3 className="sh-SettingsSystemSections-2">关于与更新</h3>
      <div className="sh-SettingsSystemSections-3">
        当前版本：<strong>{version ? `v${version}` : "—"}</strong>
      </div>

      {phase === "latest" && <div className="sh-SettingsSystemSections-3">✅ 已是最新版本。</div>}

      {phase === "available" && (
        <div className="sh-SettingsSystemSections-3">
          🎉 发现新版本 <strong>v{newVersion}</strong>
          {notes ? <div className="sh-SettingsSystemSections-4">{notes}</div> : null}
        </div>
      )}

      {phase === "downloading" && (
        <div className="sh-SettingsSystemSections-3">
          正在下载并安装{pct !== null ? ` … ${pct}%` : "…"}
          <div className="sh-SettingsSystemSections-5">
            安装时 Simple Harness 会自动关闭，请稍候它重新启动。
          </div>
        </div>
      )}

      {phase === "done" && (
        <div className="sh-SettingsSystemSections-3">✅ 更新已安装，重启后生效。</div>
      )}

      {phase === "error" && <div className="sh-SettingsSystemSections-6">{errMsg}</div>}

      <div className="sh-SettingsSystemSections-7">
        {phase === "available" ? (
          <button type="button" className="sh-SettingsSystemSections-8" onClick={onInstall}>
            下载并安装 v{newVersion}
          </button>
        ) : (
          <button
            type="button"
            className="sh-SettingsSystemSections-9"
            onClick={onCheck}
            disabled={phase === "checking" || phase === "downloading"}
          >
            {phase === "checking" ? "检查中…" : phase === "downloading" ? "安装中…" : "检查更新"}
          </button>
        )}
      </div>

      <p className="sh-SettingsSystemSections-10">
        点击后会联网检查是否有新版本。发现新版本时下载安装包并自动校验签名， 确保来自官方发布。
      </p>
    </section>
  );
}

export function AutoModeToggle({ getChannel }: { getChannel: () => ControlChannel | null }) {
  // null = 尚未拿到权威快照（加载中）。绝不用默认值占位。
  const [policy, setPolicy] = useState<AuthorizationPolicyView | null>(null);
  const [pending, setPending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const requestSeqRef = useRef(0);
  const policyRef = useRef<AuthorizationPolicyView | null>(null);

  const nextRequestId = useCallback((kind: "get" | "set") => {
    requestSeqRef.current += 1;
    return `permission-auto-mode-${kind}-${requestSeqRef.current}`;
  }, []);

  const applyPolicy = useCallback((next: AuthorizationPolicyView) => {
    // 代次单调：CAS 只会让 generation 递增，落后的快照（例如写入
    // 途中到达的旧 get 回执）不得把界面拉回写前状态。
    const current = policyRef.current;
    if (current && next.generation < current.generation) return;
    policyRef.current = next;
    setPolicy(next);
    try {
      localStorage.setItem("deskpet.auto_mode", String(next.mode === "auto"));
    } catch {
      /* localStorage full / disabled — 后端仍是权威，非致命 */
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let unsubMessage: (() => void) | null = null;
    let unsubState: (() => void) | null = null;

    const requestCurrentPolicy = (ch: ControlChannel) => {
      if (cancelled) return;
      // ControlChannel.send() 在 socket 未 OPEN 时静默丢帧并返回 false
      // （ws/ControlChannel.ts），旧实现只捕获异常 → get 被丢掉后永不重发，
      // 界面就一直停在默认值上。这里必须检查返回值并重试。
      let sent = false;
      try {
        sent = ch.send(buildAutoModeGetMessage(nextRequestId("get")));
      } catch {
        sent = false;
      }
      if (!sent) {
        retryTimer = setTimeout(() => requestCurrentPolicy(ch), 500);
      }
    };

    const attach = () => {
      if (cancelled) return;
      const ch = getChannel();
      if (!ch) {
        retryTimer = setTimeout(attach, 500);
        return;
      }
      unsubMessage?.();
      unsubState?.();
      unsubMessage = ch.onMessage((msg: IncomingMessage) => {
        const next = readAutoModePolicy(msg);
        if (!next) return;
        applyPolicy(next);
        setPending(false);
        setErr(null);
      });
      unsubState = ch.onStateChange((state) => {
        if (state === "connected") requestCurrentPolicy(ch);
      });
      // 重连/重挂载时通道已缓存了每种类型的最新一帧，先用它渲染。
      const cached = ch.getLatestMessage("permission_auto_mode_response");
      const cachedPolicy = cached ? readAutoModePolicy(cached) : null;
      if (cachedPolicy) applyPolicy(cachedPolicy);
      requestCurrentPolicy(ch);
    };

    attach();
    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      unsubMessage?.();
      unsubState?.();
    };
  }, [applyPolicy, getChannel, nextRequestId]);

  const onToggle = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      // 加载中不可交互（input disabled），这里再兜一层：没有权威快照
      // 就不写。写入携带用户实际想要的目标模式，而不是 !enabled。
      if (!policyRef.current) return;
      const target = event.target.checked;
      setErr(null);
      setPending(true);
      try {
        const ch = getChannel();
        if (!ch) throw new Error("控制通道未连接");
        if (!ch.send(buildAutoModeSetMessage(target, nextRequestId("set")))) {
          throw new Error("控制通道未连接");
        }
      } catch (e) {
        setErr(String(e));
        setPending(false);
      }
    },
    [getChannel, nextRequestId],
  );

  const loading = policy === null;
  const checked = policy?.mode === "auto";
  const disabled = loading || pending;

  return (
    <div className="sh-SettingsSystemSections-11">
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 13,
          cursor: disabled ? "default" : "pointer",
          opacity: disabled ? 0.6 : 1,
        }}
      >
        <input
          type="checkbox"
          data-testid="permission-auto-mode"
          checked={checked}
          onChange={onToggle}
          disabled={disabled}
        />
        <span>自动模式（推荐）：自动处理符合策略的请求</span>
      </label>
      <span data-testid="permission-auto-mode-status" className="sh-SettingsSystemSections-12">
        {loading
          ? "正在读取授权策略…"
          : pending
            ? "保存中…"
            : `当前：${policy.mode === "auto" ? "自动" : "手动"}` +
              `（策略代次 ${policy.generation} · ${policy.provenance}）`}
      </span>
      <p className="sh-SettingsSystemSections-13">
        开启后，读写文件、运行命令、联网、技能安装等 Agent 工具权限会直接放行；
        能力安装/生成卡片会标记“Auto 已授权”以便审计，不再弹 Simple Harness 授权窗口。
        关闭后恢复逐项确认。这个开关不等于 Windows 管理员权限，也不会绕过系统级限制。
      </p>
      {err && <span className="sh-SettingsSystemSections-14">{err}</span>}
    </div>
  );
}

// ----------------------------------------------------------------------
// Agent active-execution budget setting.
//
// The compatibility key still uses the historical chat-turn-timeout name,
// but the backend now counts only durable active execution intervals. This
// asks for the persisted value and sends clamped updates immediately.
// ----------------------------------------------------------------------
export function ChatTurnTimeoutSetting({
  getChannel,
}: {
  getChannel: () => ControlChannel | null;
}) {
  const [turnTimeoutMin, setTurnTimeoutMin] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [status, setStatus] = useState("正在读取…");
  const requestSeqRef = useRef(0);
  const activeRequestRef = useRef<string | null>(null);

  const nextRequestId = useCallback((kind: "get" | "set") => {
    requestSeqRef.current += 1;
    return `chat-turn-timeout-${kind}-${requestSeqRef.current}`;
  }, []);

  useEffect(() => {
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let unsubMessage: (() => void) | null = null;
    let unsubState: (() => void) | null = null;

    const requestCurrentValue = (ch: ControlChannel) => {
      try {
        const requestId = nextRequestId("get");
        activeRequestRef.current = requestId;
        setStatus("正在读取…");
        if (!ch.send(buildChatTurnTimeoutGetMessage(requestId))) {
          throw new Error("控制通道尚未连接");
        }
      } catch {
        /* retry on reconnect or next mount */
      }
    };

    const attach = () => {
      if (cancelled) return;
      const ch = getChannel();
      if (!ch) {
        retryTimer = setTimeout(attach, 500);
        return;
      }

      unsubMessage = ch.onMessage((msg: IncomingMessage) => {
        if (msg.type !== "chat_turn_timeout_response") return;
        if (
          msg.request_id &&
          activeRequestRef.current &&
          msg.request_id !== activeRequestRef.current
        ) {
          return;
        }
        activeRequestRef.current = null;
        setTurnTimeoutMin(clampChatTurnTimeoutMinutes(msg.payload.minutes));
        setErr(null);
        setStatus("已与后端同步");
      });
      unsubState = ch.onStateChange((state) => {
        if (state === "connected") requestCurrentValue(ch);
      });
      requestCurrentValue(ch);
    };

    attach();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      unsubMessage?.();
      unsubState?.();
    };
  }, [getChannel, nextRequestId]);

  const onChange = useCallback(
    (raw: string) => {
      setErr(null);
      const next = clampChatTurnTimeoutMinutes(Number(raw));
      setTurnTimeoutMin(next);
      setStatus("保存中…");
      try {
        const ch = getChannel();
        if (!ch) throw new Error("控制通道未连接");
        const requestId = nextRequestId("set");
        activeRequestRef.current = requestId;
        if (!ch.send(buildChatTurnTimeoutSetMessage(next, requestId))) {
          throw new Error("控制通道未连接");
        }
      } catch (e) {
        setErr(String(e));
        setStatus("保存失败");
      }
    },
    [getChannel, nextRequestId],
  );

  return (
    <div className="sh-SettingsSystemSections-15">
      <label htmlFor="chat-turn-timeout-minutes" className="sh-SettingsSystemSections-16">
        Agent 有效执行预算（分钟）
      </label>
      <input
        id="chat-turn-timeout-minutes"
        type="number"
        min={CHAT_TURN_TIMEOUT_MIN_MINUTES}
        max={CHAT_TURN_TIMEOUT_MAX_MINUTES}
        step={1}
        value={turnTimeoutMin ?? ""}
        disabled={turnTimeoutMin === null}
        placeholder="读取中"
        onChange={(e) => onChange(e.target.value)}
        className="sh-SettingsSystemSections-17"
        data-testid="chat-turn-timeout-minutes"
      />
      <p className="sh-SettingsSystemSections-10">
        只累计 Agent、模型和工具实际工作的时间；等待你确认、选择文件夹或完成外部操作时暂停。默认 15
        分钟，范围 1-60。
      </p>
      <span data-testid="chat-turn-timeout-status" className="sh-SettingsSystemSections-18">
        {status}
      </span>
      {err && <span className="sh-SettingsSystemSections-14">{err}</span>}
    </div>
  );
}

// ----------------------------------------------------------------------
// P3-S9 — Danger Zone: 完全卸载（清除用户数据）.
//
// `完全卸载` wipes the resolved Simple Harness data dir (config / SQLite / logs). A
// second opt-in checkbox additionally wipes %LocalAppData%\deskpet\
// models — that's ~9 GB so we require explicit consent.
//
// Two-step confirm via the in-app dialog (useConfirm) keeps the UI trivial while still
// preventing single-click destruction.
// ----------------------------------------------------------------------
export function DangerZoneSection() {
  const [includeModels, setIncludeModels] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [confirmDialog, ask] = useConfirm();

  const handlePurge = useCallback(async () => {
    setErr(null);
    const scope = includeModels ? "用户数据 + 本地模型缓存" : "用户数据（配置 / 数据库 / 日志）";
    // 2026-09-25：不能用 window.confirm——桌面 WebView 里它不弹框、直接当"确认"。
    const confirmed = await ask({
      title: "完全卸载",
      message:
        `即将删除：${scope}\n\n` +
        "这将清除所有聊天历史、任务记录、配置和日志，无法撤销。\n" +
        "删除完成后 Simple Harness 将自动退出。",
      confirm_label: "确认删除",
    });
    if (!confirmed) return;

    setBusy(true);
    try {
      const core = await import("@tauri-apps/api/core");
      await core.invoke("purge_user_data", { includeModels });
      // Rust will exit the app shortly; nothing else to do here.
    } catch (e) {
      setErr(typeof e === "string" ? e : ((e as Error)?.message ?? String(e)));
      setBusy(false);
    }
  }, [includeModels, ask]);

  return (
    <section className="sh-SettingsSystemSections-19">
      {confirmDialog}
      <h3 className="sh-SettingsSystemSections-20">危险区</h3>
      <p className="sh-SettingsSystemSections-10">
        "完全卸载" 会清除 Simple Harness 当前解析到的用户数据目录（配置、SQLite、日志）。 ⚠️ 若为
        portable 安装（数据实际在安装目录的 <code>userdata/</code>）或你
        自定义过数据目录，此按钮可能删不到真正的数据——最可靠的做法是直接删除
        整个安装目录。卸载安装包本身仍需在「应用和功能」里进行。
      </p>
      <label className="sh-SettingsSystemSections-21">
        <input
          type="checkbox"
          checked={includeModels}
          onChange={(e) => setIncludeModels(e.target.checked)}
          data-testid="purge-include-models"
        />
        <span>
          同时删除本地模型缓存（若已用 <code>DESKPET_MODEL_ROOT</code> 迁移到
          自定义目录，则删该目录）
        </span>
      </label>
      {err && (
        <div role="alert" className="sh-SettingsSystemSections-22">
          {err}
        </div>
      )}
      <div className="sh-SettingsSystemSections-7">
        <button
          type="button"
          data-testid="purge-user-data"
          onClick={handlePurge}
          disabled={busy}
          className="sh-SettingsSystemSections-23"
        >
          {busy ? "删除中…" : "完全卸载（清除用户数据）"}
        </button>
      </div>
    </section>
  );
}

export function DataDirSection() {
  const [confirmDialog, ask] = useConfirm();
  const [setting, setSetting] = useState<DataDirSetting | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [newPath, setNewPath] = useState("");
  const [moveData, setMoveData] = useState(true);
  const [busy, setBusy] = useState(false);
  const [opMsg, setOpMsg] = useState<string | null>(null);
  const [opErr, setOpErr] = useState<string | null>(null);

  // 读当前设置；结果在回调里写状态。
  const load = useCallback(
    () =>
      import("@tauri-apps/api/core")
        .then((core) => core.invoke<DataDirSetting>("get_data_dir_setting"))
        .then(
          (s) => {
            setSetting(s);
            // Pre-fill the input with the current effective path so the
            // user can edit-in-place rather than retype from scratch
            // (keep whatever the user already typed).
            setNewPath((current) => current || s.effective);
          },
          (e: unknown) => setLoadErr(e instanceof Error ? e.message : String(e)),
        ),
    [],
  );
  // 刷新按钮先清错误再读；挂载时错误初值就是 null，直接读。
  const reload = () => {
    setLoadErr(null);
    void load();
  };

  useEffect(() => {
    void load();
  }, [load]);

  const handlePickDir = useCallback(async () => {
    setOpErr(null);
    try {
      const core = await import("@tauri-apps/api/core");
      const picked = await core.invoke<string | null>("open_directory_dialog");
      if (picked) setNewPath(picked);
    } catch (e) {
      setOpErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const handleApply = useCallback(async () => {
    setOpErr(null);
    setOpMsg(null);
    if (!setting) return;
    if (setting.externally_pinned) {
      setOpErr(
        "当前目录由启动环境变量固定；请移除 DESKPET_USER_DATA_DIR/DESKPET_USER_DATA 后重启，再从这里切换。",
      );
      return;
    }
    const target = newPath.trim();
    if (!target) {
      setOpErr("请先选择或输入新路径");
      return;
    }
    if (isDataDirPreferenceNoop(setting, target)) {
      setOpErr("新路径与当前路径相同，无需修改");
      return;
    }
    const sizeStr = formatMb(setting.effective_size_bytes);
    const confirmed = await ask({
      title: "切换数据目录",
      message:
        `即将把数据目录切换为：\n${target}\n\n` +
        (moveData
          ? `并将现有数据（约 ${sizeStr}）从\n${setting.effective}\n复制并删除原位置文件。\n\n`
          : "（不移动现有数据 — 旧目录保留，新目录从空开始）\n\n") +
        "Simple Harness 需要重启才能完全生效。",
      confirm_label: "确认切换",
    });
    if (!confirmed) return;

    setBusy(true);
    try {
      const core = await import("@tauri-apps/api/core");
      // Persist the bootstrap pointer first so the next launch resolves the
      // same target before the backend is spawned.
      const updated = await core.invoke<DataDirSetting>("set_data_dir_preference", {
        newPath: target,
      });
      setSetting(withNextLaunchDataDirPreference(setting, updated));

      if (moveData && setting.effective_exists && setting.effective !== target) {
        const moved = await core.invoke<number>("move_data_dir_contents", {
          src: setting.effective,
          dst: target,
        });
        setOpMsg(
          `已保存并移动 ${formatMb(moved)} 数据。请重启 Simple Harness 让所有进程读到新路径。`,
        );
      } else {
        setOpMsg("已保存。下次启动时 Simple Harness 会从新路径读写。");
      }
    } catch (e) {
      setOpErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [setting, newPath, moveData, ask]);

  const handleReset = useCallback(async () => {
    if (!setting) return;
    if (setting.externally_pinned) {
      setOpErr("当前目录由启动环境变量固定，应用内不能覆盖；请先移除外部环境变量。");
      return;
    }
    const confirmed = await ask({
      title: "恢复默认数据目录",
      message:
        `将数据目录偏好切回默认路径：\n${setting.default ?? "（平台默认）"}\n\n` +
        "注意：现有数据不会被自动搬回去 —— 你需要手动移动，或先在上方填入默认路径并勾选「移动」。",
      confirm_label: "确认恢复",
      variant: "primary",
    });
    if (!confirmed) return;
    setBusy(true);
    setOpErr(null);
    setOpMsg(null);
    try {
      const core = await import("@tauri-apps/api/core");
      // Persist the platform default as the next-launch preference.
      if (setting.default) {
        const updated = await core.invoke<DataDirSetting>("set_data_dir_preference", {
          newPath: setting.default,
        });
        setSetting(withNextLaunchDataDirPreference(setting, updated));
        setNewPath(setting.default);
        setOpMsg("已切回默认目录设置。请重启 Simple Harness 生效。");
      } else {
        setOpErr("无法识别默认目录（%AppData% 未设置？）");
      }
    } catch (e) {
      setOpErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [setting, ask]);

  if (loadErr) {
    return (
      <section className="sh-SettingsSystemSections-1">
        <h3 className="sh-SettingsSystemSections-2">数据目录</h3>
        <div className="sh-SettingsSystemSections-22">加载失败：{loadErr}</div>
      </section>
    );
  }
  if (!setting) {
    return (
      <section className="sh-SettingsSystemSections-1">
        <h3 className="sh-SettingsSystemSections-2">数据目录</h3>
        <div className="sh-SettingsSystemSections-3">加载中…</div>
      </section>
    );
  }

  return (
    <section id="settings-data-dir" className="sh-SettingsSystemSections-1">
      {confirmDialog}
      <h3 className="sh-SettingsSystemSections-2">数据目录</h3>
      <p className="sh-SettingsSystemSections-10">
        Simple Harness 的聊天历史、配置、SQLite 数据库和设备 ID 都保存在这里。 如果 C
        盘空间紧张，可以搬到其他磁盘。
      </p>

      <div className="sh-SettingsSystemSections-24">
        <div className="sh-SettingsSystemSections-25">当前生效</div>
        <div className="sh-SettingsSystemSections-26">
          {setting.effective}{" "}
          <span className="sh-SettingsSystemSections-25">
            ({formatMb(setting.effective_size_bytes)})
          </span>
        </div>

        <div className="sh-SettingsSystemSections-25">环境变量</div>
        <div className="sh-SettingsSystemSections-26">
          {setting.env_override ?? (
            <span className="sh-SettingsSystemSections-27">(未设置 — 使用默认路径)</span>
          )}
        </div>

        <div className="sh-SettingsSystemSections-25">已保存偏好</div>
        <div className="sh-SettingsSystemSections-26">
          {setting.preference ?? <span className="sh-SettingsSystemSections-27">(未设置)</span>}
        </div>

        <div className="sh-SettingsSystemSections-25">默认路径</div>
        <div className="sh-SettingsSystemSections-28">{setting.default ?? "—"}</div>
      </div>

      {setting.externally_pinned && (
        <div role="status" className="sh-SettingsSystemSections-29">
          当前目录由启动环境变量固定。为避免显示“已保存”但重启仍被覆盖，应用内切换已禁用。
        </div>
      )}

      <div className="sh-SettingsSystemSections-30">
        <label className="sh-SettingsSystemSections-31">新路径</label>
        <div className="sh-SettingsSystemSections-32">
          <input
            type="text"
            value={newPath}
            onChange={(e) => setNewPath(e.target.value)}
            disabled={busy || setting.externally_pinned}
            placeholder="F:\deskpet\data"
            className="sh-SettingsSystemSections-33"
            data-testid="data-dir-input"
          />
          <button
            type="button"
            onClick={handlePickDir}
            disabled={busy || setting.externally_pinned}
            className="sh-SettingsSystemSections-9"
          >
            浏览…
          </button>
        </div>
        <label className="sh-SettingsSystemSections-34">
          <input
            type="checkbox"
            checked={moveData}
            onChange={(e) => setMoveData(e.target.checked)}
            disabled={busy || setting.externally_pinned}
          />
          <span>同时移动现有数据到新位置（推荐）</span>
        </label>
      </div>

      <div className="sh-SettingsSystemSections-35">
        <button
          type="button"
          onClick={handleApply}
          disabled={busy || setting.externally_pinned}
          className="sh-SettingsSystemSections-36"
          data-testid="data-dir-apply"
        >
          {busy ? "处理中…" : "应用"}
        </button>
        <button
          type="button"
          onClick={handleReset}
          disabled={busy || setting.externally_pinned}
          className="sh-SettingsSystemSections-9"
          data-testid="data-dir-reset"
        >
          恢复默认
        </button>
        <button
          type="button"
          onClick={reload}
          disabled={busy}
          className="sh-SettingsSystemSections-9"
        >
          刷新
        </button>
      </div>

      {opMsg && (
        <div role="status" className="sh-SettingsSystemSections-37">
          {opMsg}
        </div>
      )}
      {opErr && (
        <div role="alert" className="sh-SettingsSystemSections-22">
          {opErr}
        </div>
      )}
    </section>
  );
}
