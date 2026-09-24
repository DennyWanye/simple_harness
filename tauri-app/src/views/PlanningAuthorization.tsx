// SPDX-License-Identifier: BUSL-1.1
import { useEffect, useRef, useState } from "react";
import { asRecord, asText, newRequestKey, type MissionsChannel } from "../stores/missionsStore";

type Json = Record<string, unknown>;

export function PlanningAuthorization({ requests, channel, onChanged }: {
  requests: Json[]; channel: MissionsChannel | null; onChanged: () => void;
}) {
  return <section aria-label="规划授权">
    {requests.map(request => <Request key={asText(request.request_id)} request={request}
      channel={channel} onChanged={onChanged} />)}
  </section>;
}

function Request({ request, channel, onChanged }: {
  request: Json; channel: MissionsChannel | null; onChanged: () => void;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const flight = useRef<string | null>(null);
  const command = useRef<string>(newRequestKey());
  const changed = useRef(onChanged);
  useEffect(() => { changed.current = onChanged; }, [onChanged]);
  useEffect(() => {
    flight.current = null;
    setPending(false);
    if (!channel) return;
    return channel.onMessage(incoming => {
      const message = incoming as unknown as { type?: unknown; payload?: unknown };
      const payload = asRecord(message.payload);
      if (message.type !== "mission_planning_authorization_response" || payload.request_id !== flight.current) return;
      flight.current = null;
      setPending(false);
      if (payload.ok === true) { setError(""); changed.current(); }
      else setError(asText(payload.error) || "授权未完成，请重试");
    });
  }, [channel]);
  useEffect(() => {
    if (!pending || !flight.current) return;
    const requestId = flight.current;
    const timer = setTimeout(() => {
      if (flight.current !== requestId) return;
      flight.current = null;
      setPending(false);
      setError("等待响应超时，可使用原授权请求重试");
    }, 30000);
    return () => clearTimeout(timer);
  }, [pending]);
  const authorize = () => {
    if (!channel || pending) return;
    flight.current = newRequestKey();
    setPending(true);
    setError("");
    try {
      if (channel.send({ type: "mission_planning_authorization", request_id: flight.current, payload: {
        operation: "issue", mission_id: request.mission_id, request_id: request.request_id,
        command_id: command.current,
      } })) return;
    } catch { /* The same command ID recovers a committed response after reconnect. */ }
    flight.current = null;
    setPending(false);
    setError("连接不可用，授权结果尚未确认；请重试");
  };
  return <div>
    <p style={{ margin: "4px 0" }}>授权后任务开始规划；涉及外部操作时仍会按原有权限再问你。</p>
    <button type="button" disabled={pending || !channel} onClick={authorize}>
      {pending ? "正在授权…" : "授权本轮规划"}
    </button>
    {error && <p role="alert">{error}</p>}
  </div>;
}
