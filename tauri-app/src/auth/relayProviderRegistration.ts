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
import { updateCloudConfig } from "../bindings/config";
import { pickModel } from "./relayProviderBridge";
import type { Provider, User } from "./types";

const RELAY_PROVIDER_ID = "relay-cloud";

type EnsureReason = "login" | "restore" | "recover";

/** Why a registration attempt did not complete. Surfaced to the UI so the
 * reset-key button (and login flow) can show an actionable message instead
 * of failing silently (the historical bug: expired token → empty models →
 * silent `return`, user left wondering whether anything happened). */
export type EnsureFailReason =
  | "not_logged_in" // no current relay user / account ref
  | "no_channel" // backend control WS not connected yet
  | "no_device_key" // relay refused to (re)issue a device key — auth likely expired
  | "empty_models" // relay /models returned nothing — key invalid / 401
  | "repeated_failure" // rate-limited: key kept failing within the window
  | "error"; // unexpected exception while talking to the relay

export type EnsureResult =
  | { ok: true }
  | { ok: false; reason: EnsureFailReason; detail?: string };

// Loose `any` on send so the real control channel (whose send takes a typed
// OutgoingMessage) is assignable here without a contravariance error.
type ControlChannel = { send: (m: any) => void };

/** Only the adapter surface registration actually needs — keeps the module
 * decoupled and lets callers/tests pass a narrow stub. A full
 * RelayAuthAdapter is structurally assignable. */
type RegistrationAdapter = Pick<
  RelayAuthAdapter,
  "currentUser" | "syncDeviceKey" | "fetchRelayProviderMeta"
>;

export class RelayProviderRegistration {
  private getChannel: (() => ControlChannel | null) | null = null;
  private onFatal: ((msg: string) => void) | null = null;
  private inflight: Promise<EnsureResult> | null = null;
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
    adapter: RegistrationAdapter,
    reason: EnsureReason = "login",
    force = false,
  ): Promise<EnsureResult> {
    const run = () => this.ensureOnce(adapter, reason, force);
    // Chain after any in-flight attempt so concurrent ensures serialize, but
    // each caller still gets ITS OWN run's result (the button awaits this).
    const p: Promise<EnsureResult> = (
      this.inflight ?? Promise.resolve<EnsureResult>({ ok: true })
    ).then(run, run);
    this.inflight = p;
    p.finally(() => {
      if (this.inflight === p) this.inflight = null;
    });
    return p;
  }

  recover(adapter: RegistrationAdapter): Promise<EnsureResult> {
    const now = Date.now();
    this.recoverHits = this.recoverHits.filter((t) => now - t < 60_000);
    if (this.recoverHits.length >= 2) {
      this.recoverHits = [];
      this.onFatal?.("中转站 key 反复失效，请重新登录或检查余额");
      return Promise.resolve<EnsureResult>({ ok: false, reason: "repeated_failure" });
    }
    this.recoverHits.push(now);
    return this.ensure(adapter, "recover", true);
  }

  private async ensureOnce(
    adapter: RegistrationAdapter,
    reason: EnsureReason,
    force: boolean,
  ): Promise<EnsureResult> {
    const user: User | null = adapter.currentUser();
    const acct = user?.id ?? "";
    if (!acct) return { ok: false, reason: "not_logged_in" };
    const ok =
      !!this.lastEnsured &&
      this.lastEnsured.accountRef === acct &&
      this.lastEnsured.keyPresent;
    // Idempotent no-op on restore when already ensured — treated as success.
    if (reason === "restore" && ok && !force) return { ok: true };

    // Channel check FIRST — on cold start the `login` event (from
    // restoreSession / auto-login) can fire before the control WS is
    // connected. Aborting here (before syncDeviceKey) avoids a wasted
    // device-key rotation; App.tsx re-triggers ensure on ws "connected".
    const ch = this.getChannel?.();
    if (!ch) {
      console.warn("[reg] no channel (will retry on ws connect)");
      return { ok: false, reason: "no_channel" };
    }

    try {
      const synced = await adapter.syncDeviceKey({ force: force || !ok });
      if (!synced) {
        console.warn("[reg] no device key");
        return { ok: false, reason: "no_device_key" };
      }

      const meta: Provider | null = await adapter.fetchRelayProviderMeta();
      const models = (meta?.models ?? []).map((m) => m.id);
      if (!meta || !models.length) {
        console.warn("[reg] empty models");
        return { ok: false, reason: "empty_models" };
      }

      ch.send({
        type: "settings_providers_ensure",
        payload: {
          id: RELAY_PROVIDER_ID,
          source: "relay",
          account_ref: acct,
          enabled: true,
          name: "中转站 · chinzy",
          base_url: meta.base_url,
          models,
          default_model: pickModel(meta),
          api_key: synced.key,
        },
      });
      try {
        await updateCloudConfig("", {
          base_url: meta.base_url,
          model: pickModel(meta),
          api_key: synced.key,
          persist_key: false,
        });
      } catch (e) {
        console.warn("[reg] live cloud config update failed", e);
      }
      this.lastEnsured = { accountRef: acct, keyPresent: true };
      return { ok: true };
    } catch (e) {
      console.warn("[reg] ensure error", e);
      return {
        ok: false,
        reason: "error",
        detail: e instanceof Error ? e.message : String(e),
      };
    }
  }

  onLogout(): void {
    this.lastEnsured = null;
    this.recoverHits = [];
  }
}

export const relayProviderRegistration = new RelayProviderRegistration();
