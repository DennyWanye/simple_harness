# GLM-5.2 Relay live verification — 2026-07-19

## Outcome

- User-facing model request: `zai-org/GLM-5.2`
- Relay dynamic-catalog alias: `sf-glm-5.2`
- Persistent main model: `sf-glm-5.2`
- Persistent problem-pipeline analysis model: `sf-glm-5.2`
- Final source-Tauri UI reply: `GLM 最终验证通过`

## 1M context pin and DeepResearch rerun

- DeskPet now temporarily pins both `sf-glm-5.2` and the canonical
  `zai-org/GLM-5.2` to a 1,000,000-token nominal context window. The runtime
  profile uses `effective_pct=0.95`, `compact_at_pct=0.75`, and a 384k recall
  sweet spot until relay/provider metadata becomes authoritative.
- Isolated context/model regression: `58 passed`; `py_compile` passed.
- Fresh source-Tauri restart evidence is `tauri-context1.err.log`. Startup and
  request-time resolution repeatedly report
  `model_context_resolved model=sf-glm-5.2 window=1000000 source=builtin` and
  `wi4_0_compaction_enabled context_window=1000000 threshold=0.75 eff_pct=0.95`.
- The real Context usage dialog showed `sf-glm-5.2`, an effective ceiling of
  `950,000`, compaction at `750k`, and recall sweet spot `384k`.
- Computer Use submitted the real root input
  `深入调研：2024年中国总人口和出生人口分别是多少？优先国家统计局。`.
  Run `8109026887b64b309723c004e35c85af` completed as
  `deep_research@v6`; all four relay completions were HTTP 200 with
  `sf-glm-5.2`, with no DeepSeek outbound and no HTTP 402.
- Durable cross-check: SessionDB contains exactly one final assistant with
  `140828 万人`, `954 万人`, and the National Bureau of Statistics citation,
  plus one artifact card. The required session-message, artifact, and final
  status deliveries are all `delivered` in one attempt.
- Current Computer Use exposed the main pet window and the context dialog, but
  not the separately docked `message-panel` as a targetable window. Therefore
  the new run's visible main-window `(完成)` frame is not claimed as a final
  answer screenshot; the earlier T13 full message-panel screenshots remain the
  UI-content evidence, while this incremental run is bound by UI submission,
  runtime log, workflow DB, delivery rows, and SessionDB content.

## Final log gate

The final run used the existing authenticated userdata and restarted source-Tauri after both the repository defaults and the effective userdata config were updated. From the final message baseline onward:

- `sf-glm-5.2` outbound calls: 5
- HTTP 200 responses: 5
- `deepseek-v4-pro` outbound calls: 0
- HTTP 402 responses: 0
- `intent_triage.done`: present
- `chat_v2_final_send_completed`: present

The UI was operated with real coordinate clicks and text input. The final Computer Use capture visibly showed `GLM 最终验证通过`.

## Automated checks

- Relay config/bridge/registration Vitest: `27 passed`
- Problem-pipeline config pytest: `6 passed`
- TypeScript project check: passed
- TOML parse and effective model assertions: passed

## Evidence files

- `tauri.err.log`: initial live alias discovery and first successful GLM reply
- `tauri-restart.err.log`: persistent main-model restart validation
- `tauri-final.err.log`: repository analysis-model change before effective-userdata correction
- `tauri-final2.err.log`: final clean routing evidence
- matching `vite-*.log` and Tauri stdout logs: process-launch evidence

## Non-blocking observation

The assembler classifier hit its existing 8-second timeout once in the final run, then the pipeline continued normally. This did not change the model route or final result, but remains a latency signal worth tracking separately.
