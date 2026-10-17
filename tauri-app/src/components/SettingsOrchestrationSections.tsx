// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { useConfirm } from "./useConfirm";
import { dark } from "../theme/components";
import type { ControlMessage, IncomingMessage } from "../types/messages";
import type { ControlChannel } from "../ws/ControlChannel";
import { sendWhenConnected } from "../ws/sendWhenConnected";
import { buildStorageUsageMessage, formatGb, formatMonth, formatTokens } from "./settingsMessages";
import { statusStyle } from "./settingsStyles";
import {
  buildPublishDirMessage,
  SKILL_STATE,
  poolLabel,
  poolStatus,
  evaluationStatus,
  SKILL_DONE,
} from "./settingsOrchestration";
import type {
  StorageUsageData,
  GlobalBudgetData,
  PublishDirData,
  SkillCatalogueRow,
  SkillCatalogueData,
  SkillReply,
} from "./settingsOrchestration";
import "./SettingsOrchestrationSections.styles.css";

export function StorageUsageSection({ getChannel }: { getChannel: () => ControlChannel | null }) {
  const [data, setData] = useState<StorageUsageData | null>(null);
  // 有通道就在挂载时读一次，所以首次渲染就是读取中。
  const [busy, setBusy] = useState(() => getChannel() !== null);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<string | null>(null);
  const counter = useRef(0);

  // 只发请求，不写状态；返回是否发出。
  const send = useCallback(
    (refresh: boolean) => {
      const ch = getChannel();
      if (!ch) return false;
      const id = `storage-${Date.now()}-${++counter.current}`;
      pending.current = id;
      return ch.send(buildStorageUsageMessage(id, refresh));
    },
    [getChannel],
  );
  // 点击"重新测量"：先设读取中，发不出去就报错。
  const request = (refresh: boolean) => {
    if (!getChannel()) {
      setError("连接不可用");
      return;
    }
    setBusy(true);
    setError(null);
    if (!send(refresh)) {
      setBusy(false);
      setError("连接不可用，请求未发送");
    }
  };

  useEffect(() => {
    const ch = getChannel();
    if (!ch) return;
    const off = ch.onMessage((raw: IncomingMessage) => {
      // 编排通道的应答不在 IncomingMessage 联合里（同其他编排视图一样宽松读取）
      const msg = raw as unknown as { type?: string; payload?: unknown };
      if (msg.type !== "orchestration_storage_get_response") return;
      const payload = (msg.payload ?? {}) as {
        ok?: boolean;
        request_id?: unknown;
        data?: StorageUsageData;
        error?: string;
      };
      if (payload.request_id !== pending.current) return;
      setBusy(false);
      if (payload.ok === false || !payload.data) {
        setError(payload.error ?? "读取失败");
        return;
      }
      setData(payload.data);
    });
    // 挂载读一次：没连上就等连上再发（结果走上面的消息回调）。
    const cancelSend = sendWhenConnected(ch, () => send(false));
    return () => {
      off();
      cancelSend();
    };
  }, [getChannel, send]);

  const over = !!data?.over_warn;
  return (
    <section className="sh-SettingsOrchestrationSections-1" data-testid="storage-usage">
      <h3 className="sh-SettingsOrchestrationSections-2">任务与会话数据</h3>
      {over && data ? (
        <div
          role="alert"
          data-testid="storage-usage-warning"
          className="sh-SettingsOrchestrationSections-3"
        >
          已超过 {formatGb(data.warn_bytes)}。这些数据会一直保留，请到「数据目录」迁移到更大的磁盘。{" "}
          <button
            type="button"
            className="sh-SettingsOrchestrationSections-4"
            onClick={() =>
              document.getElementById("settings-data-dir")?.scrollIntoView({ behavior: "smooth" })
            }
          >
            去数据目录
          </button>
        </div>
      ) : null}
      <p className="sh-SettingsOrchestrationSections-5">
        {data
          ? data.measured
            ? `任务与会话数据：${formatGb(data.bytes)}`
            : "任务与会话数据：尚未统计"
          : (error ?? "读取中…")}
        （含聊天记录、检索索引和检查记录；用于审计，不会自动删除）
      </p>
      {data?.measured ? (
        <div className="sh-SettingsOrchestrationSections-6">
          {data.breakdown.map((row) => (
            <Fragment key={row.key}>
              <div>{row.label}</div>
              <div>{formatGb(row.bytes)}</div>
            </Fragment>
          ))}
        </div>
      ) : null}
      <div>
        <button
          type="button"
          data-testid="storage-usage-refresh"
          className="sh-SettingsOrchestrationSections-7"
          disabled={busy}
          onClick={() => request(true)}
        >
          {busy ? "统计中…" : "重新统计"}
        </button>
        {error && data ? <span className="sh-SettingsOrchestrationSections-8">{error}</span> : null}
      </div>
    </section>
  );
}

export function GlobalBudgetSection({ getChannel }: { getChannel: () => ControlChannel | null }) {
  const [data, setData] = useState<GlobalBudgetData | null>(null);
  // 挂载时没有通道就直接说明（原来在 effect 里补写）。
  const [error, setError] = useState<string | null>(() => (getChannel() ? null : "连接不可用"));
  const pending = useRef<string | null>(null);

  useEffect(() => {
    const ch = getChannel();
    if (!ch) return;
    const off = ch.onMessage((raw: IncomingMessage) => {
      const msg = raw as unknown as { type?: string; payload?: unknown };
      if (msg.type !== "orchestration_status_response") return;
      const payload = (msg.payload ?? {}) as {
        ok?: boolean;
        request_id?: unknown;
        data?: { global_budget?: GlobalBudgetData | null };
        error?: string;
      };
      if (payload.request_id !== pending.current) return;
      if (payload.ok === false || !payload.data) {
        setError(payload.error ?? "读取失败");
        return;
      }
      setData(payload.data.global_budget ?? null);
      setError(payload.data.global_budget ? null : "后台没有报全局预算");
    });
    // 读一次：没连上就等连上再发（结果走上面的消息回调）。
    const cancelSend = sendWhenConnected(ch, () => {
      const id = `global-budget-${Date.now()}`;
      pending.current = id;
      return ch.send({
        type: "orchestration_status",
        request_id: id,
        payload: {},
      } as unknown as ControlMessage);
    });
    return () => {
      off();
      cancelSend();
    };
  }, [getChannel]);

  return (
    <section className="sh-SettingsOrchestrationSections-1" data-testid="global-budget">
      <h3 className="sh-SettingsOrchestrationSections-2">本月全局预算</h3>
      <p className="sh-SettingsOrchestrationSections-5">
        这台机器上所有后台任务每个月合计可用的 token 上限（单个任务默认上限 2000 万、每一步 1000
        万）。 任务的用量算在它建立的那个月；每月 1 日自动换成新的一个月，额度重新是满的。
        本月用完后新任务会在第一轮规划时停下、本月建立的任务也会停下； 在 config.toml 的
        [orchestration] 里改 global_monthly_max_tokens
        并重启，调大调小都在重启后立即生效（在跑的任务也按新上限）；
        调小到低于某个月的已用量时，那个月建立、还在跑的任务（包括上个月建立的）会按“全局预算用完”停下。
      </p>
      <div
        role="status"
        data-testid="global-budget-status"
        style={{ ...statusStyle, color: data ? dark.text : dark.textMuted }}
      >
        {data === null
          ? (error ?? "读取中…")
          : !data.opened
            ? `${formatMonth(data.month)}：上限 ${formatTokens(data.max_tokens)}；本月还没有任务用过`
            : `${formatMonth(data.month)}：上限 ${formatTokens(data.max_tokens)}；已用 ${formatTokens(data.used_tokens)}，预留中 ${formatTokens(data.reserved_tokens)}，剩余 ${formatTokens(data.remaining_tokens)}`}
      </div>
    </section>
  );
}

export function PublishDirSection({ getChannel }: { getChannel: () => ControlChannel | null }) {
  const [confirmDialog, ask] = useConfirm();
  const [data, setData] = useState<PublishDirData | null>(null);
  const [path, setPath] = useState("");
  // 有通道就在挂载时读一次，所以首次渲染就是读取中。
  const [busy, setBusy] = useState(() => getChannel() !== null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const pending = useRef<string | null>(null);
  const counter = useRef(0);
  const filled = useRef(false);

  // 只发请求，不写状态；返回是否发出。
  const sendRequest = useCallback(
    (next?: string) => {
      const ch = getChannel();
      if (!ch) return false;
      const id = `publish-dir-${Date.now()}-${++counter.current}`;
      pending.current = id;
      return ch.send(buildPublishDirMessage(id, next));
    },
    [getChannel],
  );
  // 用户操作：先设处理中，发不出去就报错。
  const send = useCallback(
    (next?: string) => {
      if (!getChannel()) {
        setError("连接不可用");
        return;
      }
      setBusy(true);
      setError(null);
      if (!sendRequest(next)) {
        setBusy(false);
        setError("连接不可用，请求未发送");
      }
    },
    [getChannel, sendRequest],
  );

  useEffect(() => {
    const ch = getChannel();
    if (!ch) return;
    const off = ch.onMessage((raw: IncomingMessage) => {
      const msg = raw as unknown as { type?: string; payload?: unknown };
      if (
        msg.type !== "orchestration_publish_dir_get_response" &&
        msg.type !== "orchestration_publish_dir_set_response"
      )
        return;
      const payload = (msg.payload ?? {}) as {
        ok?: boolean;
        request_id?: unknown;
        data?: PublishDirData;
        error?: string;
      };
      if (payload.request_id !== pending.current) return;
      pending.current = null;
      setBusy(false);
      if (payload.ok !== true || !payload.data) {
        setError(payload.error ?? "读取失败");
        return;
      }
      setData(payload.data);
      if (!filled.current) {
        filled.current = true;
        setPath(payload.data.configured);
      }
      if (msg.type === "orchestration_publish_dir_set_response") {
        setPath(payload.data.configured);
        setDone(payload.data.configured ? "已授权，新建带发布的任务可以用了" : "已撤销授权");
      }
    });
    // 挂载（或换通道）读一次：没连上就等连上再发，等待期间不报错。
    const cancelRead = sendWhenConnected(ch, () => sendRequest());
    return () => {
      off();
      cancelRead();
    };
  }, [getChannel, sendRequest]);

  const pick = useCallback(async () => {
    try {
      const core = await import("@tauri-apps/api/core");
      const picked = await core.invoke<string | null>("open_directory_dialog");
      if (picked) setPath(picked);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const save = useCallback(
    async (next: string) => {
      setDone(null);
      const running = data?.active_missions ?? 0;
      const ok = await ask({
        title: next ? "授权这个发布目录？" : "撤销发布目录授权？",
        message:
          (next
            ? `带"发布"要求的任务会把验收过的文件发布到：${next}。`
            : "之后带发布要求的任务将无法发布；已经发布的文件不会被改动。") +
          (running > 0
            ? `保存后任务服务会重启一次，正在进行的 ${running} 个任务会暂停几秒后自动接着做。`
            : "保存后任务服务会重启一次。"),
        confirm_label: next ? "授权" : "撤销",
        variant: next ? "primary" : "danger",
      });
      if (ok) send(next);
    },
    [ask, data, send],
  );

  const publish = data?.publish ?? {};
  return (
    <section className="sh-SettingsOrchestrationSections-1" data-testid="publish-dir">
      {confirmDialog}
      <h3 className="sh-SettingsOrchestrationSections-2">任务发布目录</h3>
      <p className="sh-SettingsOrchestrationSections-5">
        任务里要求"发布到…"时，系统只会把审核通过的文件发布到这个目录（每次发布都要你批准）。
      </p>
      <div
        role="status"
        data-testid="publish-dir-status"
        style={{ ...statusStyle, color: publish.enabled ? dark.text : dark.textMuted }}
      >
        {data === null
          ? (error ?? "读取中…")
          : publish.enabled
            ? `已授权：${publish.root}`
            : data.configured
              ? `未生效：${publish.reason ?? "原因未知"}（${data.configured}）`
              : "未授权：带发布要求的任务无法发布"}
      </div>
      <div className="sh-SettingsOrchestrationSections-9">
        <input
          type="text"
          aria-label="发布目录路径"
          value={path}
          disabled={busy}
          onChange={(e) => setPath(e.target.value)}
          placeholder="/Users/你/Documents/发布"
          className="sh-SettingsOrchestrationSections-10"
        />
        <button
          type="button"
          className="sh-SettingsOrchestrationSections-7"
          disabled={busy}
          onClick={pick}
        >
          选择目录…
        </button>
      </div>
      <div className="sh-SettingsOrchestrationSections-11">
        <button
          type="button"
          data-testid="publish-dir-save"
          className="sh-SettingsOrchestrationSections-12"
          disabled={busy || !path.trim() || path.trim() === (data?.configured ?? "")}
          onClick={() => save(path.trim())}
        >
          {busy ? "处理中…" : "保存并授权"}
        </button>
        {data?.configured ? (
          <button
            type="button"
            data-testid="publish-dir-revoke"
            className="sh-SettingsOrchestrationSections-7"
            disabled={busy}
            onClick={() => save("")}
          >
            撤销授权
          </button>
        ) : null}
      </div>
      {error && data ? (
        <p role="alert" className="sh-SettingsOrchestrationSections-13">
          {error}
        </p>
      ) : null}
      {done ? <p className="sh-SettingsOrchestrationSections-5">{done}</p> : null}
    </section>
  );
}

export function SkillCatalogueSection({ getChannel }: { getChannel: () => ControlChannel | null }) {
  const [confirmDialog, ask] = useConfirm();
  const [data, setData] = useState<SkillCatalogueData | null>(null);
  const [path, setPath] = useState("");
  // 有通道就在挂载时读一次目录，所以首次渲染就是读取中。
  const [busy, setBusy] = useState(() => getChannel() !== null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const pending = useRef<string | null>(null);
  const counter = useRef(0);

  // 只发请求，不写状态；返回是否发出。
  const sendRequest = useCallback(
    (type: string, payload: Record<string, unknown>) => {
      const ch = getChannel();
      if (!ch) return false;
      const id = `skill-${Date.now()}-${++counter.current}`;
      pending.current = id;
      return ch.send({ type, request_id: id, payload });
    },
    [getChannel],
  );
  // 用户操作：先设处理中，发不出去就报错。
  const send = useCallback(
    (type: string, payload: Record<string, unknown>) => {
      if (!getChannel()) {
        setError("连接不可用");
        return;
      }
      setBusy(true);
      setError(null);
      if (!sendRequest(type, payload)) {
        setBusy(false);
        setError("连接不可用，请求未发送");
      }
    },
    [getChannel, sendRequest],
  );

  useEffect(() => {
    const ch = getChannel();
    if (!ch) return;
    const types = [
      "orchestration_skill_catalogue_response",
      "orchestration_skill_install_file_response",
      "orchestration_skill_lifecycle_response",
      "orchestration_skill_evaluate_response",
      "orchestration_skill_admit_response",
    ];
    const off = ch.onMessage((raw: IncomingMessage) => {
      const msg = raw as unknown as { type?: string; payload?: SkillReply };
      if (!msg.type || !types.includes(msg.type)) return;
      const payload = msg.payload ?? {};
      if (payload.request_id !== pending.current) return;
      pending.current = null;
      setBusy(false);
      if (payload.ok !== true || !payload.data) {
        setError(payload.error ?? "操作失败");
        return;
      }
      if (msg.type === "orchestration_skill_catalogue_response") {
        setData(payload.data as SkillCatalogueData);
        return;
      }
      const body = payload.data as {
        response?: { error?: { code?: string } | null };
        catalogue?: SkillCatalogueData;
      };
      if (body.catalogue) setData(body.catalogue);
      const refused = body.response?.error?.code;
      if (refused) {
        setError(`操作被拒：${refused}`);
        return;
      }
      setDone(SKILL_DONE[msg.type] ?? "已完成，各档位同步生效");
    });
    // 挂载（或换通道）读一次目录：没连上就等连上再发，等待期间不报错。
    const cancelRead = sendWhenConnected(ch, () =>
      sendRequest("orchestration_skill_catalogue", {}),
    );
    return () => {
      off();
      cancelRead();
    };
  }, [getChannel, sendRequest]);

  const act = useCallback(
    async (row: SkillCatalogueRow, action: "SUSPEND" | "RESUME" | "RETIRE") => {
      setDone(null);
      if (action === "RETIRE") {
        const ok = await ask({
          title: `退役技能 ${row.name}？`,
          message: "退役后所有档位都不能再使用这个版本，且不能恢复。",
          confirm_label: "确认退役",
          variant: "danger",
        });
        if (!ok) return;
      }
      send("orchestration_skill_lifecycle", {
        skill_ref: row.skill_ref,
        action,
        command_id: `skill-${action.toLowerCase()}-${row.skill_id}-${row.version}-${Date.now()}`,
      });
    },
    [ask, send],
  );

  const step = useCallback(
    (row: SkillCatalogueRow, action: "evaluate" | "admit") => {
      setDone(null);
      send(`orchestration_skill_${action}`, {
        skill_ref: row.skill_ref,
        command_id: `skill-${action}-${row.skill_id}-${row.version}-${Date.now()}`,
      });
    },
    [send],
  );

  return (
    <section className="sh-SettingsOrchestrationSections-1" data-testid="skill-catalogue">
      {confirmDialog}
      <h3 className="sh-SettingsOrchestrationSections-2">任务技能目录</h3>
      <p className="sh-SettingsOrchestrationSections-5">
        所有任务执行档位共用这一个技能目录：安装一次，各档位看到同一版本；暂停或退役一次，各档位下一次使用即生效。新装的技能要先评估：系统建一个试用任务，由执行者真实调用它，独立审阅通过后才能准入。
      </p>
      <div
        role="status"
        data-testid="skill-catalogue-status"
        className="sh-SettingsOrchestrationSections-14"
      >
        {data === null
          ? (error ?? "读取中…")
          : data.skills.length === 0
            ? "还没有技能"
            : `共 ${data.skills.length} 个技能版本`}{" "}
        <button
          type="button"
          className="sh-SettingsOrchestrationSections-7"
          disabled={busy}
          onClick={() => {
            setDone(null);
            send("orchestration_skill_catalogue", {});
          }}
        >
          刷新
        </button>
      </div>
      {data?.skills.map((row) => {
        const evaluation = evaluationStatus(row.state, row.evaluation);
        return (
          <div
            key={`${row.skill_id}@${row.version}`}
            data-testid="skill-row"
            className="sh-SettingsOrchestrationSections-15"
          >
            <div className="sh-SettingsOrchestrationSections-16">
              {row.name}{" "}
              <span className="sh-SettingsOrchestrationSections-17">
                第 {row.version} 版 · {SKILL_STATE[row.state ?? ""] ?? "未知"}
              </span>
            </div>
            {row.description ? (
              <div className="sh-SettingsOrchestrationSections-18">{row.description}</div>
            ) : null}
            <ul className="sh-SettingsOrchestrationSections-19">
              {Object.entries(row.pools).map(([pool, view]) => (
                <li key={pool}>
                  {poolLabel(pool)}：{poolStatus(view)}
                </li>
              ))}
            </ul>
            {evaluation.text && row.state === "TRIAL" ? (
              <div data-testid="skill-evaluation" className="sh-SettingsOrchestrationSections-18">
                {evaluation.text}
              </div>
            ) : null}
            <div className="sh-SettingsOrchestrationSections-11">
              {evaluation.action === "evaluate" ? (
                <button
                  type="button"
                  className="sh-SettingsOrchestrationSections-12"
                  disabled={busy}
                  onClick={() => step(row, "evaluate")}
                >
                  {row.state === "TRIAL" ? "重新评估" : "开始评估"}
                </button>
              ) : null}
              {evaluation.action === "admit" ? (
                <button
                  type="button"
                  className="sh-SettingsOrchestrationSections-12"
                  disabled={busy}
                  onClick={() => step(row, "admit")}
                >
                  准入
                </button>
              ) : null}
              {row.state === "ADMITTED" ? (
                <button
                  type="button"
                  className="sh-SettingsOrchestrationSections-7"
                  disabled={busy}
                  onClick={() => act(row, "SUSPEND")}
                >
                  暂停
                </button>
              ) : null}
              {row.state === "SUSPENDED" ? (
                <button
                  type="button"
                  className="sh-SettingsOrchestrationSections-7"
                  disabled={busy}
                  onClick={() => act(row, "RESUME")}
                >
                  恢复
                </button>
              ) : null}
              {row.state !== "RETIRED" ? (
                <button
                  type="button"
                  className="sh-SettingsOrchestrationSections-7"
                  disabled={busy}
                  onClick={() => act(row, "RETIRE")}
                >
                  退役
                </button>
              ) : null}
            </div>
          </div>
        );
      })}
      <div className="sh-SettingsOrchestrationSections-20">
        <input
          type="text"
          aria-label="技能包路径"
          value={path}
          disabled={busy}
          onChange={(e) => setPath(e.target.value)}
          placeholder="/Users/你/Downloads/某技能.zip"
          className="sh-SettingsOrchestrationSections-10"
        />
        <button
          type="button"
          data-testid="skill-install"
          className="sh-SettingsOrchestrationSections-12"
          disabled={busy || !path.trim()}
          onClick={() => {
            setDone(null);
            send("orchestration_skill_install_file", { path: path.trim() });
          }}
        >
          {busy ? "处理中…" : "安装"}
        </button>
      </div>
      {error && data ? (
        <p role="alert" className="sh-SettingsOrchestrationSections-13">
          {error}
        </p>
      ) : null}
      {done ? <p className="sh-SettingsOrchestrationSections-5">{done}</p> : null}
    </section>
  );
}
