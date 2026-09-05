# S6 Task1/2 实施验收清单

下表是完整目标验收清单，不能整表标PASS。P1与无scope admission的已执行子集见IMPLEMENTATION，其它项仍NOT_RUN；不更改V0 sealed required集合。

## 1. 文件、正例、反例与判定

| ID / 落点 | 正例 | 必需负例/故障 | 原契约映射 |
|---|---|---|---|
| P-AUTH `backend/tests/memory/test_primary_control_binding.py` + 既有credential回归 | 真实签名测试key、main window、现profile_bind后primary.open；同连接重复读无需重签，重连同primary | 未bind/另一socket仅ready/旧lease/rechallenge/unbind拒绝；原签名scope/body/replay和public authority字段拒绝保留 | HM-AC1/7/8；HM-X01、HM07 |
| P-INIT `test_primary_conversation_api.py` | 隔离fresh roots并发attach只产生一个writable primary；close/reopen两次ID与receipt一致 | init事务故障重试无半态/第二primary；legacy primary CRUD仍拒绝且hash不变；provider未配置可读primary/设置，发送明确不可用不清draft | HM-AC1/8；HM-X01 |
| P-SEND 同文件 | 无scope文本真实SQLite入队、ACK durable；同delivery key丢ACK/重开重发返回同turn；附件与选择冻结 | 改text/attachment/resume intent的同key冲突；非法附件、错receipt不落半状态；离线不默认new_session，不丢draft | HM-AC1/3/8；HM-X01、HM09/10 |
| P-RUNTIME `backend/tests/execution/test_primary_foreground_runtime.py` | deterministic Provider stub+真实runtime/SQLite，无scope direct/memory standalone可完成；task分支继续exact绑定；最近10完整turn groups跨重开可恢复 | no-recall零Memory query/额外continuation；普通聊天不建fake scope；未裁决project tool不能执行；不能读legacy历史或给空context冒充；跨task不串authority | HM-AC3/4/6；HM01/02/10 |
| P-QUEUE 同文件 | 长Run中多个普通消息FIFO、始终一个foreground；stop/pause及时且精确作用目标 | 控制A延迟到B启动后stale拒绝、B不变；重连不重放stop到新Run；enqueue/worker重放不重发Provider/tool；PAUSED不误标完成 | HM-AC3/8；HM02/X01 |
| P-PROJECT `backend/tests/memory/test_primary_projection.py` | primary→turn→host/sdk Run→execution session真实绑定，多页+同timestamp+重开消息无漏/重；durable ACK/通知丢失可补齐 | 他人ref/伪绑定/旧cursor/oversize绕过失败；terminal先于投影显示pending；不要str(ContentBlock)；补投影不再执行工具 | HM-AC1/7/8；HM-X01 |
| P-PRIV 同文件 | 普通过滤后message/Tool/Artifact DTO可用，精确detail分页 | suppressed旧cache/cursor/详情/订阅/rebuild不复活；credential/hidden-reasoning canary不出现；sealed refs不能借普通API读取 | HM-AC1/7；HM07/X01 |
| P-TASK `test_primary_conversation_api.py` 与 `TaskScopePanel.test.tsx` | active/recent分页；相似A/B search→exact B→六视图source/hash→resume B，新queued intent最终route B | 点击候选不切active/不授root；stale hash、他人scope、错open receipt、队列等待后root漂移拒绝effect；不偷偷选A；UI fake live_probe不能证明fresh | HM-AC3/4/7；HM02/09/10 |
| P-BIND 同文件 | Manual native picker→propose→明确confirm只追加一次；Auto只读provenance | 取消、错challenge、过期/replay、公共父目录、替换root、identity drift无追加/effect；UI不能自开Auto | HM-AC3；HM09 |
| P-UI `primaryProtocol.test.ts`、`primaryConversationStore.test.ts`、`WorkbenchShell.test.tsx`、`ChatView.test.tsx`、`InputBar.chat.test.tsx` | cold bootstrap/reload稳定primary；一个聊天入口；queued/stream/terminal/重连渲染；draft按ACK清除 | production import/route不达SessionList；HUMAN不发chat_v2/new_session/session CRUD/hydration；旧session_switched不能切主对话；bootstrap失败不fallback | HM-AC1/8；V0 Session替代集合 |
| P-KEEP 上述UI文件及现Skill/permission/voice测试 | Settings、model、usage、Skill安装/slash、附件、单一授权弹窗、Tool轨迹、Artifact、voice启动/挂断、Memory/Trace/反馈入口保留 | 两个permission owner、lost-ACK安装重复、Tool目录缩水、voice创建可见Session、旧project continuation按钮回流均失败 | TC-GS03～10及原受影响功能 |

新增runtime测试允许deterministic Provider stub，仅称自动化/runtime证据；不可mock checkpoint/route/binding恢复校验来获得PASS。测试全部独立tmpDB，不接18110、main运行中userdata或现Provider。

## 2. 实施时命令

在该worktree准备好依赖与新增文件后执行，当前没有执行这些命令：

```sh
# cwd=/Users/denny/projects/simple_harness-s6-primary-preparation
backend/.venv/bin/python -m pytest backend/tests/memory/test_primary_control_binding.py backend/tests/memory/test_primary_conversation_api.py backend/tests/memory/test_primary_projection.py backend/tests/execution/test_primary_foreground_runtime.py -q -p no:cacheprovider

# cwd=<worktree>/tauri-app
npm run typecheck
npm run test -- src/chat/primaryProtocol.test.ts src/stores/primaryConversationStore.test.ts src/components/TaskScopePanel.test.tsx src/components/WorkbenchShell.test.tsx src/views/ChatView.test.tsx src/code-panel/InputBar.chat.test.tsx
# scope/verifier变更同时执行现Rust credential/canonical相应测试；由源码test名确定精确filter，不能只测TS。
```

新worktree不含ignored `.venv`/node_modules；实施时用锁定依赖建立本树环境或显式指定已核验解释器，记录package版本/源码来源；不能因命令找不到依赖而报PASS。对具体失败做窄重测；全量/三仓consumer/真人门依原S6计划在独立执行阶段记录，不在本准备阶段自动开始。

原始证据仅放本worktree `.local-test-evidence/<实际日期>/s6-primary/<run>/`；日志包含exact Host/SDK/Memory refs、命令、退出码、总数、各失败、hung位置及超时。不能提交raw DB/log/receipt。历史四红不归本计划修复，后续全量须逐项核对原因，不能用固定数量豁免新失败。

## 3. V0 Session lineage逐项保留

以下从base的`testcase/index.md`读取，原文件保留；“superseded”指既有V0 lineage，不是本次新排除。

| 旧TC | 既有替代TC | S6检查 |
|---|---|---|
| TC-GS-01 | TC-HM-09 | 自动workspace迁入TaskScope binding，不再每聊天建Session |
| TC-GS-02 | TC-HM-09 | selected directory/取消/非法根仍按binding语义验证 |
| TC-PS-01 | TC-HM-09 | root identity/去重/显式授权保留，移除project Session注册导航 |
| TC-PS-02 | TC-HM-09 | task binding持久/幂等保留，唯一primary重启不变 |
| TC-PS-03 | TC-HM-10 | 普通聊天可用，项目effect在route前拒绝；继续任务不新建Session |
| TC-PS-04 | TC-HM-09 | 多根语义归TaskScope，不恢复旧Session绑定操作 |
| TC-PS-05 | TC-HM-02 | TaskScope inspect/resume与分页承接阅读能力，移除grouped Session导航和同项目新建 |
| TC-PS-06 | TC-HM-09 | project grouping不能充当execution root；TaskScope exact roots仍独立核验 |
| TC-PS-07 | TC-HM-09 | missing root可见且effect拒绝；不恢复旧Session relocate捷径或替换已绑定root |
| TC-PS-08 | TC-HM-X01 | 不执行旧物理删除oracle；验证immutable evidence与fresh契约 |

仍active的8条不得删除/跳过或改成只看新UI：TC-GS-03全局Skill幂等、04 standalone/TaskScope Run共享全局snapshot、05 cold restart目录持久、06失败原子性、07完整Tool catalog及policy、08并发/lostACK、09 Auto默认与强制gates、10 Skill故障Run收敛/恢复。以各TC正文为最终oracle，不以本摘要替代。

V0 closure历史记录145条总inventory，13 HM+8 retained GS=21 required、15 scenarios；另10条superseded、109 legacy needs-review。该数字是原V0历史分类，不是本轮测试收集结果。执行前重新核对sealed source hash/lineage；不重seal、不临时增加/删减required、不放宽旧hash。

## 4. 真实UI与program门仍欠账

Task1/2自动化之后，以当前worktree exact Tauri build+isolated userdata真实点击：cold start/reload同primary、无Session管理、相似A/B候选不能授权、exact resume、Manual/Auto及drift、保留功能。由Tauri自己spawn本树backend/Vite，记录真实路径。原S6两个独立Provider roots、至少20 committed turns/两个TaskScopes/exact resume及HM-S1～S12/冻结quality集合、full-surface、三仓clean候选和machine finalize都保持原要求，不以stub代替。

本轮交付状态：P1及P2 admission源码/回归可审阅；runtime闭环待共享terminal owner协调，UI/真实Provider及整个S6 gate仍NOT_RUN。S5c Dirac另线不由本计划宣布完成。
