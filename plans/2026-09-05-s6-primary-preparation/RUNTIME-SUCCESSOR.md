# Runtime successor composition preparation

2026-09-05. The currently installed0.6.7 path remains usable.

HumanMemoryV7Runtime.manager prepares the SDK-owned schema upgrade only when an
existing database file and the new public migration capability both exist. Under
the existing runtime lock, before any new manager handle, await the public
migrate_human_memory_v7_to_v7_2(db_path, backup_path=...). The backup is a sibling
`<db.name>.pre-schema-7.2.backup`; keep it after upgrade. Store the returned receipt
on the runtime. No Host SDK-schema query, silent reset or private DDL.

The current0.6.7 SDK has no new upgrade function, so it retains its own initializer
behavior. Existing0.6.7 reopen was exercised by the real typed-outbound pair while
this preparation was present. The0.6.9 upgrade branch is not yet exercised here;
its wheel and actual old-data integration are pending. Do not claim old DB compatibility.

An optional memory_action_authority is forwarded to the SDK public builder only
when explicitly supplied. Main has not yet supplied the new semantic-correction
object in production; the Carver leaf, explicit intent controls and exact recovery
need to be completed/reviewed before that composition. No auto-approval fallback.
