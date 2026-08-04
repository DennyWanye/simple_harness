import { invoke } from "@tauri-apps/api/core";
import {
  canonicalRequestHash,
  type CanonicalJson,
  parseCanonicalU64,
} from "./controlCommandCanonical";

export type WindowControlScope = "identity_bind" | "companion_action";

export interface WindowControlCredential {
  backendProcessInstanceId: string;
  connectionId: string;
  controlEpoch: string;
  windowLabel: string;
  scope: WindowControlScope;
  challengeHash: string;
  requestSeq: string;
  commandKind: string;
  canonicalRequestHash: string;
  nonce: string;
  issuedAt: string;
  expiresAt: string;
  signatureHex: string;
}

export interface CredentialRequest {
  connectionId: string;
  controlEpoch: string;
  challenge: string;
  requestSeq: string;
  bindingEpoch: string;
  commandKind: string;
  body: CanonicalJson;
  requestedScope: WindowControlScope;
}

export async function getWindowControlCredential(
  request: CredentialRequest,
  invokeFn: typeof invoke = invoke,
): Promise<WindowControlCredential> {
  parseCanonicalU64(request.controlEpoch, "control_epoch");
  parseCanonicalU64(request.requestSeq, "request_seq");
  parseCanonicalU64(request.bindingEpoch, "binding_epoch");
  const requestHash = await canonicalRequestHash(
    request.commandKind,
    request.requestSeq,
    request.bindingEpoch,
    request.body,
  );
  return invokeFn<WindowControlCredential>("get_window_control_credential", {
    connectionId: request.connectionId,
    controlEpoch: request.controlEpoch,
    challenge: request.challenge,
    requestSeq: request.requestSeq,
    commandKind: request.commandKind,
    requestHash,
    requestedScope: request.requestedScope,
  });
}
