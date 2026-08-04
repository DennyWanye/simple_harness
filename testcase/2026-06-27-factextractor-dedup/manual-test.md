# FactExtractor 内容哈希幂等去重（Layer 1）— 手工测试文档（windows-mcp 真人点击+输入）

> **被测功能**：`FactExtractor.process_message` 在 `config.memory.v2.extract_content_dedup=True` 时，对 `source=="user_message"` 的用户聊天消息做内容哈希短路。用户在 TTL 窗口内重发归一化后相同的内容（`strip` + 折叠内部空白 + 小写）时，后台在 LLM 抽取之前直接跳过本次事实抽取，`facts` 表行数不应增长。
>
> **修复目标**：防止用户重复发送完全相同事实陈述时，LLM 因抽取漂移反复生成不同 `(category,key)`，导致 `facts` 表累积重复记录。
>
> **默认状态**：出厂点亮，`[memory.v2].extract_content_dedup = true`；TTL 默认 `3600s`，配置项为 `[memory.v2.facts].content_dedup_ttl_s`。
>
> **本文档定位**：生产上线手工验收。通过本文档 = Layer 1 内容哈希去重可进入生产；任一 ★ 用例失败 = HOLD。
>
> **最后更新**：2026-06-27

---

## 0. 测试前置（HARD — 不满足则结果作废）

### 0.1 环境（必须先满足）

1. **进程清场**：先停止旧桌宠与残留 dev 进程。不要手动启动 backend / Vite；本测试只能通过 Tauri dev 启动链路触发真实聊天。
2. **启动方式**：运行：
   `G:\projects\deskpet\plans\manual-results-2026-06-27-enable-flags\launch-dev.ps1`
   该脚本只给 Tauri 注入 env、跑源码非 frozen、日志重定向。**不要手动起 backend/vite**（坑 #7/#8/#9）。
3. **boot 必须出现两个硬锚点**：
   - `[backend_launch] Dev python=...backend`：证明跑的是源码 backend，不是 frozen 旧包。
   - `p4_fact_extractor_ready ... content_dedup=True`：证明 `extract_content_dedup` flag 真传进 `FactExtractor`。
   若任一缺失，立即停止，本轮结果作废。
4. **零崩溃硬门槛**：boot 日志不得出现 `ConfigError`、`p4_fact_extractor_init_failed`、桌宠“启动失败”弹窗；桌宠主界面必须正常，消息窗可打开。
5. **facts 表观测面**：
   - DB：`G:\projects\deskpet\backend\userdata\data\state.db`
   - 表：`facts`
   - 计数命令：
     ```powershell
     & 'G:\projects\deskpet\backend\.venv\Scripts\python.exe' -c "import sqlite3; p=r'G:\projects\deskpet\backend\userdata\data\state.db'; con=sqlite3.connect(p); print(con.execute('select count(*) from facts').fetchone()[0]); con.close()"
     ```
6. **日志观测面**：
   - Tauri dev 输出：`G:\projects\deskpet\plans\manual-results-2026-06-27-enable-flags\tauri-dev.log`
   - 该日志为 **UTF-16LE**，必须用：
     `G:\projects\deskpet\plans\manual-results-2026-06-27-enable-flags\loggrep.py`
   - 常用 grep：
     ```powershell
     & 'G:\projects\deskpet\backend\.venv\Scripts\python.exe' 'G:\projects\deskpet\plans\manual-results-2026-06-27-enable-flags\loggrep.py' facts_extract_skip_dup
     & 'G:\projects\deskpet\backend\.venv\Scripts\python.exe' 'G:\projects\deskpet\plans\manual-results-2026-06-27-enable-flags\loggrep.py' intent_triage.done
     & 'G:\projects\deskpet\backend\.venv\Scripts\python.exe' 'G:\projects\deskpet\plans\manual-results-2026-06-27-enable-flags\loggrep.py' chat/completions
     ```
7. **聊天 UI 坐标参考**（每次重启先 Screenshot 核对，scale 3.0，坐标会漂移）：
   - 桌宠“消息”按钮约 `(2715, 960)`
   - 消息窗输入框约 `(2226, 1215)`
   - “发送”按钮约 `(2520, 1215)`
   - 坐标来源：`G:\projects\deskpet\plans\manual-results-2026-06-27-enable-flags\RESULTS-TESTCASE.md`

### 0.2 windows-mcp 真测纪律（HARD CONSTRAINT — 不可妥协）

1. **真触发**：功能用例必须通过桌宠“消息”窗真实点击、剪贴板中文输入、点击发送触发。DB count 与日志是合法观测面，但不能替代真 UI 触发。
2. **每个动作前 declare**：记录 `坐标=(x,y) | 动作=click/type/restart | 期望=...`。
3. **输入方式**：`Clipboard set <中文>` → Click 输入框 → `ctrl+a` → `ctrl+v` → Click 发送。
4. **截图存盘**：建议保存到 `G:\projects\deskpet\plans\manual-results-2026-06-27-factextractor-dedup\screenshots\<case-id>-NN-*.png`。每条消息发送前后至少各一张。
5. **禁绕过**：不许用 `import` backend、WebSocket 直注、pytest、直接调用 `FactExtractor.process_message` 替代真聊天发送作为功能证据。
6. **失败处理**：失败后必须 retry ≥ 3 次不同 workaround（如重新打开消息窗、重启 Tauri、换一条全新事实句、等待 relay 恢复）才可标 `env-limited`。跳过任何 case 必须写明原因。
7. **不要在用例之间点“新话题”**：可能产生额外消息和事实抽取，污染 `facts` 计数。
8. **日志分段纪律**：每次发送前记录日志文件大小或末尾时间点；发送后只分析本次窗口内新增日志，避免把历史 `facts_extract_skip_dup` 误当本次命中。

### 0.3 ★ 一票否决项（任一 FAIL = HOLD）

| Case | 验收点 | 类别 |
|---|---|---|
| **TC-0 ★** | boot 出现 `[backend_launch] Dev python=...backend` + `p4_fact_extractor_ready ... content_dedup=True`，无 `ConfigError`，桌宠正常 | 接线/零崩溃 |
| **TC-1 ★** | 全新事实首次发送后 `C1 > C0`，证明真抽取成功；同句重发后 `C2 == C1`，且第二次出现 `facts_extract_skip_dup seen_ago=...`，且无新的 facts 抽取 LLM 出站 | 核心去重 |
| **TC-3 ★** | 不同内容不能误短路：另一条全新事实必须正常抽取，`C` 增长，且本次无 `facts_extract_skip_dup` | 误杀防护 |
| **IDEM-A ★** | 同内容重复发送在 TTL 内幂等：不新增 facts，不重复抽取，不依赖 relay 失败假绿 | 幂等核心 |
| **IDEM-D ★** | flag OFF BC：`extract_content_dedup=false` 后，同句重发走旧行为，不出现 skip；relay 健康时 facts 可继续增长 | 兼容性/逃生口 |

---

## 1. 被测行为与判定口径

### 1.1 被测行为

1. `config.memory.v2.extract_content_dedup=True` 时启用 Layer 1 内容哈希去重。
2. 只对 `source=="user_message"` 生效；summarizer 来源不短路。
3. 内容归一化规则：`strip`、折叠内部空白、小写；归一化后相同即同一内容哈希。
4. 命中重复时，在 LLM 抽取前返回空结果，本次不会写 `facts`。
5. 命中重复时 info 日志必须出现：`facts_extract_skip_dup seen_ago=...`。
6. TTL 默认 3600 秒；窗口内重复短路，超 TTL 后同内容允许重抽。
7. flag OFF 时回到旧行为：每次都尝试抽取，字节级 BC。

> **⚠️ 观测口径（重要，避免误判）**：`facts` 表行数受**两条写入路径**影响——① 用户说"请记住X"时 agent 调的 **`memory_write` 工具**（其 key 已于本批次前序改为内容哈希 + find_active 去重）；② 本功能的**后台 `FactExtractor`**（L1 内容哈希短路）。重发完全相同的话时，**两条路径都应不增行**，故 `C2==C1` 是两者合力的可观测结果。本文档 ★ 判定以 `facts` 总行数 + `facts_extract_skip_dup` 日志为准（用户可见行为），不要求区分两路；若想单看 FactExtractor 路径，认 `facts_extract_skip_dup` 日志即可。

### 1.2 判定三证

核心用例不能只看 `C2 == C1`。必须同时满足：

1. **真抽过**：第一次全新事实 `C1 > C0`。否则可能是 relay 502 或 extractor 未接线。
2. **真短路**：第二次同内容 `C2 == C1`，且本次新增日志含 `facts_extract_skip_dup seen_ago=...`。
3. **真未出站抽取**：第二次日志窗口内不得出现新的 facts 抽取 LLM 出站。若日志只能看到全局 `chat/completions`，必须结合时间顺序记录：主回复/intent triage 允许出站，但 `facts_extract_skip_dup` 之后不得再出现可归因于 facts extractor 的抽取出站；如无法区分来源，记录为“出站证据不足”，不得把该项单独判 PASS。

### 1.3 relay 502 诚实标注

relay 对 `json_schema` 偶发 502 是已知瞬态。若事实抽取失败，`facts` 表不增长不是本功能去重成功。必须用“第一次 `C1 > C0`”排除假绿：

- `C0 == C1` 且日志有 `FactExtractor.extract LLM failed` / `502 Bad Gateway`：抽取失败，标 `env-limited(relay)`，不能判去重 PASS。
- `C1 > C0` 后，第二次 `C2 == C1` + skip 日志：才可判去重 PASS。

---

## 2. 总判定

- 所有 ★ 用例 PASS，且 §6 判定表无未解释 FAIL → `DECISION: SHIP`。
- 任一 ★ 用例 FAIL → `DECISION: HOLD`，修复后从 TC-0 重新跑。
- relay 502 只允许影响“抽取真发生”的验证；不能用它掩盖接线、flag、skip 日志缺失。

---

## 3. 功能正确性 TC（windows-mcp 真人点击+输入）

> 通用步骤：每条消息发送前记录 DB count、日志分段点、截图；发送后等待桌宠回复完成，再等 5-15 秒给后台 facts fanout 完成，然后重新 count DB 并 grep 本段日志。

### TC-0 ★ 零崩溃 / 接线检查

1. **动作**：运行 `launch-dev.ps1` 启动桌宠。
   - declare：`动作=restart via launch-dev.ps1 | 期望=源码 backend + FactExtractor content_dedup=True`
2. **检查 boot 日志**：
   - 必须有 `[backend_launch] Dev python=...backend`
   - 必须有 `p4_fact_extractor_ready ... content_dedup=True`
   - 不得有 `[backend_launch] Bundled exe=...`
   - 不得有 `ConfigError`
   - 不得有 `p4_fact_extractor_init_failed`
3. **检查 UI**：Screenshot 核对桌宠正常，点击“消息”按钮能打开消息窗。
   - declare：`坐标≈(2715,960) | 动作=click 消息按钮 | 期望=消息窗打开`
4. **预期**：boot 接线完整，桌宠可聊天。
5. **FAIL 判定**：缺 `content_dedup=True` 或跑 frozen，后续所有用例作废。

### TC-1 ★ 核心：全新事实首次抽取 + 完全相同重发短路

测试句：

```text
我的工号是 B9981，在数据平台组。
```

1. **记录 baseline**：查询 `facts` 表行数，记为 `C0`。
2. **记录日志分段点**：记录 `tauri-dev.log` 当前大小或最后一条 `intent_triage.done` 时间。
3. **发送第一次全新事实**：
   - declare：`坐标≈(2226,1215) | 动作=Clipboard set + ctrl+a + ctrl+v | 期望=输入框出现“我的工号是 B9981，在数据平台组。”`
   - declare：`坐标≈(2520,1215) | 动作=click 发送 | 期望=消息发出，桌宠开始回复`
4. **等待**：桌宠回复完成后再等 5-15 秒。
5. **记录第一次结果**：查询 `facts` 行数，记为 `C1`；grep 本段日志。
6. **第一次预期**：
   - `C1 > C0`，证明 facts 抽取真发生并落库。
   - 本段不得以 `FactExtractor.extract LLM failed` / `502` 结束；若失败，按 §1.3 标 `env-limited(relay)`，不能继续判核心去重。
7. **发送完全相同文本第二次**：
   - declare：`坐标≈(2226,1215) | 动作=Clipboard set 同一句 + ctrl+a + ctrl+v | 期望=输入框内容与第一次逐字相同`
   - declare：`坐标≈(2520,1215) | 动作=click 发送 | 期望=消息发出`
8. **等待**：桌宠回复完成后再等 5-15 秒。
9. **记录第二次结果**：查询 `facts` 行数，记为 `C2`；grep 第二次日志窗口。
10. **第二次预期（三证都要满足）**：
    - `C2 == C1`
    - 第二次新增日志出现 `facts_extract_skip_dup seen_ago=...`
    - 第二次没有新的 facts 抽取 LLM 出站；允许主聊天/intent triage 相关 `chat/completions`，但不允许 skip 后继续发生 facts extractor 抽取请求。
11. **PASS 判定**：`C1 > C0 && C2 == C1 && skip 日志出现 && 无新增 facts 抽取出站`。
12. **一票否决**：
    - `C1 == C0`：第一次未证明真抽取，不能判 PASS。
    - `C2 > C1`：重复内容仍落库，核心修复失败。
    - 第二次无 `facts_extract_skip_dup`：未走 Layer 1 短路。

### TC-1b 快速连发（真机并发近似 — 补 T6 单测的 UI 面）

> 后台抽取是 `asyncio.create_task` 并发跑（每条消息一个 task）。本用例用"**不等第一条抽取完成就快速连发同句**"近似并发 TOCTOU 场景，验证占位在 LLM 抽取前就登记、并发的第二/三条也被短路（不双抽）。

1. **记 baseline**：`facts` 行数 = `R0`。
2. **快速连发**：用一句**全新**事实（如 `我的应急联系电话尾号是 3721。`），**连续发送 3 次，每次发送之间不等待桌宠回复**（尽量快：Clipboard set 同句 → Click 输入框 → ctrl+a → ctrl+v → Click 发送，立刻重复，3 次）。
   - declare：`坐标≈(2226,1215)/(2520,1215) | 动作=同句快速连发×3 不等回复 | 期望=仅第一次进入抽取，后两次被占位短路`
3. **等待**：最后一次发送后等 15-20 秒让后台 task 全部结算。
4. **记结果**：`facts` 行数 = `R1`；grep 本段日志 `facts_extract_skip_dup` 出现次数。
5. **预期**：
   - `R1 - R0` 等于"**仅一次**全新抽取的净增量"（不是 3 次累加）。
   - 本段出现 `facts_extract_skip_dup` **≥1 次**（后续连发命中占位）。
   - facts 抽取 LLM 出站对这句内容**只发生 1 次**（其余被短路）。
6. **FAIL 判定**：`R1 - R0` 接近 3 倍单次增量 / 抽取出站对同句发生多次 → 并发 TOCTOU 双抽未挡住，HOLD。
7. **标注**：真机连发速度受 UI 限制，难严格并发；若工具点击间隔过大导致第一条已抽完，本用例退化为 TC-1，标 `degraded(sequential)` 并以单测 `test_content_dedup_concurrent_same_content_calls_extract_once` + `test_content_dedup_placeholder_registered_before_llm_call` 兜底。

### TC-2 归一化：前后空白 / 内部空白 / 大小写变化仍短路

前置：TC-1 已经成功发送过 `我的工号是 B9981，在数据平台组。`。

变体句：

```text
   我的工号是   b9981，   在数据平台组。   
```

1. **记录 baseline**：记当前 `facts` 行数为 `N0`。
2. **发送归一化等价文本**：
   - declare：`坐标≈(2226,1215) | 动作=Clipboard set 归一化变体 + ctrl+a + ctrl+v | 期望=输入框出现带多余空白和小写 b9981 的句子`
   - declare：`坐标≈(2520,1215) | 动作=click 发送 | 期望=消息发出`
3. **等待**：回复完成后再等 5-15 秒。
4. **记录结果**：记 `facts` 行数为 `N1`；grep 本段日志。
5. **预期**：
   - `N1 == N0`
   - 新增日志出现 `facts_extract_skip_dup seen_ago=...`
   - 无新的 facts 抽取出站。
6. **FAIL 判定**：
   - `N1 > N0`：归一化不符合预期，重复事实仍被重抽。
   - 无 skip 日志：未命中内容哈希。

### TC-3 ★ 不同内容不误短路

测试句：

```text
我的办公座位在 12 楼 A 区 18 号。
```

1. **记录 baseline**：记当前 `facts` 行数为 `D0`。
2. **发送另一条全新事实**：
   - declare：`坐标≈(2226,1215) | 动作=Clipboard set 新事实 + ctrl+a + ctrl+v | 期望=输入框出现“我的办公座位在 12 楼 A 区 18 号。”`
   - declare：`坐标≈(2520,1215) | 动作=click 发送 | 期望=消息发出`
3. **等待**：回复完成后再等 5-15 秒。
4. **记录结果**：记 `facts` 行数为 `D1`；grep 本段日志。
5. **预期**：
   - relay 健康时 `D1 > D0`
   - 本段不得出现针对这条新事实的 `facts_extract_skip_dup`
   - 若出现 `FactExtractor.extract LLM failed` / `502`，标 `env-limited(relay)` 并 retry；不能用它判“不误短路”PASS。
6. **一票否决**：
   - 新内容出现 skip 且 `D1 == D0`：误短路，HOLD。

### TC-4 flag OFF BC：关闭去重后同句重发走旧行为

> 该用例会改本地 config。执行前备份，执行后必须还原。

1. **备份 config**：
   - 文件：`G:\projects\deskpet\backend\userdata\config.toml`
   - 复制为：`config.toml.factextractor-dedup-backup`
2. **修改 config**：在 `[memory.v2]` 下设置：
   ```toml
   extract_content_dedup = false
   ```
   若该键不存在则新增；若已存在则改为 `false`。
3. **重启**：运行 `launch-dev.ps1`。
4. **boot 预期**：
   - `p4_fact_extractor_ready ... content_dedup=False`
   - 无 `ConfigError`
5. **发送全新事实**：
   ```text
   我的临时测试编号是 OFF-BC-7744。
   ```
   记录发送前行数 `F0`，发送后行数 `F1`。relay 健康时应 `F1 > F0`。
6. **重发完全相同文本**：记录重发后行数 `F2`。
7. **预期**：
   - 第二次新增日志 **不得** 出现 `facts_extract_skip_dup`
   - relay 健康且 LLM 抽取成功时，`F2 > F1` 或至少可观察到新的抽取尝试；若 facts 合并导致行数不增，必须用日志证明确实没有走 skip 且发生了抽取出站。
8. **还原**：把 `extract_content_dedup` 改回 `true`，或恢复备份 config；重启后确认 `p4_fact_extractor_ready ... content_dedup=True`。
9. **FAIL 判定**：
   - flag OFF 后仍出现 skip：BC 逃生口失效。
   - 还原后 boot 不是 `content_dedup=True`：环境未恢复，后续用例作废。

### TC-5 TTL 超时重抽（边界 / 真机难测）

默认 TTL 为 3600 秒，真机不建议等待一小时。可用临时小 TTL 验证。

1. **备份 config**。
2. **临时配置**：
   ```toml
   [memory.v2]
   extract_content_dedup = true

   [memory.v2.facts]
   content_dedup_ttl_s = 5
   ```
3. **重启并确认**：`p4_fact_extractor_ready ... content_dedup=True`。
4. **发送全新事实**：
   ```text
   我的 TTL 测试暗号是 TTL-5599。
   ```
   记 `T0 -> T1`，要求 `T1 > T0`。
5. **5 秒内重发同句**：要求 `T2 == T1` + `facts_extract_skip_dup`。
6. **等待 6-10 秒后再次重发同句**：TTL 已过，预期不出现 skip；relay 健康时发生新抽取，`T3 > T2` 或可观察到新的抽取尝试。
7. **还原 config**：恢复 `content_dedup_ttl_s = 3600` 或删除临时覆盖，确认重启正常。
8. **标注**：若不能改 config 或不能接受重启成本，本用例可标 `env-limited(manual TTL)`，但必须保留单测覆盖引用：`backend\tests\test_factextractor_content_dedup.py::test_content_dedup_ttl_expiry_allows_same_content_again`。

### TC-6 summarizer 不短路（机制类 / 真机 env-limited）

说明：Layer 1 只对 `source=="user_message"` 生效。summarizer 由系统生成 summary 并以 `source="summarizer"` 进入 `FactExtractor`，用户很难通过普通聊天稳定直接触发两条完全相同 summarizer 内容。

1. **真机口径**：不把普通用户消息当 summarizer 测；普通 UI 只能覆盖 `user_message`。
2. **预期机制**：summarizer 来源即使内容相同，也不应被 `facts_extract_skip_dup` 短路。
3. **判定方式**：
   - 手工测试记录为 `env-limited(system-generated summarizer)`。
   - 以单测作为机制覆盖：`backend\tests\test_factextractor_content_dedup.py::test_content_dedup_does_not_skip_summarizer_source`。
4. **FAIL 判定**：如果真机日志中能明确看到 `source=summarizer` 触发 `facts_extract_skip_dup`，则为严重 bug，HOLD。

### TC-7 relay 502 防假绿

1. **触发方式**：任一事实抽取用例中，如 `facts` 不增，立即 grep：
   - `FactExtractor.extract LLM failed`
   - `502`
   - `chat/completions`
2. **预期标注**：
   - 若第一次全新事实 `C1 == C0` 且有 502：该轮为 relay 抽取失败，不是去重成功。
   - 必须 retry ≥ 3 次；若仍 502，标 `env-limited(relay)`。
3. **禁止判定**：
   - 禁止把“第一次不增、第二次也不增”判为 PASS。
   - 禁止在没有 `facts_extract_skip_dup` 的情况下声称命中去重。

---

## 4. 副作用地图（遍历 + 写副作用）

| 代码/机制 | 会产生副作用吗 | 已处理判定 | 持久化/重启影响 | 失败可重试吗 | 覆盖用例 |
|---|---|---|---|---|---|
| `p4_fact_extractor_ready` 初始化：把 `extract_content_dedup`、TTL、cache max 传入 `FactExtractor` | 不写 DB；只接线和打 boot log | boot log 必须含 `content_dedup=True` | 重启后重新构造；不持久化缓存 | 初始化失败会 `p4_fact_extractor_init_failed`，本轮作废 | TC-0 |
| `source=="user_message"` 内容哈希检查 | 写内存 LRU cache，不写 DB | hit 时 `facts_extract_skip_dup` 后直接 `return []` | cache 是进程内；重启后遗忘，重启后同内容可重抽 | 可重发；但 TTL 内会继续短路 | TC-1/TC-2/IDEM-A |
| 归一化：strip + 折叠内部空白 + 小写 | 不直接写 DB | 归一化等价内容必须同 hash | 不持久化 | 可重发验证 | TC-2/IDEM-B |
| 首次内容占位：LLM 前先登记 hash | 写内存 LRU | 防并发同内容双抽；LLM 异常/坏 JSON 会撤销占位 | 进程内 | relay 失败后应可重试，不应永久挡住 | TC-7；单测覆盖 placeholder revoke |
| `FactExtractor` LLM 抽取 | 外部 LLM 出站；成功后进入持久化 | 第一次必须 `C1 > C0` 才证明真抽过 | facts 写入 SQLite，跨重启保留 | relay 502 可 retry | TC-1/TC-3 |
| facts 持久化 | 写 `state.db` 的 `facts` 表 | 成功抽取才允许 count 增长 | 持久化 | DB 错误是产品 bug，不可吞 | TC-1/TC-3/TC-4 |
| skip 命中 | 不调用抽取 LLM，不写 facts | `C` 不增 + skip log + 无 facts 抽取出站 | 只在 TTL 和当前进程 cache 内有效 | TTL 过或重启后可重抽 | TC-1/TC-2/TC-5 |
| flag OFF | 跳过 Layer 1 cache 检查 | 不应打 skip；恢复旧行为 | config 持久化，重启后生效 | 可还原 | TC-4/IDEM-D |
| summarizer 来源 | 不走 Layer 1 短路 | 即使内容相同也不应 skip | summary facts 可持久化 | 真机难稳定触发 | TC-6 |
| TTL 过期 | 清理过期 hash，允许重抽 | 超 TTL 后不应 skip | TTL config 持久化；cache 仍进程内 | 可调小 TTL 真测 | TC-5/IDEM-C |

**结论**：本功能的核心写副作用只有两类：内存 LRU cache 与 `facts` 表写入。正确行为是“首次事实写 DB，重复事实只写/命中内存 cache，不写 DB、不抽取 LLM”。因此 §5 幂等用例必须同时看 DB、skip 日志、抽取出站三条证据。

---

## 5. IDEM 幂等用例（重点）

### IDEM-A ★ 同内容 TTL 内幂等（核心）

对应 TC-1。验收公式：

```text
C1 > C0
C2 == C1
second_segment contains facts_extract_skip_dup seen_ago=...
second_segment contains no new facts-extractor LLM outbound
```

PASS 说明：首次事实真实落库；第二次在 LLM 抽取前短路；不是 relay 失败假绿。

FAIL 说明：

- `C1 == C0`：没有证明抽取链路健康。
- `C2 > C1`：Layer 1 未挡住重复内容。
- 无 skip：没有命中去重分支。

### IDEM-B 归一化幂等

对应 TC-2。验收公式：

```text
normalize("我的工号是 B9981，在数据平台组。")
==
normalize("   我的工号是   b9981，   在数据平台组。   ")

N1 == N0
segment contains facts_extract_skip_dup
```

PASS 说明：前后空白、内部空白、小写差异不会绕过去重。

### IDEM-C TTL 边界幂等

对应 TC-5。

1. TTL 内：同内容短路，`T2 == T1` + skip。
2. TTL 后：同内容允许重抽，不应 skip；relay 健康时 `T3 > T2` 或至少可证明发生抽取尝试。

PASS 说明：去重窗口有边界，不会永久挡住用户重新写入。

### IDEM-D ★ flag OFF 字节级 BC

对应 TC-4。

验收公式：

```text
boot content_dedup=False
duplicate segment does not contain facts_extract_skip_dup
old behavior: every send attempts extraction
```

PASS 说明：配置逃生口有效；线上若去重误伤，可关闭恢复旧行为。

### IDEM-E 不同内容不共享 hash

对应 TC-3。验收公式：

```text
D1 > D0
segment does not contain facts_extract_skip_dup for this new content
```

PASS 说明：hash key 绑定归一化内容，不会把不同事实误杀。

### IDEM-F 进程重启边界（补充）

本功能 cache 为进程内 LRU，不持久化。若在 TC-1 后重启 Tauri，再发送同一句：

1. 预期不出现 skip（因为 cache 丢失）。
2. relay 健康时可能重抽，`facts` count 可能增长。

该行为是当前设计，不算 bug。若产品期望跨重启也幂等，需要 Layer 2 持久化去重，超出本文档范围。

---

## 6. 判定汇总表（执行时填写）

| Case | 类别 | 预期 | 实测 | 截图 / log / DB 证据 | PASS/FAIL |
|---|---|---|---|---|---|
| TC-0 ★ | 接线/零崩溃 | Dev python + `p4_fact_extractor_ready content_dedup=True` + 无 ConfigError | | | |
| TC-1 ★ | 核心同句去重 | `C1>C0`、`C2==C1`、skip 日志、无抽取出站 | | | |
| TC-2 | 归一化去重 | 空白/大小写变体 `N1==N0` + skip | | | |
| TC-3 ★ | 不同内容不误杀 | 新事实 `D1>D0` + 无 skip | | | |
| TC-4 | flag OFF BC | `content_dedup=False` 后无 skip，重发会抽取 | | | |
| TC-5 | TTL 边界 | TTL 内 skip；TTL 后允许重抽 | | | |
| TC-6 | summarizer 不短路 | 真机 env-limited；单测覆盖；不得观察到 summarizer skip | | | |
| TC-7 | relay 502 防假绿 | 502 时不把 count 不增判 PASS | | | |
| IDEM-A ★ | 同内容幂等 | 首次真抽，重复不增 | | | |
| IDEM-B | 归一化幂等 | 归一化等价不增 | | | |
| IDEM-C | TTL 幂等边界 | 窗口内幂等，窗口外可重抽 | | | |
| IDEM-D ★ | flag OFF BC | 关闭后不短路 | | | |
| IDEM-E ★ | 不同内容不共享 hash | 不同事实不 skip | | | |
| IDEM-F | 重启边界 | 重启后 cache 清空，可重抽 | | | |

**最终判定**：`DECISION: SHIP / HOLD`（填写）

---

## 7. 执行记录模板

```text
测试人：
日期：
启动脚本：
日志文件：
DB 文件：

TC-0:
  boot Dev python:
  p4_fact_extractor_ready content_dedup:
  ConfigError/p4 init failed:
  UI screenshot:

TC-1:
  C0:
  C1:
  C2:
  skip log line:
  chat/completions / extraction outbound notes:
  screenshots:
  verdict:

TC-2:
  N0:
  N1:
  skip log line:
  verdict:

TC-3:
  D0:
  D1:
  skip absent:
  relay status:
  verdict:

TC-4:
  config backup:
  boot content_dedup=False:
  F0/F1/F2:
  skip absent:
  config restored:
  verdict:

TC-5:
  TTL value:
  T0/T1/T2/T3:
  skip within TTL:
  skip absent after TTL:
  config restored:
  verdict:

TC-6:
  manual status:
  unit coverage reference:
  verdict:

TC-7:
  502 observed:
  retries:
  final relay status:
  verdict:
```
