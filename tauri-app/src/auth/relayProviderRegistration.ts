// SPDX-FileCopyrightText: 2026 DennyWanye
// SPDX-License-Identifier: BUSL-1.1

/**
 * relayProviderRegistration — WI-3 registry mirror for relay mode.
 *
 * The relay device key lives in RelayAuthAdapter/keyring. This module mirrors
 * the current stable key into the backend provider registry through the
 * injected control channel, without depending on any concrete channel class.
 */
import type { RelayAuthAdapter } from "./RelayAuthAdapter";
import { pickModel } from "./relayProviderBridge";
import type { Provider, User } from "./types";

const RELAY_PROVIDER_ID = "relay-cloud";

type EnsureReason = "login" | "restore" | "recover";
type ControlChannel = { send: (m: unknown) => void };

export class RelayProviderRegistration {
  private getChannel: (() => ControlChannel | null) | null = null;
  private onFatal: ((msg: string) => void) | null = null;
  private inflight: Promise<void> | null = null;
  private lastEnsured: { accountRef: string; keyPresent: boolean } | null = null;
  private recoverHits: number[] = [];

  attach(
    getChannel: () => ControlChannel | null,
    onFatal: (msg: string) => void,
  ): void {
    this.getChannel = getChannel;
    this.onFatal = onFatal;
  }

  ensure(
    adapter: RelayAuthAdapter,
    reason: EnsureReason = "login",
    force = false,
  ): Promise<void> {
    const run = () => this.ensureOnce(adapter, reason, force);
    const p = (this.inflight ?? Promise.resolve()).then(run, run);
    this.inflight = p;
    p.finally(() => {
      if (this.inflight === p) this.inflight = null;
    });
    return p;
  }

  recover(adapter: RelayAuthAdapter): Promise<void> {
    const now = Date.now();
    this.recoverHits = this.recoverHits.filter((t) => now - t < 60_000);
    if (this.recoverHits.length >= 2) {
      this.recoverHits = [];
      this.onFatal?.("中转站 key 反复失效，请重新登录或检查余额");
      return Promise.resolve();
    }
    this.recoverHits.push(now);
    return this.ensure(adapter, "recover", true);
  }

  private async ensureOnce(
    adapter: RelayAuthAdapter,
    reason: EnsureReason,
    force: boolean,
  ): Promise<void> {
    const user: User | null = adapter.currentUser();
    const acct = user?.id ?? "";
    const ok =
      !!this.lastEnsured &&
      this.lastEnsured.accountRef === acct &&
      this.lastEnsured.keyPresent;
    if (reason === "restore" && ok && !force) return;

    const synced = await adapter.syncDeviceKey({ force: force || !ok });
    if (!synced) {
      console.warn("[reg] no device key");
      return;
    }

    const meta: Provider | null = await adapter.fetchRelayProviderMeta();
    const models = (meta?.models ?? []).map((m) => m.id);
    if (!meta || !models.length) {
      console.warn("[reg] empty models");
      return;
    }

    const ch = this.getChannel?.();
    if (!ch) {
      console.warn("[reg] no channel");
      return;
    }

    ch.send({
      type: "settings_providers_ensure",
      payload: {
        id: RELAY_PROVIDER_ID,
        source: "relay",
        account_ref: acct,
        name: "中转站 · chinzy",
        base_url: meta.base_url,
        models,
        default_model: pickModel(meta),
        api_key: synced.key,
      },
    });
    this.lastEnsured = { accountRef: acct, keyPresent: true };
  }

  onLogout(): void {
    this.lastEnsured = null;
    this.recoverHits = [];
  }
}

export const relayProviderRegistration = new RelayProviderRegistration();
