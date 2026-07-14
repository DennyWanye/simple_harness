# PPT Pro Session Approval - Manual E2E

Date: 2026-07-10

## Scope

Verify the repaired user path in the real DeskPet desktop app:

1. Start `ppt_pro` from a normal message session.
2. Receive the generated outline card in that same session.
3. Click `确认生成` in the session card.
4. Observe immediate acknowledgement and durable workflow resume.

## Environment

- Source backend: `F:\projects\deskpet\backend`
- Backend/Vite: `8100` / `5173`
- Session: `6af52d3e-ff14-47f6-9d7f-e80728ae79ad`
- Durable run: `f25296978c56472fbddc8d5cdfbe578a`
- Runtime log: `tauri-relay11.err.log`
- UI driver: Windows Computer Use with real window clicks

## Result

PASS.

- The current session displayed `PPT 大纲确认` with `确认生成`, `修改`, and `取消` controls.
- Clicking the actual `确认生成` control changed the card to `已提交决定`.
- The same session immediately displayed: `大纲已确认，正在生成 PPT。完成后文件会发送到当前会话。`
- The backend log recorded `ppt_outline_decision_resolved` for outline id `workflow:f25296978c56472fbddc8d5cdfbe578a:0`.

Two initial coordinate attempts did not activate a control: one was rejected as outside the window bounds, and one landed on blank space. The final action targeted the accessibility button itself. No unintended control was activated.

## Automated Regression

- Backend focused tests: `28 passed`
- Frontend focused tests: `10 passed`
- TypeScript `--noEmit`: passed

