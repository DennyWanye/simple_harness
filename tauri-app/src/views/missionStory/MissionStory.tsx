// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
/**
 * 任务过程：按时间一张张卡片讲清楚模型做了什么（2026-09-27 用户：执行图看不出模型一步一步做了什么）。
 *
 * - 数据（2026-09-30 改走 SDK 正式只读接口）：卡片来自 `taskgraph.execution_snapshot`（分页读完），
 *   展开一张模型回合卡片才读它的 `taskgraph.execution_detail`（SDK 已去掉思考内容与系统提醒、做密钥脱敏）。
 * - 实时：本任务的 `mission_changed` 到了就重读，最快 2 秒一次，读取在途只合并成一次后续读取；
 *   展开着且还在进行的回合随每次重读刷新明细；任务结束后再读一次收尾。
 * - 性能：内容没变的卡片沿用旧对象（React.memo 不重画）；卡片默认折叠只画一行，展开才画明细；
 *   正在进行的回合自动展开。
 */
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { asRecord, newRequestKey, type MissionsChannel } from "../../stores/missionsStore";
import {
  eventLine, headline, itemLine, systemReason, parseTurnDetail, reuseCards, stepNames, storyFromExecution, turnBadge,
  turnGist, turnTitle, type Card, type EventCard, type StepNames, type Story, type TurnCard, type TurnDetail,
} from "./storyModel";
import { mergePages, parseExecutionPage, STALL_SECONDS, type ExecutionPage, type ExecutionView } from "../liveGraph/model";
import { PROTOCOL_ERROR, PROTOCOL_ERROR_TEXT } from "../../ws/orchestrationContracts";
import "./MissionStory.css";

const SNAPSHOT_TYPE = "taskgraph.execution_snapshot";
const DETAIL_TYPE = "taskgraph.execution_detail";
const MAX_PAGES = 20;
const THROTTLE_MS = 2000;
const TIMEOUT_MS = 20000;
const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
const ROLE_ICON: Record<string, string> = { plan: "🧭", work: "🛠", review: "🔍", final: "🏁", other: "🤖" };

function clock(seconds: number | null | undefined): string {
  if (!seconds) return "";
  const d = new Date(seconds * 1000);
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((v) => String(v).padStart(2, "0")).join(":");
}

const TurnView = memo(function TurnView({ card, names, open, missionEnded, stalled, onToggle }: {
  card: TurnCard; names: StepNames; open: boolean; missionEnded: boolean; stalled: boolean; onToggle: (id: string) => void;
}) {
  const badge = turnBadge(card, missionEnded);
  const gist = open ? "" : turnGist(card, names);
  return (
    <li className={"ms-card ms-" + card.kind + " ms-badge-" + badge.tone} data-testid={"story-card-" + card.id}>
      <button type="button" className="ms-head" aria-expanded={open} onClick={() => onToggle(card.id)}>
        <span className="ms-icon" aria-hidden>{ROLE_ICON[card.kind] ?? "🤖"}</span>
        <span className="ms-title">{turnTitle(card, names)}</span>
        {stalled && <span className="ms-badge ms-tone-bad" title="超过 10 分钟没有新动作">可能卡住</span>}
        <span className={"ms-badge ms-tone-" + badge.tone}>{badge.text}</span>
      </button>
      <div className="ms-sub">
        {clock(card.at)}{card.model ? " · " + card.model : ""}
      </div>
      {gist && <div className="ms-gist">{gist}</div>}
      {open && (
        <ol className="ms-items">
          {card.items === null && <li className="ms-muted">正在读取这一回合的明细…</li>}
          {card.hidden_items > 0 && <li className="ms-muted">{`前面还有 ${card.hidden_items} 条更早的操作没有显示`}</li>}
          {card.items?.length === 0 && <li className="ms-muted">{card.state === "done" ? "这一回合没有留下操作记录。" : "还没有动作…"}</li>}
          {(card.items ?? []).map((item, index) => {
            const line = itemLine(item, names);
            return (
              <li key={index} className={"ms-item ms-tone-" + line.tone}>
                <span className="ms-icon" aria-hidden>{line.icon}</span>
                <span className="ms-text">
                  {line.text}
                  {line.tone === "model" && <span className="ms-muted">（模型生成）</span>}
                  {line.list && line.list.length > 0 && (
                    <ul className="ms-reasons">
                      {line.list.map((r, i) => (
                        <li key={i}><span className={"ms-tag ms-tag-" + (r.good === true ? "good" : r.good === false ? "bad" : "info")}>{r.tag || "说明"}</span>{r.text}</li>
                      ))}
                    </ul>
                  )}
                </span>
              </li>
            );
          })}
          {!missionEnded && card.state === "running" && <li className="ms-muted ms-live">模型正在工作…</li>}
        </ol>
      )}
    </li>
  );
});

const EventView = memo(function EventView({ card, names }: { card: EventCard; names: StepNames }) {
  const line = eventLine(card, names);
  return (
    <li className={"ms-event ms-tone-" + line.tone} data-testid={"story-card-" + card.id}>
      <span className="ms-icon" aria-hidden>{line.icon}</span>
      <span className="ms-text">
        <span className="ms-time">{clock(card.at)}</span> {line.text}
        {card.reasons && card.reasons.length > 0 && (
          <ul className="ms-reasons">{card.reasons.map((r, i) => <li key={i}>{r.model ? r.text : systemReason(r.text)}
            {r.model && <span className="ms-muted">（模型生成）</span>}</li>)}</ul>
        )}
      </span>
    </li>
  );
});

export function MissionStory({ missionId, channel, missionStatus, onStalled }: {
  missionId: string; channel: MissionsChannel | null; missionStatus: string;
  /** "可能卡住"的回合数变化时告诉外面（任务列表行显示提示）。 */
  onStalled?: (count: number) => void;
}) {
  const [view, setView] = useState<ExecutionView | null>(null);
  const [details, setDetails] = useState<Map<string, TurnDetail>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [toggled, setToggled] = useState<Map<string, boolean>>(new Map());
  const inflight = useRef<{ id: string; pages: ExecutionPage[]; restarted: boolean } | null>(null);
  const detailInflight = useRef<Map<string, string>>(new Map()); // request id → node id
  const again = useRef(false);
  const lastStart = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const timeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ended = TERMINAL.has(missionStatus);
  const endedRef = useRef(ended);
  useEffect(() => { endedRef.current = ended; }, [ended]);

  const sendPage = useCallback((cursor: string | null, pending: { pages: ExecutionPage[]; restarted: boolean }) => {
    if (!channel) return;
    const id = newRequestKey();
    inflight.current = { id, ...pending };
    lastStart.current = Date.now();
    const payload: Record<string, unknown> = { mission_id: missionId };
    if (cursor) payload.cursor = cursor;
    if (!channel.send({ type: SNAPSHOT_TYPE, request_id: id, payload })) {
      inflight.current = null; setError("连接不可用，请求未发送"); return;
    }
    if (timeout.current) clearTimeout(timeout.current);
    timeout.current = setTimeout(() => {
      if (inflight.current?.id !== id) return;
      inflight.current = null; setError("读取超时，已保留上次内容");
    }, TIMEOUT_MS);
  }, [channel, missionId]);

  const sendRef = useRef<() => void>(() => {});
  const send = useCallback(() => {
    if (!channel) return;
    if (inflight.current) { again.current = true; return; }
    const wait = lastStart.current + THROTTLE_MS - Date.now();
    if (wait > 0) {
      timer.current ??= setTimeout(() => { timer.current = null; sendRef.current(); }, wait);
      return;
    }
    sendPage(null, { pages: [], restarted: false });
  }, [channel, sendPage]);
  useEffect(() => { sendRef.current = send; }, [send]);

  const askDetail = useCallback((nodeId: string) => {
    if (!channel || [...detailInflight.current.values()].includes(nodeId)) return;
    const id = newRequestKey();
    detailInflight.current.set(id, nodeId);
    if (!channel.send({ type: DETAIL_TYPE, request_id: id, payload: { mission_id: missionId, node_id: nodeId } })) {
      detailInflight.current.delete(id);
    }
  }, [channel, missionId]);

  useEffect(() => {
    send();
    const detailRequests = detailInflight.current;
    const off = channel?.onMessage((raw) => {
      const message = raw as unknown as { type?: string; payload?: unknown };
      const body = asRecord(message.payload);
      if (message.type === "mission_changed") {
        if (body.mission_id === missionId) send();
        return;
      }
      if (message.type === DETAIL_TYPE + "_response") {
        const nodeId = detailInflight.current.get(String(body.request_id));
        if (nodeId === undefined) return;
        detailInflight.current.delete(String(body.request_id));
        if (body.ok !== true) {
          // 明细读不到：卡片保留摘要，下次展开或刷新再读。坏消息（推后第 3 批 U09）要说出来，不悄悄吞掉
          if (body.error_code === PROTOCOL_ERROR) setError(typeof body.error === "string" && body.error ? body.error : PROTOCOL_ERROR_TEXT);
          return;
        }
        try {
          const detail = parseTurnDetail(body.data, missionId, nodeId);
          setDetails((current) => new Map(current).set(nodeId, detail));
        } catch (caught) {
          setError(caught instanceof Error ? caught.message : "回合明细响应无效");
        }
        return;
      }
      if (message.type !== SNAPSHOT_TYPE + "_response") return;
      const pending = inflight.current;
      if (!pending || body.request_id !== pending.id) return;
      inflight.current = null;
      if (timeout.current) clearTimeout(timeout.current);
      if (body.ok !== true) {
        if (body.error_code === "SNAPSHOT_CHANGED" && !pending.restarted) { sendPage(null, { pages: [], restarted: true }); return; }
        setError(typeof body.error === "string" && body.error ? body.error : "任务过程读取失败，请稍后重试");
      } else {
        try {
          const page = parseExecutionPage(body.data, missionId);
          const pages = [...pending.pages, page];
          if (!page.complete && page.next_cursor && pages.length < MAX_PAGES) {
            sendPage(page.next_cursor, { pages, restarted: pending.restarted });
            return;
          }
          setView(mergePages(pages));
          setError(pages.length >= MAX_PAGES && !page.complete ? `任务过程太长，只显示了前 ${MAX_PAGES} 页` : null);
        } catch (caught) {
          setError(caught instanceof Error ? caught.message : "任务过程响应无效");
        }
      }
      if (again.current) { again.current = false; send(); }
    });
    const offState = channel?.onStateChange?.((state) => {
      inflight.current = null; again.current = false; detailInflight.current.clear();
      if (state === "connected") send();
    });
    return () => {
      off?.(); offState?.();
      if (timer.current) clearTimeout(timer.current);
      if (timeout.current) clearTimeout(timeout.current);
      timer.current = null; inflight.current = null; detailRequests.clear();
    };
  }, [channel, missionId, send, sendPage]);

  // 任务刚结束：再读一次拿到收尾的卡片
  useEffect(() => { if (ended) send(); }, [ended, send]);

  const [story, setStory] = useState<Story | null>(null);
  useEffect(() => {
    if (!view) return;
    const next = storyFromExecution(view, details);
    // eslint-disable-next-line react-hooks/set-state-in-effect -- derived from the two reads, cards reused by content
    setStory((previous) => ({ ...next, cards: reuseCards(previous?.cards, next.cards) }));
  }, [view, details]);

  const stepsKey = story ? JSON.stringify(story.steps) : "";
  // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed by content, not identity
  const names = useMemo(() => stepNames(story?.steps ?? []), [stepsKey]);
  const storyRef = useRef<Story | null>(null);
  useEffect(() => { storyRef.current = story; }, [story]);
  // stable: a new story must not re-render every memoised card through a new callback
  const onToggle = useCallback((id: string) => setToggled((current) => {
    const next = new Map(current);
    const card = storyRef.current?.cards.find((c) => c.id === id);
    const auto = !!card && card.kind !== "event" && card.state === "running" && !endedRef.current;
    next.set(id, !(current.get(id) ?? auto));
    return next;
  }), []);
  const setAll = (open: boolean) => setToggled(new Map((story?.cards ?? []).map((c) => [c.id, open])));

  const cards: Card[] = useMemo(() => story?.cards ?? [], [story]);
  // 展开着的模型回合读明细：没读过就读；还在进行的随每次过程重读刷新
  const executionHash = view?.execution_hash ?? "";
  useEffect(() => {
    for (const card of cards) {
      if (card.kind === "event") continue;
      const open = toggled.get(card.id) ?? (card.state === "running" && !ended);
      if (!open) continue;
      if (card.items === null || card.state === "running" || card.state === "waiting") askDetail(card.id);
    }
  }, [cards, toggled, ended, executionHash, askDetail]);
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (ended) return undefined;
    const tick = setInterval(() => setNow(Date.now() / 1000), 30000);
    return () => clearInterval(tick);
  }, [ended]);
  // 进行中的回合 10 分钟没有新动作：只是提示，不改状态
  const stalled = useMemo(() => new Set(ended ? [] : cards.filter((c) => c.kind !== "event" && c.state === "running"
    && now - c.at > STALL_SECONDS).map((c) => c.id)), [cards, now, ended]);
  useEffect(() => { onStalled?.(stalled.size); }, [onStalled, stalled.size]);
  return (
    <section className="mission-story" aria-label="任务过程" data-testid="mission-story">
      <div className="ms-bar">
        <strong className="ms-headline" role="status">{story ? headline(story, names, missionStatus) : "正在读取任务过程…"}</strong>
        {cards.length > 0 && <>
          <button type="button" onClick={() => setAll(true)}>全部展开</button>
          <button type="button" onClick={() => setAll(false)}>全部收起</button>
        </>}
      </div>
      <p className="ms-muted ms-hint">每张卡片是一个模型回合：规划器拆分任务，执行者完成一个步骤，审阅员检查结果。点卡片看它具体做了什么。</p>
      {error && <p role="alert" className="ms-error">{error}</p>}
      {story && cards.length === 0 && <p className="ms-muted">{missionStatus === "CREATED" ? "还没开始：确认完成要求后，模型的每一步会出现在这里。" : "还没有模型开始工作。"}</p>}
      <ol className="ms-list">
        {cards.map((card) => card.kind === "event"
          ? <EventView key={card.id} card={card} names={names} />
          : <TurnView key={card.id} card={card} names={names} missionEnded={ended} onToggle={onToggle} stalled={stalled.has(card.id)}
              open={toggled.get(card.id) ?? (card.state === "running" && !ended)} />)}
      </ol>
    </section>
  );
}

export default MissionStory;
