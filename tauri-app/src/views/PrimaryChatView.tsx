import { useEffect, useRef, useState } from "react";
import type { ControlChannel } from "../ws/ControlChannel";
import { InputBar } from "../code-panel/InputBar";
import { ChangeModelModal } from "../code-panel/ChangeModelModal";
import { MarkdownMessage } from "../components/MarkdownMessage";
import { SessionClient, SESSION_ID, record, rows, text, type Json } from "../primary/sessionClient";
import { ChatMissionCard } from "./ChatMissionCard";
import { ChatMissionNotices } from "./ChatMissionNotices";
import "./PrimaryChatView.styles.css";

export interface PrimaryChatViewProps {
  channel?: ControlChannel | null;
  onOpenSettings?: () => void;
  active?: boolean;
}
function planningChoices(request: Json): { key: string; label: string }[] {
  if (!Array.isArray(request.options)) return [];
  return request.options
    .map((option) => {
      const row = record(option);
      return { key: text(row.key), label: text(row.label) || text(row.key) };
    })
    .filter((option) => option.key);
}
export function PrimaryChatView({ channel, onOpenSettings, active = true }: PrimaryChatViewProps) {
  const client = useRef<SessionClient | null>(null);
  const cursor = useRef(0);
  const deltaScope = useRef("");
  const [turns, setTurns] = useState<Json[]>([]);
  const [tasks, setTasks] = useState<Json[]>([]);
  const [inbox, setInbox] = useState<Json[]>([]);
  const acknowledged = useRef(new Set<string>());
  const [requests, setRequests] = useState<Record<string, Json>>({});
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [showModel, setShowModel] = useState(false);
  const [connected, setConnected] = useState(channel?.state === "connected");
  const input = useRef<{ text: string; key: string } | null>(null);
  const history = useRef<HTMLDivElement>(null);
  const refresh = useRef<() => void>(() => {});
  const mergeTurns = (incoming: Json[]) =>
    setTurns((previous) => {
      const byId = new Map(previous.map((turn) => [text(turn.turn_id), turn]));
      incoming.forEach((turn) =>
        byId.set(text(turn.turn_id), { ...byId.get(text(turn.turn_id)), ...turn }),
      );
      return Array.from(byId.values());
    });
  useEffect(() => {
    if (!channel) return;
    let live = true;
    let reading = false;
    let readAgain = false;
    let historyAgain = false;
    const transport = new SessionClient(channel);
    client.current = transport;
    const report = (err: unknown) => {
      if (live) setError(err instanceof Error ? err.message : "读取失败");
    };
    const read = async (includeHistory = false) => {
      if (reading) {
        readAgain = true;
        historyAgain ||= includeHistory;
        return;
      }
      reading = true;
      try {
        const [taskData, inboxData, historyData] = await Promise.all([
          transport.request("tasks"),
          transport.request("inbox"),
          includeHistory ? transport.request("history") : Promise.resolve(null),
        ]);
        if (!live) return;
        setTasks(rows(taskData));
        rows(inboxData)
          .filter((row) => row.acked === true)
          .forEach((row) => acknowledged.current.add(text(row.event_id)));
        setInbox((previous) => {
          const merged = new Map(previous.map((row) => [text(row.event_id), row]));
          rows(inboxData).forEach((row) => merged.set(text(row.event_id), row));
          return Array.from(merged.values()).filter(
            (row) => !acknowledged.current.has(text(row.event_id)),
          );
        });
        if (historyData) mergeTurns(rows(historyData));
      } catch (err) {
        report(err);
      } finally {
        reading = false;
        if (live && readAgain) {
          const history = historyAgain;
          readAgain = historyAgain = false;
          void read(history);
        }
      }
    };
    const connect = () => {
      setConnected(true);
      void transport.request("events", { after: cursor.current }).catch(report);
      void read(true);
    };
    refresh.current = () => void read(true);
    const off = channel.onMessage((incoming) => {
      const message = record(incoming);
      const event = record(message.payload);
      if (
        message.type !== "session_event" ||
        event.session_id !== SESSION_ID ||
        event.schema_version !== 1
      )
        return;
      const seq = Number(event.seq);
      if (!Number.isSafeInteger(seq) || seq <= cursor.current) return;
      cursor.current = seq;
      const payload = record(event.payload);
      switch (event.kind) {
        case "text_delta":
          {
            const scope = JSON.stringify([event.turn_id, payload.generation]);
            const continued = deltaScope.current === scope;
            deltaScope.current = scope;
            setPreview((previous) => (continued ? previous : "") + text(payload.text));
          }
          break;
        case "turn_committed":
          deltaScope.current = "";
          setPreview("");
          mergeTurns([
            {
              turn_id: event.turn_id ?? payload.turn_id,
              text: payload.text,
              ...(input.current ? { input: input.current.text } : {}),
            },
          ]);
          void read(true);
          break;
        case "needs_response":
          setRequests((previous) => ({
            ...previous,
            [text(payload.request_id)]: { ...payload, mission_id: event.mission_id },
          }));
          void read();
          break;
        case "result_ready":
          if (acknowledged.current.has(text(payload.event_id))) break;
          setInbox((previous) => [
            ...previous.filter((item) => item.event_id !== payload.event_id),
            payload,
          ]);
          void read();
          break;
        case "delegated":
        case "task_progress":
          void read();
          break;
        case "error":
          setError(text(payload.code) || "会话执行失败");
          break;
      }
    });
    const offState = channel.onStateChange((state) => {
      if (state === "connected") connect();
      else setConnected(false);
    });
    if (channel.state === "connected") connect();
    return () => {
      live = false;
      off();
      offState();
      transport.close();
      client.current = null;
    };
  }, [channel]);
  useEffect(() => {
    history.current?.scrollTo?.({ top: history.current.scrollHeight });
  }, [turns, preview]);
  const call = async (operation: string, payload: Json = {}) => {
    if (!client.current || !connected) throw new Error("连接不可用，请稍后重试。");
    return client.current.request(operation, payload);
  };
  const submit = async (value: string, attachments: Json[]) => {
    if (attachments.length) throw new Error("请将资料内容作为文字发送，或交由后台任务处理。");
    if (sending) throw new Error("正在等待当前回复。");
    if (input.current && input.current.text !== value)
      throw new Error("上一条输入结果尚未确认，请先用原文重试。");
    if (!input.current || input.current.text !== value)
      input.current = { text: value, key: crypto.randomUUID() };
    const frozen = input.current;
    setSending(true);
    setError("");
    setPreview("");
    try {
      const reply = record(await call("send", { text: value, idempotency_key: frozen.key }));
      if (reply.state === "failed") {
        input.current = null;
        deltaScope.current = "";
        setPreview("");
        refresh.current();
        throw new Error(text(record(reply.error).code) || "本轮执行失败，可以重新发送。");
      }
      if (reply.state !== "committed")
        throw new Error(text(record(reply.error).code) || "回复未提交，请用原文重试。");
      mergeTurns([{ turn_id: reply.turn_id, input: value, text: reply.text }]);
      input.current = null;
      setPreview("");
      refresh.current();
    } catch (err) {
      setError(err instanceof Error ? err.message : "发送失败");
      throw err;
    } finally {
      setSending(false);
    }
  };
  const perform = async (operation: string, payload: Json, after?: () => void) => {
    try {
      await call(operation, payload);
      after?.();
      setError("");
      refresh.current();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    }
  };
  const respond = (request: Json, decision: string, answer?: string) =>
    void perform(
      "respond",
      {
        request_id: request.request_id,
        version: request.version,
        decision,
        ...(request.request_kind === "planning"
          ? { answer: answer ?? answers[text(request.request_id)] ?? "" }
          : decision === "reject"
            ? { reason: answers[text(request.request_id)] ?? "" }
            : {}),
      },
      () =>
        setRequests((previous) => {
          const next = { ...previous };
          delete next[text(request.request_id)];
          return next;
        }),
    );
  const pendingRequests = Object.values(requests).filter((request) => {
    const task = tasks.find((row) => row.mission_id === request.mission_id);
    if (!task) return true;
    if (["COMPLETED", "FAILED", "CANCELLED", "REJECTED"].includes(text(task.status))) return false;
    const detail = record(task.detail);
    const planning = request.request_kind === "planning";
    return rows(planning ? detail.planning_questions : detail.approvals).some(
      (row) =>
        (planning ? row.decision_id : row.request_id) === request.request_id &&
        row.state === "PENDING" &&
        (!planning || String(row.version) === String(request.version)),
    );
  });
  return (
    <section data-testid="view-chat" className="sh-PrimaryChatView-1">
      <header data-testid="chat-header" className="sh-PrimaryChatView-2">
        <h1 className="sh-PrimaryChatView-3">主对话</h1>
        <button onClick={() => setShowModel(true)}>切换模型</button>
        <button onClick={onOpenSettings}>模型与设置</button>
        <button onClick={() => refresh.current()} disabled={!connected}>
          刷新状态
        </button>
      </header>
      {showModel && <ChangeModelModal onClose={() => setShowModel(false)} />}
      <div role="status" className="sh-PrimaryChatView-6">
        {!connected ? "连接未就绪" : sending ? "正在回复" : "可以发送消息"}
      </div>
      {error && (
        <p role="alert" className="sh-PrimaryChatView-8">
          {error}
        </p>
      )}
      <div
        aria-label="主对话历史"
        ref={history}
        style={{ overflowY: "auto", flex: 1, minHeight: 0, padding: 24 }}
      >
        {turns.map((turn) => (
          <div key={text(turn.turn_id)} className="sh-PrimaryChatView-9">
            {text(turn.input) && (
              <div aria-label="用户消息">
                <strong>你</strong>
                <MarkdownMessage>{text(turn.input)}</MarkdownMessage>
              </div>
            )}
            <div aria-label="助手消息">
              <strong>助手</strong>
              <MarkdownMessage>{text(turn.text)}</MarkdownMessage>
            </div>
          </div>
        ))}
        {sending && input.current && <p aria-label="正在发送">{input.current.text}</p>}
        {preview && (
          <div aria-label="回复预览">
            <MarkdownMessage>{preview}</MarkdownMessage>
          </div>
        )}
        {tasks.map((task) => (
          <ChatMissionCard
            key={text(task.delegation_id) || text(task.mission_id)}
            task={task}
            cancel={() =>
              void perform("cancel_task", { task_id: task.delegation_id || task.mission_id })
            }
            inspect={() =>
              void call("task", { task_id: task.delegation_id || task.mission_id })
                .then((value) =>
                  setTasks((previous) =>
                    previous.map((row) =>
                      row.delegation_id === task.delegation_id ? record(value) : row,
                    ),
                  ),
                )
                .catch((err) => setError(String(err)))
            }
          />
        ))}
      </div>
      {active && (
        <div className="sh-PrimaryChatView-13">
          {pendingRequests.map((request) => (
            <div key={text(request.request_id)} aria-label="需要你回答">
              <p>
                {text(request.question) ||
                  text(request.summary) ||
                  text(request.reason) ||
                  "后台任务需要你确认"}
              </p>
              {(request.request_kind !== "planning" || planningChoices(request).length === 0) && (
                <input
                  aria-label="回答或拒绝理由"
                  value={answers[text(request.request_id)] ?? ""}
                  onChange={(event) =>
                    setAnswers((previous) => ({
                      ...previous,
                      [text(request.request_id)]: event.target.value,
                    }))
                  }
                />
              )}
              {request.request_kind === "planning" ? (
                planningChoices(request).length ? (
                  planningChoices(request).map((option) => (
                    <button key={option.key} onClick={() => respond(request, "answer", option.key)}>
                      {option.label}
                    </button>
                  ))
                ) : (
                  <button onClick={() => respond(request, "answer")}>提交回答</button>
                )
              ) : (
                <>
                  <button onClick={() => respond(request, "approve")}>批准</button>
                  <button
                    onClick={() => respond(request, "reject")}
                    disabled={!answers[text(request.request_id)]?.trim()}
                  >
                    拒绝
                  </button>
                </>
              )}
            </div>
          ))}
          <ChatMissionNotices
            notices={inbox}
            acknowledge={(eventId) =>
              void perform("ack", { event_id: eventId }, () => {
                acknowledged.current.add(eventId);
                setInbox((previous) => previous.filter((item) => item.event_id !== eventId));
              })
            }
          />
        </div>
      )}
      <InputBar
        disabled={!connected}
        primary={{ submit, ...(sending ? { stop: () => void perform("interrupt", {}) } : {}) }}
      />
    </section>
  );
}
