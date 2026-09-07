/** Recovery references only: never a cached grant, decision, nonce or root path.
 * Owned by the stable primary view, so card/connection teardown cannot discard
 * the last attempted challenge in that exact owner + primary namespace.
 */
export const bindingIdentityKeys = ["primary_ref", "run_ref", "sdk_run_ref", "generation", "effect_ref",
  "challenge_ref", "challenge_hash", "scope_ref", "proposal_hash"] as const;
export type BindingIdentity = Record<Exclude<(typeof bindingIdentityKeys)[number], "generation">, string> & {
  generation: number;
};
export class PrimaryBindingRecovery {
  private entries = new Map<string, Readonly<BindingIdentity>>();
  read(owner: string, primary: string): Readonly<BindingIdentity> | undefined {
    return this.entries.get(JSON.stringify([owner, primary]));
  }
  remember(owner: string, item: BindingIdentity): void {
    // Copy only exact identity fields. The UI must obtain every displayed
    // result again from status under the newly verified connection.
    const target = Object.fromEntries(bindingIdentityKeys.map(key => [key, item[key]])) as BindingIdentity;
    this.entries.set(JSON.stringify([owner, item.primary_ref]), Object.freeze(target));
  }
}
