在 DeskPet 项目实现 PPT Pro 计划的前端大纲确认卡（WI-4）。改/建 `G:\projects\deskpet\tauri-app\src\` 下文件，别动后端。

先读权威计划：`G:\projects\deskpet\plans\2026-06-21-ppt-deepresearch-pro\00-PLAN.md` 的 WI-4 段 + WI-3 的 WS envelope（ppt_outline_proposed / ppt_outline_decision / ppt_outline_resolved）+ §6 R-18/R-19。
范本参考（只读，照抄结构）：
- `tauri-app/src/code-panel/MessageBubble.tsx` 的 `SkillCandidateCard`（卡片渲染 + 按钮 + send decision）。
- `tauri-app/src/code-panel/ws.ts` 的 `case "skill_candidate_proposed"` → push_message。
- `tauri-app/src/stores/sessionsStore.ts` 的 skill_candidate role/字段 + resolve + `set_messages` 如何保留 awaiting 卡（reload 不丢）。
- `tauri-app/src/components/ClarificationDialog.tsx`（textarea 输入范式，可借鉴「修改」编辑态）。

要实现：
1. **类型**：在合适的类型文件（grep `ClarificationRequest` 所在的 `types/skillPlatform.ts` 或 ws 消息类型处）加 `ppt_outline_proposed`/`ppt_outline_decision`/`ppt_outline_resolved` 的 payload 类型：
   - proposed: `{outline_id, topic, outline_md, session_id, sources_count, no_research, history: {outline_id,topic,created_at,sources_count,status}[]}`
   - decision（前端→后端）: `{outline_id, action: "accept"|"modify"|"cancel"|"reuse", feedback?, reuse_id?}`
   - resolved（后端→前端广播）: `{outline_id}`
2. **ws.ts**：`case "ppt_outline_proposed"` → `store.push_message(sid, {role:"ppt_outline", ppt_outline_awaiting:true, outline_id, topic, outline_md, sources_count, no_research, history})`；`case "ppt_outline_resolved"` → 调 store 清掉该 outline_id 的 awaiting 卡（stale 清理）。
3. **sessionsStore.ts**：加 `ppt_outline` message role + 上述字段；`resolve_ppt_outline(sid, outline_id)` 清 `ppt_outline_awaiting`；**`set_messages` 合并时保留内存里仍 awaiting 的 `ppt_outline` 卡**（仿现有 skill_candidate 分支，实现「同进程 reload 不丢卡」R-19）；**按 outline_id 去重**（同 oid 只留一张，防后端广播到多面板重复，R-18）。
4. **新建 `tauri-app/src/code-panel/PPTOutlineCard.tsx`**（仿 SkillCandidateCard）：
   - 渲染 `outline_md`（用项目已有 markdown 渲染器；没有就 `<pre style={{whiteSpace:"pre-wrap",maxHeight:"40vh",overflow:"auto"}}>`）。
   - 顶部显 `📚 {sources_count} 个调研来源`；`no_research` 为真显 `⚠️ 本次未取得调研来源，基于通用知识`。**不要显示任何费用/价格字样**（用户明确要求）。
   - 按钮：`✅ 确认生成` → send `{type:"ppt_outline_decision", payload:{outline_id, action:"accept"}}`；`✏️ 修改` → **不立即发**，展开 textarea + 提示「说说想改哪里」+「提交修改」按钮 → 提交才 send `action:"modify", feedback`；`✖ 取消` → `action:"cancel"`。
   - 历史复用：`history` 非空 → 折叠区「📜 历史大纲」列出 topic + created_at，点某条 → send `action:"reuse", reuse_id:<该条 outline_id>`。
   - 发送任一 decision 后本地清 `ppt_outline_awaiting`（按钮消失/禁用）。
   - send 走该面板的 control WS（参考 SkillCandidateCard 怎么拿 ws / codePanelWS）。
5. **挂载**：在 `MessageBubble.tsx`（code panel）渲染 `role==="ppt_outline"` 用 `PPTOutlineCard`；并在主消息面板（grep `MessageStreamPanel` 或子代理面板 SubagentProgressPanel 的挂载处，对齐其双挂载教训）也渲染该卡。

约束：
- 中文 UI 文案；TS 严格，跑 `cd /g/projects/deskpet/tauri-app && npx tsc --noEmit` 必须 0 error。
- 不破坏现有 skill_candidate / clarification 逻辑。
- 自己补 vitest `src/code-panel/__tests__/PPTOutlineCard.test.tsx`：(a) 渲染 outline_md+来源数+无费用字样；(b) accept/modify(textarea提交)/cancel/reuse 各发对 envelope；(c) outline_id 去重；(d) resolved 清 stale。跑 `npx vitest run PPTOutlineCard` 到绿。

验收：tsc 0 err + vitest 绿 + 卡片四动作 envelope 正确 + 历史复用 + 去重/stale 清理。完成后简述改了哪些文件、vitest 覆盖了什么。