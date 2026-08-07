# TC-WB-04 — ChatView 消息真实往返 + companion 特权动作（主窗）

> 对应 AC：WB-4 ｜ 行为契约：B4、**B5（companion 特权在 main 窗可用——专门步骤 4–5）**、B9（mic 占位）
> manual_required: true
> 前置：后端就绪、provider 可用（记录 HEAD、启动日志、健康检查）。禁止 WS 直注/脚本回放代替 UI 输入。

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 在聊天视图输入栏用真实键入/剪贴板粘贴：`用 markdown 回复：一个二级标题、一个三项无序列表、一段行内代码。` 并点击发送。 | 消息出现在消息流；后端**真实往返**（非本地回显）：收到助手回复。 |
| 2 | 检查回复渲染。 | markdown 正常渲染：二级标题为标题样式、无序列表为列表样式、行内代码为代码样式（非裸 `#`/`-`/反引号字面文本）。 |
| 3 | 观察输入栏旁的麦克风按钮；悬停查看 tooltip。 | 麦克风按钮存在且为**禁用态**（不可点击/点击无反应）；tooltip 说明语音待接入（如"语音输入待中转站 Realtime 接入"语义等价文案）。 |
| 4 | **B5 专门步骤·第 (a) 层（裁决第 7 条，凭据链单测层）**：`cd backend && uv run pytest tests/companion/ -q` | 与**基线等值**：`9 failed, 643 passed`，且失败集合与改版前基线 `644ab16` **逐条一致**（见下方注）——companion 凭据链白名单/迁移断言层通过。 |

> **步骤 4 判据修正（2026-08-08，r7 实证）**：本步骤原写「pytest **全绿**（0 failed）」，
> 与 TC-WB-12 / S12 把「9 failed, 643 passed」当基线等值验收的判据直接矛盾。
> 用 `git worktree add --detach <tmp> 644ab16` 检出改版前基线、同一解释器跑同一条命令，
> 实测基线为 **9 failed, 640 passed, 10 skipped**，失败用例 ID 与当前**逐条完全一致**
> （`test_candidate_draft_receipts` 的 v19→v20 升级 1 个、`test_performance` 的
> durability lane 参数化 7 个、`test_skill_pack_adapter` 的清单哈希 1 个）；
> passed 640→643 是改版新增的 3 个测试全部通过。
> ⇒ 这 9 个是**与本改版无关的既有基线失败**，「全绿」标准在基线上同样达不到，
> 属措辞错误，故收敛为「与基线等值」。此 9 个失败另行作为既有缺陷跟踪，不在本改版验收范围。
| 5 | **B5 专门步骤·第 (b) 层（真机连接 scope 证据；取证配方=裁决第 9 条）**：① 以 `dev.sh 2>&1 | tee /tmp/wb04-dev.log` 方式启动（dev.sh 终端 stdout 已合流后端 uvicorn 日志），等应用启动完成、进入聊天视图；② 执行 `grep -aE "companion_action" /tmp/wb04-dev.log` 检索连接建立行；③ **canonical 触发**：真机打开 devtools 控制台（withGlobalTauri 已开启），执行一次 `await window.__TAURI__.core.invoke("get_window_control_credential")`，记录返回值。 | ② 的 grep **有命中**，把实际命中行**原样入账**并断言：含 `scope=companion_action`（字段样式以实测行为准，入账即判定）且捕获文件中**零** `window_scope_denied`（`grep -c "window_scope_denied" /tmp/wb04-dev.log` 为 0）、零 scope 降级记录；③ 的 invoke 返回**非 error 对象**（凭据签发成功）——消除 vacuous pass。零命中或任一 denied/降级即 FAIL。 |
| 5b | **加分证据（非必需，裁决第 7 条）**：若会话中自然出现 companion 确认卡片，点击确认并观察结果。 | 卡片在主窗内出现且确认后动作端到端成功、无 scope denied——记为加分证据；未出现卡片**不判 FAIL**。 |
| 6 | 检查聊天头部条。 | 当前会话标题、模型按钮、ContextRing、Harness 巡检开关在 ChatView 头部条可达（B4 能力不减）。 |

判定：步骤 1–3 + 步骤 4（(a) 层）+ 步骤 5（(b) 层）全部满足才 PASS；(a)(b) 两层缺一不可（只有单测绿、真机连接 scope 未验，恰是主要矛盾警告的静默降级盲区）；步骤 5b 仅加分。
