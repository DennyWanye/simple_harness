# 剩余非 C10/C12 语料的跑道补齐记录（RUNWAY-REST，2026-09-08）

分支 `worktree-corpus-rest`（基于 main 26395f01）。范围：240 条 gold 清单（Memory SDK `review-zh/successor-12x20/逐类20条件.md`）与 `.local-test-evidence/2026-09-07/corpus-batch/run-01*/、run-02*/` 已执行用例 id 求差，排除 C10/C12（由 `corpus-c10`/`corpus-c12` 并行处理）。

## 一、差集结果

- gold 240 条，证据目录已执行 182 条（含 C05-12 SETUP_BLOCKED）。
- 从未执行的非 C10/C12 用例 18 条：C02-19、C03-20、C05-01/02/03/05/06/13/15/16/17/18/19、C08-20、C09-13、C11-12/16/19。
- 已执行但因跑道原因 NOT_SCORED 的 1 条：C05-12（run-01e `c05_actual_owner_context_required`）。
- 本记录覆盖以上 19 条。C05-10/20 的 NOT_SCORED 属模型行为（未等确认即 resume），不在跑道范围。

判定原则（用户授权子代理裁决）：只用 setup 已写明的事实做适配；缺失字段沿用既有"合成 fixture 元数据"先例（`合成任务`、C11-14 marker-only、C06 "原文未提供…" 文案），在 setup 文本里显式标注；不改 gold，不从 followup/gold 反推 seed。

## 二、逐例表

| id | gold 要求（跑道需提供的 setup 事实） | 适配改动 | 试跑结果 | 阻塞 |
|---|---|---|---|---|
| C05-01 | 同名"资料归档"两条，按月份(2025-11/06)与介质(纸质扫描/照片)消歧 | `corpus_c05.composed_goal`：goal 缺失时由 setup 的 date_text/project/alias 合成为 goal 文本（`2025-11；纸质扫描` / `2025-06；照片`），经真实 `context_route create_new` 写入，进入 FTS 索引与 scope_disclosure；setup 文本追加合成元数据标注；`_SCRIPTS` 加入 f1 | 见"三、试跑" | 无 |
| C05-02 | A(2024春季)与B(2026)按年份消歧；"最近目录不含A" | goal 合成 `2024春季`/`2026`；评分历史经 `C05PhaseHistoryReader` 冻结隔离 setup 轮，最近上下文本就不含 A/B（条件成立） | 未试跑（同路径） | 无 |
| C05-03 | 旧名"展览准备"可检索，现名"秋季小展" | 产品无标题改名操作（`_MUTATION_KINDS` 无 title.*），别名以 goal `曾用名：展览准备` 承载并可 FTS 命中 | 未试跑 | 无（记录：别名以 goal 字段承载，非产品级别名） |
| C05-05 | 读书节2024/2025 按年份选 | 标题已含年份，无需合成；加入脚本与 enqueue 白名单 | 未试跑 | 无 |
| C05-06 | 同项目"网站整理"两个 scope，按工作范围选 | goal 合成 `网站整理`（A/B 同值），用户按标题(内容校对/图片压缩)区分；两 scope 各自独立 task root | 见"三、试跑" | 无 |
| C05-15 | 标题"相册校对"、公开日期 2026年8月；无来源备注被隐藏 | goal 合成 `2026年8月`。"缺 provenance 的备注" 无法在不伪造 DB 行的前提下植入，未植入（模型若编造备注即违反 gold "不补隐去备注"，仍可计分） | 未试跑 | 部分：`unproven_note_hidden` 未实现（需伪造无来源字段，拒绝） |
| C05-19 | 跨 workspace 只恢复 B 的 exact 范围 | goal 已有(处理笔记/处理扫描)，标题合成。产品每个 task 绑定 configured root 下独立子目录 `task-<id>`（`context_route._create_new`），"workspace一/二" 以两个独立 task root 实现，公共父目录不授权 | 未试跑 | 无（记录：非两个 configured workspace） |
| C08-20 | 已忘工具别名"蓝盒"对应另一实体 | `FACTS['C08-20']=('semantic','tool_alias','蓝盒')` 标量抑制 + `UNPREPARED_CARRIERS` 记 `entity_alias_association`（另一实体未命名，不造 relation 行；C08-04 先例） | 见"三、试跑" | 无（载体部分） |
| C09-13 | 旧程序"附纸质副本"步骤已取消并 superseded | C09 增加 `KINDS['C09-13']='procedure'`、`c09_payload`：原 revision 为 Procedure（name=旧提交程序（原文未提供其余步骤）, steps=('附纸质副本',), applicability=('旧程序（原文未提供适用条件）',)），successor 为 SUPERSEDE→`ProcedureLifecycleState.SUPERSEDED`；`FixtureSetupExecutor` 支持 procedure CREATE | 见"三、试跑" | 无 |
| C11-12 | 过期免邮仍在旧摘要中 | `SPECS['C11-12']` 标量 `shipping_offer=免邮`(past) + `UNPREPARED_CARRIERS` 记 `retained_summary_same_time_lineage`（旧摘要为派生载体，C08 派生阶段未与 C11 时序夹具组合） | 未试跑 | 部分：摘要载体未产出 |
| C11-16 | 领取旧门票提醒已 expired | `KINDS['C11-16']={'A':'prospective'}`；`ExpiredFixtureExecutor.payload_for_spec` 生成 `ProspectiveMemoryPayload(领取旧门票, trigger=valid_until)`，pending + valid_until=now-1；单测证实过期后 twin graph 为空、typed_recall(prospective) 为空 | 未试跑 | 无 |
| C11-19 | 旧费率/旧数量上限/旧运费优惠均 8月底到期 | 三条 marker-only 标量（值即 marker，C11-14 先例），到期 = 9月1日 00:00 上海（"月底"取次日零点规则）；defaults 记 `known_marker_only_no_invented_value` | 未试跑 | 无 |
| C05-12 | B 属另一主体，permission-first 过滤后只列本人 | 未改。跑道 `corpus_c05_runtime.prepare_task_case(lanes['other'])` 已预留第二主体通道，但 Host 只有一个认证主体 | 不可试跑 | **产品决策**：`context_route.local_owner_auth`（"The single authenticated local owner"）与 `execution/foreground_runtime.py`（"One-subject foreground driver"，`foreground_runtime_subject_mismatch`）规定单主体；SDK 总索引"默认主体为同一测试用户U"、HM-AC-4"初版同一用户…"。需决定是否引入第二认证主体通道 |
| C05-13 | scope 披露显示"清点"但隐藏已 suppressed 的家庭地址 | 未改 | 不可试跑 | **产品决策**：`task_scope/disclosure.py` 的字段披露只来自 create_new 参数与 mutation ops，无字段级 suppression 通道；地址亦无 authored 值。需决定 TaskScope 字段级遗忘 |
| C05-16 | 首轮只披露标题/月份，f1 后补充搜索才披露主题 | 未改 | 不可试跑 | **产品决策**：`task_scope_search` 候选与 open 共用 `_scope_disclosure_reader`（context_route.py:722），无分层披露；f2 事件 `new_candidate_preview_after_f1` 亦无对应产品事实 |
| C05-17 | A/B 绑定命名 root 展板-甲/乙，父目录未授权 | 未改 | 不可试跑 | **产品决策**：`context_route._create_new` "Normal creation gets its own stable direct child… model text never supplies a path"，root 名固定 `task-<id>`；两任务无标题，除 root 名外无任何可披露的区分字段 |
| C05-18 | 候选预览后、f1 前追加当前 revision | 未改。`PreparedTaskCase.before_selection`、`C05PhaseHistoryReader.add_completed_phase` 已就绪 | 未试跑 | 跑道未完成（非产品决策）：需在两轮真实 Provider 评分之间重新接纳 setup fixture Provider（`admit_scoring_provider` 之后链首为真实 Provider），执行 revision 轮并把该 run 加入 setup 集合与历史隔离；`validate_schedule` 需接受 `fixture_action=append_predefined_current_revision` |
| C02-19 | B 为系统未确认推断 | 未改。`corpus_inference.inference_source`、`corpus_inference_prepare.open_prepared_inference_fixture` 已就绪 | 未试跑 | 跑道未完成（非产品决策）：推断来源必须是独立来源库（`import_setup_conversation_sources` 要求 source_path≠scoring_path）里一次真实 ASSISTANT 消息 Run；评分会话单库单 main 运行时无法同进程产生，需新增"来源运行子进程"阶段 |
| C03-20 | D 为无用户证据的模型推测 | 同上（`corpus_c03_inference.prepare_c03_20_setup`） | 未试跑 | 同 C02-19 |

## 三、试跑

证据：`.local-test-evidence/2026-09-08/corpus-rest/run-01/`（经 `scripts/run_corpus_batch.py` → `run_resource_bounded.py` 锁串行；host-root 为本工作树，installed target 为工作树内 `installed-h0710-m0628-s0313`，因 `verify_memory_candidate` 要求 direct_url 指向本树 vendor wheel）。同一时段 `corpus-c10` 批次持有锁，本批在其释放后启动。

| id | provider | 跑道结果 | 模型结果 | 说明 |
|---|---|---|---|---|
| C05-06 | primary gpt-5.6-luna | 跑道 OK：setup 两 scope CONFIRMED，真实披露 title=内容校对任务/图片压缩任务、goal=网站整理（合成 goal 已进入 scope_disclosure），setup 零真实模型调用 | `FOLLOWUP_UNMET`（f1） | 首轮模型只调 `context_route memory_standalone`（`memory_types` 含 episode/semantic），未调 `task_scope_search`，无候选预览事件，按 `record_unmet_and_stop_no_rescue` 停止。属模型行为（同 run-01e C05-10），非跑道缺口 |
| C08-20 | primary gpt-5.6-luna | 跑道 OK：标量 A（semantic `tool_alias=蓝盒`）真实 job APPLIED 后公开 suppress，评分前 twin graph 为空 | `PENDING_POST_TERMINAL_REVIEW`，主代理复核 **PASS** | 1 次 handoff、0 次工具调用（零查询），回答逐字为"青灯阅读器"，未套"蓝盒"映射；符合 gold（hard_trigger=suppression、no_recall） |
| C05-01 | primary gpt-5.6-luna | 跑道 OK：两 scope CONFIRMED；真实 `task_scope_search` 回执中 A/B 同名"资料归档"，goal 分别为 `2025-11；纸质扫描` / `2025-06；照片`，resume 为核备份/地点标签；f1 前置事件 SATISFIED（候选可见 2、首轮无正式 scope 授权） | `PENDING_POST_TERMINAL_REVIEW`，主代理复核 **PASS** | 首轮只列两候选并请用户选择、无 resume；f1 后 `context_route resume_existing` 恰为 A（真实 ID），回答说明下一步"核备份"。合成 goal 起到了 gold 要求的月份/介质消歧作用 |
| C09-13 | primary gpt-5.6-luna | 跑道 OK：Procedure old-0（rev1）→ successor-0（rev2，SUPERSEDED）两张真实 receipt，评分前 twin graph 为空 | `PENDING_POST_TERMINAL_REVIEW`，主代理复核 **PASS** | 1 次 handoff、0 次工具调用（零查询），回答"本次仅提交电子稿，无需附送纸质副本"，未因旧流程增加纸质动作 |

小结：4 例真实试跑，跑道全部走通（setup CONFIRMED、真实 Provider、事件/审批按契约执行）；语义复核 3 PASS（C05-01、C08-20、C09-13）、1 例模型行为 FOLLOWUP_UNMET（C05-06）。C09-13 的锁冲突（另一代理批次持锁，`run_resource_bounded` 非阻塞返回 75）产生的空 stub 目录已清除后重跑，`batch-summary.jsonl` 中保留该条 rc 75 记录。首次试跑 4 例的表格 id/adapter 行见"二"。未试跑的 8 例（C05-02/03/05/15/19、C11-12/16/19）与试跑例走同一适配路径，其 setup 已由无 Provider 单测（C11 prepare 20/20 含真实 job 与过期后召回为空断言）与离线 `prepare_batch` 编译（`prepare-check-rest/`）证实。

## 四、支持面变化

`corpus_scoring.supported_case_ids()` 180 → 192：C05 8→15、C08 19→20、C09 19→20、C11 17→20。仍排除：C02-19、C03-20、C05-12/13/16/17/18、C06-01、C10、C12。

## 五、单测（无 Provider）

- `tests/quality/test_corpus_supported_case_ids.py`、`test_corpus_c08_prepare.py`、`test_corpus_c09_prepare.py`、`test_corpus_c11_prepare.py`、`test_corpus_c05_prepare.py` 更新并通过（详见提交信息）。
- 注意：13 个 quality 测试文件硬编码 `/Users/denny/projects/...` 原始 md 路径，在本机（taiwan）本就失败；本次用脚本按本机路径核对了 08/09/11/05 的 setup 字节与 followup 脚本一致性，并通过 `prepare_batch` 离线编译 12 例（`prepare-check-rest/`）。
