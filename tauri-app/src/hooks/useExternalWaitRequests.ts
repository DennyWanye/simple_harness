// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

import { useCallback, useEffect, useRef, useState } from "react";

import type {
  ExternalWaitRequest,
  ExternalWaitResponse,
} from "../types/skillPlatform";
import type { ControlChannel } from "../ws/ControlChannel";

type Payload = ExternalWaitRequest["payload"];

/** Keep external OS/app waits separate from DeskPet authorization prompts.
 * Auto authorization never resolves this queue: only an explicit user
 * handled signal resumes the exact durable Attempt so the model can verify the
 * real external result. */
export function useExternalWaitRequests(channel: ControlChannel | null) {
  const [current, setCurrent] = useState<Payload | null>(null);
  const currentRef = useRef<Payload | null>(null);
  const queueRef = useRef<Payload[]>([]);

  const setActive = useCallback((value: Payload | null) => {
    currentRef.current = value;
    setCurrent(value);
  }, []);

  const showNext = useCallback(() => {
    setActive(queueRef.current.shift() ?? null);
  }, [setActive]);

  useEffect(() => {
    if (!channel) return undefined;
    return channel.onMessage((message) => {
      if (message.type !== "external_wait_request") return;
      const payload = (message as ExternalWaitRequest).payload;
      if (!payload?.run_id || !payload.decision_id || !payload.nonce) return;
      if (currentRef.current === null) {
        setActive(payload);
      } else if (
        currentRef.current.decision_id !== payload.decision_id &&
        !queueRef.current.some(
          (item) => item.decision_id === payload.decision_id,
        )
      ) {
        queueRef.current.push(payload);
      }
    });
  }, [channel, setActive]);

  const complete = useCallback(() => {
    const value = currentRef.current;
    if (!value || !channel) return;
    const response: ExternalWaitResponse = {
      type: "external_wait_response",
      payload: {
        session_id: value.session_id,
        run_id: value.run_id,
        request_id: value.request_id,
        decision_id: value.decision_id,
        nonce: value.nonce,
        version: value.version,
        wait_ref: value.wait_ref,
      },
    };
    channel.send(response);
    showNext();
  }, [channel, showNext]);

  return { current, complete } as const;
}
