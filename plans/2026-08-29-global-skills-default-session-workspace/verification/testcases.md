# Frozen black-box testcases：Global Skill / ordinary workspace
## Immutable fixture

- ID: `GS-FX-IMMUTABLE-01`
- Repository: `https://github.com/DennyWanye/plan-test-skill`
- Exact commit: `3a094db39db558dc72127938a377dccd8463c475`
- Archive URL: `https://github.com/DennyWanye/plan-test-skill/archive/3a094db39db558dc72127938a377dccd8463c475.zip`
- Archive SHA-256: `c576ab2cc92b3b0a56e5dcaa31f509850b0d814fcf4b4429eba216040cf6b861`
- Archive bytes: `836188`
- Selected Skill subdirectory/name: `skills/plan-test` / `plan-test`
- Required runtime oracle: fresh Run 的 `tool_search` 能返回 exact managed manifest/content identity，随后 `skill_invoke` 成功载入该 Skill；不得用目录存在代替。

## Exact automated entrypoints

```bash
backend/.venv/bin/python -m pytest backend/tests/session/test_default_workspace_allocation.py backend/tests/session/test_default_workspace_faults.py backend/tests/capabilities/test_global_skill_install.py backend/tests/capabilities/test_global_catalog_snapshot.py backend/tests/sdk_adapters/test_global_descriptor_authorization.py backend/tests/test_permission_mode_migration.py -q
npm --prefix tauri-app test -- --run src/components/ordinarySessionWorkspace.test.tsx src/components/globalSkillInstall.test.tsx src/components/permissionMode.test.tsx
cargo test --manifest-path tauri-app/src-tauri/Cargo.toml documents_resolver
backend/.venv/bin/python plans/2026-08-29-global-skills-default-session-workspace/verification/global_workspace_skill_probe.py --fixture plans/2026-08-29-global-skills-default-session-workspace/verification/fixture.json --evidence-root .local-test-evidence/2026-08-29/<run-id>
backend/.venv/bin/python scripts/baseline_runner.py --config baseline-shards.json --run-dir .local-test-evidence/2026-08-29/<run-id>/baseline --known-failures baseline-known-failures.json
```

## Manual environment invariant

- unique ignored `DESKPET_USER_DATA_DIR` and unused backend/Vite ports;
- set `DESKPET_BACKEND_DIR` to this checkout and `DESKPET_PYTHON` to its venv;
- launch only Tauri; it owns backend and Vite;
- use current build visible UI and existing authorized Provider credentials without printing/copying secrets;
- each action records pre-screenshot, declared coordinates/action/expectation, post-screenshot, visible terminal result, session/root/operation/catalog IDs and SHA-256 under `.local-test-evidence/2026-08-29/<run-id>/`.

## Required cases

| TC | Scenario | Exact procedure and decisive oracle |
|---|---|---|
| TC-GS-01 | S-GS-01 | Fresh profile → click new ordinary Session → choose default. Assert exactly one new Documents/SimpleHarnessProjects child; UI root, persisted canonical identity and real `pwd`/canary write agree. Quit app and owned children, relaunch same profile, root and Session ID unchanged. |
| TC-GS-02 | S-GS-02 | Three lanes: select precreated Unicode folder (no default child); cancel picker (one default child); select deleted/file/read-only target (visible error, zero Session/binding/default-dir delta). |
| TC-GS-03 | S-GS-03 | Settings with no explicit Project installs immutable fixture; Chat in selected-folder Session repeats exact request. Require progress then success only after terminal user-global receipt/runtime verification; one active binding; no `.claude/skills`, `.codex/skills`, per-Session or legacy directory authority. |
| TC-GS-04 | S-GS-04 | Existing-before-install, automatic-after-install, selected-after-install Sessions each start distinct fresh root, search and invoke exact fixture through real Provider. In-flight generation N may remain; next fresh Run must use N+1 without backend restart. |
| TC-GS-05 | S-GS-05 | Fully quit app and owned backend/Vite; verify new process incarnation after relaunch, same profile and no reinstall. Three Sessions retain roots/global generation/descriptor digest and each completes new real Provider Skill invocation. |
| TC-GS-06 | S-GS-06 | Automated faults at fetch, validate, publish intent, materialize, catalog swap, commit-before-ACK, runtime verify, mkdir, binding persist and receipt persist. Hash DB/catalog/tree before/after; only full-old/full-new/fenced-unknown allowed, never half Session or mixed Skill. Retry converges. |
| TC-GS-07 | S-GS-07 | Three Session classes search/describe identical descriptor identities containing built-in, installed Skill and configured unhealthy/platform-ineligible external Tool. Callable subsets differ only by eligibility. Invoke unavailable and confirm-only/deny examples; real structured reason and zero unauthorized effect required. |
| TC-GS-08 | S-GS-08 | 16 concurrent default Session creates plus two concurrent and one lost-ACK identical install replay. Unique canonical dirs/Sessions, one active Skill generation, stable receipt, no orphan allocation or copies. |
| TC-GS-09 | S-GS-09 | Fresh profile shows auto/factory-default; fresh+continuation use it. Switch manual/user-explicit; cold restart preserves. Auto-eligible succeeds while confirm-only still prompts, deny has zero effect, unhealthy/platform remains unavailable. |

Direct WebSocket injection, backend imports/DB mutation, script replay, or log-only inference never substitutes for required UI/real Provider evidence.
