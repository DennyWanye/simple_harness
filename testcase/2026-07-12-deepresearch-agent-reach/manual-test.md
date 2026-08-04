# DeepResearch direct Agent-Reach manual test

## Scope

Verify the latest source build through the real Windows Session UI. Protocol
injection and backend-only scripts are not substitutes for these cases.

## TC-AR-1: Progress and final report

1. Start DeskPet from the current checkout and confirm the log uses the source
   backend directory.
2. Open a normal Session.
3. Submit: 深度调研 https://github.com/Panniantong/Agent-Reach 的设计、doctor
   和渠道机制。请给出完整报告并带引用。
4. Observe the Session while the task runs.

Expected:

- One progress card updates in place; stage updates do not create separate chat
  bubbles.
- The final cited Markdown report appears in the same Session.
- The report does not end with “现有材料不足” and contains substantive doctor
  and channel findings.
- At least one citation resolves to the requested repository URL.

## TC-AR-2: Trace attribution

After TC-AR-1 completes, inspect the matching durable workflow Trace and logs.

Expected:

- route.agent_reach.planned_urls contains the requested URL.
- planned_channels contains github.
- A hit records the same URL, channel github and the actual active backend.
- The persisted JSON contains no Authorization, cookie, token or API key.

## TC-AR-3: Session resilience

Keep the Session open until completion and then navigate away and back.

Expected:

- Progress terminal state and final report do not duplicate.
- The complete report remains visible after Session history reload.

## Evidence

Save screenshots and relevant redacted logs under
plans/manual-results-2026-07-12-deepresearch-agent-reach/.
