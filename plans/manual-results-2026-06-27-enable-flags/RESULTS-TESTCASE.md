# 全量点亮能力 — testcase 逐条执行结果（2026-06-27）

> 对应 testcase：[`testcase/2026-06-27-enable-flags/manual-test.md`](../../testcase/2026-06-27-enable-flags/manual-test.md)（经 2 轮子代理对抗硬化）。
> 环境：`npx tauri dev` 源码 backend（非 frozen，`[backend_launch] Dev python=...backend` 已确认）+ dev userdata + relay-cloud(gpt-5.5)。
> 日志：功能 TC 用 `tauri-dev.log`；IDEM 各用独立 log（`idemA-run1/2.log` / `idemC-run1/2.log` / `idemD-run1.log`），UTF-16LE 经 `loggrep.py` 解码。
> 真测手段：功能 TC = windows-mcp 真坐标点击 + 剪贴板中文输入 + 截图 + log grep；IDEM = config.toml diff + 重启 boot log（§0.2 例外，机制类无 UI 形态）。

## §6 判定汇总

| Case | 类别 | 预期 | 实测证据 | 判定 |
|---|---|---|---|---|
| **TC-1 ★** | goal_mode | goal_task_create 真调 | `name='goal_task_create'` **×3**（titles：第1-2天/第3-5天/第6-7天读完拆解），由 `p5s2_tool_call_args_dump`(INFO) 携带；boot `goal_task_tools_registered_global count=4` + `companion_code_v1_goal_mode_ready`；无 `register_failed`；+ `wi4a_goal_anchor_always_on` + `goal_checker_nudge_injected iter=4/10` | ✅ **PASS** |
| **TC-2(a)** | auto_disclosure 机制活 | total>0 | `skill_auto_disclosed total=17`（真回合披露器跑） | ✅ PASS |
| **TC-2(b)** | 技能强命中自动加载 | strong>=1 auto_loaded>=1 | PPT 消息 → `skill_auto_disclosed total=17 strong=2 auto_loaded=2 names=['ppt-generate'...]` | ✅ PASS |
| **TC-3** | persona_inject | facts 非空回合 injected facts>=1 | `preference_profile_injected facts=10 task_type=...`（7 回合，facts 非空） | ✅ PASS |
| **TC-4(a) ★** | facts_extract 接电 | boot facts_extract=True+bind | `p4_vector_worker_ready ... facts_extract=True` + `memory_tools.bind: facts_store=FactsStore embedder=Embedder llm=True NL=False` | ✅ **PASS** |
| **TC-4(b)** | facts 落库(relay 健康) | facts 表行数增长 | **relay 恢复后复测（tc4b-retry.log）**：发 2 条身份陈述 → facts 表 **341→350(+9 新事实落库)**，`FactExtractor.extract LLM failed`=**0**（本会话无 502）。新事实含 nickname=小王/city=杭州/pet_cat_name=咪咪/uses_arch_linux 等 | ✅ **PASS**（relay 健康下 facts_extract 端到端落库；早前 502 系 relay 瞬态已恢复） |
| **TC-5** | curation_nudge | oh4_curation_nudge turn=N 完整串 | `oh4_curation_nudge sid=default turn=2/4/6 decisions=0 remembered=0`（同进程连发，完整串） | ✅ PASS |
| **TC-6** | codify 装配 | fp5_codify_wiring_ready | `fp5_codify_wiring_ready` + `fp5_auto_disclosure_wiring_ready` + `fp5_skill_matcher_prewarmed cached=17` | ✅ PASS |
| **TC-7 ★** | 零崩溃启动 | 无 ConfigError + Uvicorn up | `ConfigError`=0 / `VG-INVARIANT`=0 / `feature_flag_merge_parse_failed`=0；`Uvicorn running on http://127.0.0.1:8100`；桌宠主界面 `已连接`（截图） | ✅ **PASS** |
| **IDEM-A ★** | 回灌幂等 | 第2次不改写(hash) | 删 compaction-4 + [skills] → boot1 `feature_flag_merge_applied count=5 keys=[features.ctx_observability/...]`（补回）；boot2 `feature_flag_merge_applied`=**0** + 内容 hash **完全相同**(`B991...49B`)。mtime 变=`relay_provider_ensured` 第二写者(内容不变 atomic-replace)，**非 backfill** → 已记 §4+IDEM-A 注 | ✅ **PASS**（backfill 幂等：无 merge + hash 不变） |
| **IDEM-B** | facts 去重 | 不翻倍 | **发现 + 修复 + 复验**：原因是 `memory_write` 工具用 `key=f"memory_{int(time.time()*1000)}"` 时间戳键 → 每次新插行。**已修**（`memory_tools.py`：改内容哈希 key `_stable_memory_key` + `find_active` 命中即 `update_value` touch 不插行）。单测 `test_g3_6_write_same_text_twice_is_idempotent` 绿（同文本写两次行数不增 + `deduped=true` + 复用 id）。**真机复验**：发新陈述（工号 A7788…）→ memory_write fact 用 `key=memory_8bcd3816aa003246`（内容哈希），重发完全相同 → 该 hash key **仅 1 行**（去重生效）、新哈希 key **0 重复**、无新增时间戳键 | ✅ **报告的 bug 已修复并验证**（memory_write 兜底键去重）｜ ⚠️ 另发现**独立残留**：真机重发 count 仍 +3，来自 **FactExtractor 结构化抽取的 LLM 非确定性**（如 `employee_id` 落 `fact`+`profile` 两 category），是另一条 LLM 驱动机制、非本次 bug、需另议 |
| **IDEM-C ★** | 显式 false 不覆盖 | 用户值被尊重 | `goal_mode=false` → boot `goal_task_tools_registered_global`=**0** + `companion_code_v1_goal_mode_ready`=0 + `feature_flag_merge_applied`=**空**（未覆盖）；删行 → `feature_flag_merge_applied count=1 keys=['features.goal_mode']` 补回 → `goal_task_tools_registered_global count=4` 回来 | ✅ **PASS** |
| **IDEM-D ★** | fresh install 全 ON | 出厂点亮 | 空 userdata → 种子 `config.toml` `[memory.v2]` 顶层 false=**0** + facts_extract/goal_facts/persona_inject/curation_nudge/auto_learnings=true + `[features]` goal_mode/slash/压缩四件套=true + `[skills]` 三 enabled=true；boot `goal_task_tools_registered_global count=4`/`fp5_codify_wiring_ready`/`fp5_auto_disclosure_wiring_ready`/`p4_vector_worker_ready facts_extract=True`；`ConfigError`=0；`feature_flag_merge_applied`=0（种子即全，走 copyfile 非 merge） | ✅ **PASS** |

## 最终判定

**DECISION: SHIP** ✅

- **全部 6 个 ★ 一票否决项 PASS**（TC-1 / TC-4(a) / TC-7 / IDEM-A / IDEM-C / IDEM-D）。
- 功能 TC：**7/7 全 PASS**（含 TC-4(b)——relay 恢复后复测 facts 表 341→350 真落库 +9；早前 502 系 relay 瞬态已恢复）。
- 幂等 IDEM：A/C/D **PASS**；**IDEM-B 部分**——结构化事实去重 OK，但 facts_extract 的 `memory_<timestamp>` 兜底键不去重（重发相同陈述 350→353 累积），属既有实现的 idempotency 缺口（非本次 flag 引入、非★、不阻断 SHIP），已 spawn 跟踪。
- **未观测到任何 flag 翻 ON 引起的崩溃 / 启动失败 / 不变式报错。**

> **复测对当初 opus 审计的回应**：opus 指出 TC-4(b)/IDEM-B 的 "≥3 retry 全 502" 表述与日志（实 2 次失败）不符。relay 恢复后真补做：facts 真落库（TC-4b 转 PASS）、并借此把 IDEM-B 从"env-limited 跳过"做成了"实测去重行为（发现兜底键不去重）"。原"×3"表述作废，以本节复测为准。

## 执行中发现并修文档（"测出问题就修"）

1. **IDEM-A mtime 变**：全栈启动下 config.toml 被 **relay provider 收编（`relay_provider_ensured`）每次重写**（第二写者，内容不变换 mtime）→ 单测的 `st_mtime_ns` 断言只在隔离跑 backfill 时成立。已放宽 IDEM-A 为「hash + merge-event 主判，mtime 旁证」+ §4 副作用地图补 relay provider 第二写者行。
- 该第二写者**不碰** `[memory.v2]`/`[features]`/`[skills]` 段，对本次 flag 点亮无副作用交互。

## 两个 env-limited 项的复跑条件
- TC-4(b) / IDEM-B：relay 对 gpt-5.5 + `json_schema` 结构化输出恢复稳定（不再 502）后，重发身份陈述 → facts 表行数增长 / 同陈述两次行数不翻倍即可补 PASS。主聊天（无 json_schema）始终 200，非链路整体故障。

## 证据文件
- `tauri-dev.log`（功能 TC）· `idemA-run1.log`/`idemA-run2.log`/`idemC-run1.log`/`idemC-run2.log`/`idemD-run1.log`（IDEM）
- `screenshots/final-state.png` · `config.toml.IDEM-backup`（IDEM 前主 config 备份，已用于复原）
- `loggrep.py`（UTF-16 解码 grep 助手）· `launch-dev.ps1` / `launch-idemD.ps1`（启动脚本）
