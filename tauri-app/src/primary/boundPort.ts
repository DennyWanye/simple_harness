// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1
import type { ControlChannel } from "../ws/ControlChannel";
import type { PrimaryPort } from "./controller";

/** Reuse App's identity_bind connection, not the companion-action broadcast socket. */
export function boundPrimaryPort(channel: ControlChannel): PrimaryPort {
  return {
    send_command: (message) => channel.send(message),
    state: () => channel.state,
    on_state_change: (listener) => channel.onStateChange(listener),
    on_message(listener) {
      const off = channel.onMessage(listener);
      let active = true;
      queueMicrotask(() => {
        // Read at delivery time so a disconnect/unbind cannot replay an old lease.
        const bound = channel.state === "connected" ? channel.getLatestMessage("companion_profile_bound") : null;
        if (active && bound) listener(bound);
      });
      return () => { active = false; off(); };
    },
  };
}
