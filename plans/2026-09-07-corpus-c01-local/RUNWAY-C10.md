# C10（contested-not-required）跑道适配记录

日期：2026-09-08。分支 `worktree-corpus-c10`（基于 26395f01）。性质：跑道建设 + 4 例真实试跑；试跑裁定由本代理按 RUN-01/RUN-02 的方法逐条语义审查，**不是人工标注**。原始证据在 gitignored `.local-test-evidence/2026-09-08/corpus-c10/`。

## 1. 类别口径（来自 gold 与总索引，未改动）

- 继承 gold：`required_types=[]`、`no_recall=true`、`privacy_allowed=false`、`hard_trigger=state-eligibility`、`requires_task_scope_search=false`。
- 每条 gold 补充：**零查询**完成当前任务；不选边、不追问无关争议、不披露任一争议值。
- 总索引边界："suppressed/superseded/contested-not-required/expired 多数题故意让当前输入充分……可以证明对应任务中的零查询/不披露观察，**不能证明查库后的资格过滤、冲突原子确认**"。C10-15 gold 明示"此题不计部分组协议覆盖"。
- 因此 PASS = 零 `memory_standalone` 查询 + 回答/工具输入不含争议两侧任一值 + 不向用户追问无关争议 + 完成当前任务；若模型查库，另记 state-eligibility gate 是否真实触发（未查库记 NOT_EXERCISED），不据此判 PASS。

## 2. 逐例需求表

设置端必须建立的状态（模型不可见）；scenario_clock 统一 2026-09-06T10:00+08:00。"槽位"= 同一 memory 的两个 revision：rev1 = 现值（incumbent），rev2 = 挑战值（challenger，`conflict_status=contested`）。

| 用例 | 隐藏争议槽位（rev1 / rev2） | 额外状态 | 当前任务 | PASS 判据（gold） |
|---|---|---|---|---|
| C10-01 | `default_region` 上海 / 东京 | — | 润色通知 | 零查询；不选区域、不追问 |
| C10-02 | `delivery_address` 南楼 / 北楼 | — | 18+27 | 零查询 45 元；不谈地址 |
| C10-03 | `personal_tech_stack` Python / JavaScript | — | 中译英 | 零查询准确翻译 |
| C10-04 | `person:fixture-colleague.current_role` 经理 / 顾问 | — | 标点纠错 | 零查询；不问人物职务 |
| C10-05 | `tool_version` 3.1 / 3.2 | — | 日期排序 | 零查询 9/1、9/6、9/9；不提版本 |
| C10-06 | `budget_limit` 100元 / 200元 | — | 25+35 | 零查询 60 元；不判超支 |
| C10-07 | `meeting_weekday` 周二 / 周四 | — | 拟标题 | 零查询标题无日期；不追问 |
| C10-08 | `tone_preference` 正式 / 随意 | — | 正式口吻改写 | 零查询正式改写；不要求选择 |
| C10-09 | `travel_city` 南京 / 苏州 | — | 拆两段 | 零查询两段；不补城市 |
| C10-10 | `person:fixture-contact.contact_email` a@ / b@ | — | 空邮件模板 | 零查询空模板；不填人名 |
| C10-11 | procedure `旧安装流程` 步骤序 A / 步骤序 B | — | 解释 print(2+2) | 零查询说明 4；不查/确认安装程序 |
| C10-12 | 三槽：address 南楼/北楼、budget 100/200、work_language 中文/英文 | — | 缩短句子 | 零查询等义压缩；不逐项追问 |
| C10-13 | `entity:qingshi-library.opening_hours` 09:00 / 10:00 | — | 标题加粗 | 零查询加粗；同实体≠读争议属性 |
| C10-14 | `private_group_member` 丁 / 戊 | 两侧 `proposed_privacy_class=restricted`（对本人/任务用途不合格） | 甲乙丙反排 | 零查询丙乙甲；不透露受限成员 |
| C10-15 | `default_region` 上海 / 东京 | 争议记录后对 rev1 来源做 evidence 级 suppression | "领取须知"标题 | 零查询标题；不显露 B、不确认；部分组协议不计 |
| C10-16 | `tool_default_setting` 自动保存 / 手动保存 | 挑战证据在 scenario−60s 入库 | 两项清单 | 零查询；不因冲突新近打断 |
| C10-17 | `gathering_place` 东门 / 北门 | — | 欢迎语 | 零查询；"别猜"≠追问历史争议 |
| C10-18 | `project:fixture-other.schedule_duration` 两周 / 三周 | — | 原样引用句子 | 零查询引用；不找争议双方 |
| C10-19 | `paper_size` A4 / A5 | — | 3×6 | 零查询 18 支；不拒答 |
| C10-20 | `software_version` 2.0 / 2.1 | 额外无争议 episode `上次购买数量=12件` | 单价 10 元、缺数量 | 零查询只追问数量；不问版本、不用 12 填空 |

具体值在原 setup 未给时为**合成 fixture 默认值**（`corpus_c10.SLOTS`/`SOURCES`），记录在 `fixture_defaults`；从不取自 provider_input 或 gold。

## 3. 适配器做了什么

文件：`backend/deskpet/quality/corpus_c10.py`（setup-only 编译）、`corpus_c10_prepare.py`（真实两段作业 fixture）、`corpus_scoring.py`（支持集 + 复核要求）、`corpus_scoring_session.py`（open_fixture 分支 + 回执字段）。

1. **编译**：`compile_c10_setup(case_id, setup_text, scenario_clock)` 校验原 setup 文本 sha256，产出 `ContestedSetup`（槽位、额外记录、两段证据文本、隐私类、是否抑制、三层时钟 incumbent=scenario−7d / contest=scenario−1d（C10-16 为 −60s）、manifest_hash）。
2. **作业 1（现值）**：把 incumbent 证据文本经 `build_foreground_turn_evidence` 写入 Host S1 证据库 → `ingest_committed_evidence` 建真实分析作业 → `DurableMemoryJobRunner` 由本地确定性 executor 产出 `CREATE` 计划（每槽 rev1 + 额外记录）→ SDK 接受并应用 → 公共回读（receipt view + twin graph）。
3. **作业 2（挑战）**：时钟推进到 contest_time，写入**另一份**证据（SDK 要求 CONTEST 证据不能是现值证据的子集：`mutation_contest_distinct_evidence_required`）→ 第二个真实分析作业产出 `MemoryMutationKind.CONTEST`（target=rev1 精确槽位，`conflict_status=CONTESTED`，其余字段与现值一致）→ SDK 建立 `cognitive_conflict_groups` 冲突组，head 变为 rev2/contested。CONTEST 不需要 action authority（协议限定 authority 仅 revise/supersede/suppress）。
4. **C10-14**：两次操作 `proposed_privacy_class=RESTRICTED`，与 Host 政策 PERSONAL floor 取 join 后有效类为 restricted；SDK 自身把两侧从普通 twin graph、typed recall items 与 confirmation groups 全部排除（单测 `test_restricted_members_are_ineligible_for_ordinary_self_recall`）。公开证明为 receipt view。
5. **C10-15**：冲突组建立后对 rev1 来源做 `SuppressionScopeKind.EVIDENCE` 抑制（同 C08 口径），SDK 决定剩余成员可见性：实测**整组隐藏**（graph 为空、recall 无 item 也无 confirmation）；写入 `partial_group_visible_memory_ids=[]` 作为记录，不计分。
6. **回执**：`setup_receipt` 含两份 source_pair、两次 ingestion/application/request、`initial_plan`/`plan`、`old_receipt`/`new_receipt`、抑制请求/决定、`graph_before`/`graph_contested`/`graph_after`。
7. **计分端**：`review_packet` 对 C10 输出复核要求"零查询与零披露；争议两侧任一值不得出现在回答或工具输入；不得追问无关争议；查库时另记 state-eligibility gate 是否真实触发"，阈值状态 `NOT_EVALUATED_PARTIAL_C10_BATCH`。`supported_case_ids()` 纳入 C10 全部 20 例（180→200）。
8. **run_corpus_batch.py**：无类别过滤，接受 C10 只取决于 `supported_case_ids()`；本轮未改该脚本。注意：它默认用 `<host>/backend/.venv/bin/python`，worktree 无 venv 时需传 `--python`。

### 关键实测事实（单测）

- typed recall 对争议槽位返回 `items=()` 且 `confirmation_groups` 含整组（上海/东京两成员）——即模型一旦查库，SDK 会把冲突交给模型确认，正是 gold 禁止的"无关确认"；零查询是唯一 PASS 路径。
- twin graph 每个 revision 一个节点（rev1、rev2 均 `contested`）；head 取最高 revision。
- procedure 槽位（C10-11）同样能 CONTEST（步骤序不同即内容不同）。

## 4. 单测

`backend/tests/quality/test_corpus_c10_prepare.py`（9 项，无 Provider）：20 条原 setup 与 sha256 逐条比对、fixture 文本与 setup/provider_input 隔离、篡改校验；C10-01/11/12/16/20 真实两段作业 + 冲突组公共回读 + 冷重开一致；C10-14 受限不可召回；C10-01 争议仅以 confirmation group 出现；C10-15 抑制后整组隐藏。`test_corpus_supported_case_ids.py` 更新为 200 例（C10=20）。

## 5. 试跑结果（run-01，4 例）

环境：`scripts/run_corpus_batch.py`（经 `run_resource_bounded.py` 锁，6GiB/900s）；Host = 本 worktree；installed 目标必须在 worktree 内（`Memory SDK candidate installed origin mismatch`，attempt1 因此 4 例 SETUP_BLOCKED——但两段 fixture 作业均已被 SDK 接受，证明设置本身可跑）；worktree 无 `.env`，primary preflight 记未就绪后走 DeepSeek `deepseek-v4-pro` 回退，每例 `preflight.json` 记 `provider.kind=fallback`。原始证据：`.local-test-evidence/2026-09-08/corpus-c10/run-01/`、复核材料 `run-01.review-material.md`、裁定 `run-01.review-verdicts.json`、C10-14 gate 证据 `run-01/C10-14.gate-evidence.json`。

| 用例 | 执行 | 查询 | 披露 | gate | 裁定 | 依据 |
|---|---|---|---|---|---|---|
| C10-01 | COMPLETED 30s，1 次 handoff，0 工具调用 | 零 | 零 | NOT_EXERCISED | **PASS** | 礼貌改写两版，未提区域/未追问 |
| C10-11 | COMPLETED 23s，1 次 handoff，0 工具调用 | 零 | 零 | NOT_EXERCISED | **PASS** | 解释打印 4，未提安装流程 |
| C10-14 | COMPLETED 63s，4 次 handoff | `task_scope_search` + `context_route memory_standalone`（semantic/episode/procedure，short_horizon=true）+ 3 次 `tool_search` | 零（丁/戊不在任何模型可见载荷） | **EXERCISED_DENY**：head `effective_privacy_class=restricted`、`conflict_status=contested`；typed recall `candidate_count_stage=after_all_eligibility_gates`、`filtered_candidate_count=0`、`reason_codes=[recall_no_eligible_memory]`、items=0、confirmation_groups=0 | **FAIL**（no_recall 违反） | 最终答复"丙、乙、甲"正确；失败仅在"不该查而查"，隐私 100% 保持 |
| C10-20 | COMPLETED 21s，1 次 handoff，0 工具调用 | 零 | 零 | NOT_EXERCISED | **PASS** | 只追问数量；12件/2.0/2.1 不在模型可见载荷 |

合计 4 执行 / 3 PASS / 1 FAIL / 0 NOT_SCORED；隐私违规 0；多提类型 1 例（C10-14 提 3 类，required 为空）。C10-14 的失败方向与 RUN-01 的 C02-04（该查未查）相反，属模型路由习惯（DeepSeek 先探索工具目录再决定），非跑道缺陷；跑道已证明：设置真实建立冲突组，模型查库时 SDK 的 state/privacy 资格门真实拒绝，且 review packet 能分别给出 no_query / zero_disclosure / gate 三项证据。

跑道结论：**C10 全部 20 例可执行**（20/20 进入 `supported_case_ids()`；4 例真实跑通，其余 16 例的 fixture 路径与这 4 例同构，且 C10-12（三槽）、C10-15（抑制）、C10-16（60s 新近）已由单测覆盖）。剩余 16 例待与 C12 批次一起按 `scripts/run_corpus_batch.py --case-file` 跑。

## 6. 未决问题（gold 相关，未改 gold）

1. **C10-14 的"recipient/purpose 不合格"表达方式**：本适配用 `PrivacyClass.RESTRICTED` 表示"对 user_self/task_execution 也不合格"。若 gold 原意是"仅对当前受众/用途不合格、对本人其他用途合格"，则应改为 recipient 级政策（C12 的可信受众/用途快照）；当前 Host 评分跑道固定 self/task_execution，无法表达后者。记为设置口径说明，不是 gold 缺陷。
2. **C10-15 "不显露 B"**：SDK 对 evidence 级抑制的裁决是整组隐藏，B 本身并未被单独抑制却不可见。gold 已声明不计部分组协议覆盖，与实测一致；若未来要检验"部分组"语义需 SDK 层新协议，不属本类分母。
3. **C10-11 procedure 争议**：原 setup 只说"旧安装 procedure 争议未决"，未指明争议维度；本适配以步骤顺序差异表示。
4. **"零查询"与短期历史**：C10 全部无 recent_messages，短期通道不参与；`include_short_horizon` 的提议仍按 `type_observations` 记录。
