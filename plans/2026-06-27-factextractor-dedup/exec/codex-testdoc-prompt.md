你是资深测试工程师。任务：为 DeskPet 刚实现的「FactExtractor 内容哈希幂等去重(Layer 1)」生成一份**详细的、可一步步执行的手工测试文档（windows-mcp 真人点击+输入）**。把文档**写到** `G:\projects\deskpet\testcase\2026-06-27-factextractor-dedup\manual-test.md`（新建目录+文件）。

## 被测功能（已实现，default ON）
`config.memory.v2.extract_content_dedup=True`（出厂点亮）。行为：用户在桌宠聊天发一条消息，后台 `FactExtractor.process_message`(facts.py) 会 LLM 抽取事实写进 facts 表。**修复点**：用户**重发完全相同的话**（归一化=strip+折叠内部空白+小写后相同）→ 在 LLM 抽取**之前**按内容哈希短路 → **整次跳过抽取** → facts 表 **count 不增**（修复前每次重抽会因 LLM 漂移累积重复记录）。
关键事实：
- 仅 `source=="user_message"`（用户聊天）走短路；summarizer 来源不短路。
- TTL 默认 3600s（窗口内重发相同内容才短路）；超 TTL 重发会重抽。
- flag OFF（`extract_content_dedup=false`）= 旧行为（每次都抽，字节级 BC）。
- 命中短路时后台 log 打 **`facts_extract_skip_dup seen_ago=...`（info 级）**。
- 抽取真发生才会让 facts 表增长（relay 健康时）；relay 对 json_schema 偶发 502 时抽取失败（已知瞬态，非本功能 bug）。

## 真机环境与观测面（务必写进文档 §0）
- 启动：`G:\projects\deskpet\plans\manual-results-2026-06-27-enable-flags\launch-dev.ps1`（只给 Tauri 注 env、跑源码非 frozen、日志重定向）。**不要手动起 backend/vite**（坑 #7/#8/#9）。boot 必须出现 `[backend_launch] Dev python=...backend`（跑源码）+ **`p4_fact_extractor_ready ... content_dedup=True`**（证 flag 接对），否则白测。
- facts 表：`G:\projects\deskpet\backend\userdata\data\state.db` 的 `facts` 表，`select count(*) from facts` 看行数。
- 日志：tauri dev 输出重定向的 `tauri-dev.log`（**UTF-16LE**，用 `plans/manual-results-2026-06-27-enable-flags/loggrep.py` 解码 grep `facts_extract_skip_dup` / `intent_triage.done` / `chat/completions`）。
- 聊天 UI：桌宠「消息」窗（剪贴板中文输入：Clipboard set→Click 输入框→ctrl+a→ctrl+v→点发送）。参考坐标在 `plans/manual-results-2026-06-27-enable-flags/RESULTS-TESTCASE.md`（消息按钮≈(2715,960) / 输入框≈(2226,1215) / 发送≈(2520,1215)，scale 3.0，每次重启先 Screenshot 核对）。

## 文档要求（对标项目已有 gold-standard 格式）
先读一份现有同格式文档学结构：`G:\projects\deskpet\testcase\2026-06-27-enable-flags\manual-test.md`（§0 前置 HARD / §0.2 windows-mcp 真测纪律 / §0.3 ★一票否决 / §3 功能 TC 一步步+预期 / §4 副作用地图 / §5 IDEM 幂等 / §6 判定表）。**生成的文档要同款结构**，且因为本功能本身就是幂等修复，§4/§5 是重点。

### 必须覆盖的用例（你要写成一步步可执行 + 每条明确预期 + log/DB 判定 + ★标注一票否决）
1. **★ 核心**：发一条**全新**事实陈述（如"我的工号是 B9981，在数据平台组"）→ 等抽取 → 记 C1（应 > C0，证真抽过）。**重发完全相同** → 等 → C2。**断言 C1>C0 且 C2==C1 且第二次出现 `facts_extract_skip_dup` info 日志 且第二次无新的 LLM 抽取出站**（三证防假绿——只看 C2==C1 可能是 relay 挂了的假绿）。
2. 归一化：重发**带前后多余空格/改大小写**的同一句 → 仍被短路（C 不增 + skip 日志）。
3. 不同内容不误短路：发另一条**不同**事实 → 正常抽取（C 增 + 无 skip 日志）。
4. **★ 零崩溃/接线**：boot 出现 `p4_fact_extractor_ready content_dedup=True` + 无 ConfigError + 桌宠正常。
5. （可选/边界，标注真机难测）TTL 超时重抽：默认 3600s 真机难等；写明"可临时把 `[memory.v2.facts].content_dedup_ttl_s` 设小(如 5)重启验证超时后重抽"，或标 env/手段。
6. flag OFF BC：临时 config `extract_content_dedup=false` 重启 → 重发相同句**会重抽**（C 增，无 skip 日志）→ 测后还原。
7. summarizer 不短路（说明：summary 由系统生成、用户难直接触发 → 标注"机制类，靠单测覆盖 / 真机 env-limited"）。
8. relay 502 时抽取失败的诚实标注（C 不增是因为没抽成功，不是去重；要靠"第一次 C1>C0 真抽过"区分）。

### 真测纪律（写进 §0.2，HARD）
真坐标点击+剪贴板真输入+截图+log/DB 判定；每动作前 declare 坐标/动作/期望；截图存盘；失败 retry≥3 不同 workaround 才标 env-limited；不许用 import/WS 直注/pytest 替代真 UI 作为功能证据（DB count + skip 日志是本功能的合法观测面，但功能触发必须真聊天发送）。

## 输出
- 把文档写到 `G:\projects\deskpet\testcase\2026-06-27-factextractor-dedup\manual-test.md`。
- 然后**输出**：文档大纲（章节 + 用例清单）+ 你认为还有哪些边界没覆盖（供 Lead 评估迭代）。
