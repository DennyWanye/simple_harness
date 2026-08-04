你是严苛的技术方案评审专家（只读，不要改任何文件）。这是第 4 轮对抗评审，目标：判定一份修复 plan 的可执行部分是否 **100% 可从代码层面执行（EXECUTABLE-AS-IS）**。前 3 轮已修大量问题，第 3 轮把投机性的 L2a/L2b（近重复去重）降级为「Phase 2 待设计 spike」，**本次可执行范围只剩 WI-1（Layer 1 内容哈希幂等）**。你重点审 WI-1 能不能照着 100% 落地，并确认 L2a/L2b 已干净移出本次范围。

## 要评审的 plan（绝对路径）
/g/projects/deskpet/plans/2026-06-27-factextractor-dedup/00-PLAN.md
（在 Windows 上是 G:\projects\deskpet\plans\2026-06-27-factextractor-dedup\00-PLAN.md）

## 背景
DeskPet 项目 backend（Python）。`FactExtractor`（backend/deskpet/memory/facts.py）有去重缺口：用户重发完全相同的话，后台抽取每次当新消息抽 → facts 表累积重复（真测 +3）。WI-1 的 Layer 1 = 在抽取前按"归一化内容哈希"短路：相同内容近期抽过就整次跳过抽取（直接 +0）。这是 Claude memory（内容寻址 ID）+ Hermes（精确重复拒绝）验证过的工业做法。

## 你必须去读真实代码核实 WI-1 的每条断言（用只读命令）
- backend/deskpet/memory/facts.py：
  - `process_message`（约 :1006，顶层入口；确认没有独立 extract 方法）：role 白名单(:1028)+min_chars(:1033)+LLM 抽取(:1035-1038)+`_persist_lock`(:1053 只罩 `_persist_extracted`)。确认 WI-1 把 L1 短路放在 min_chars 后、LLM 前，且抢占登记用的新 `_dedup_lock` 与 LLM 抽取（在锁外）不串行化、两锁不嵌套不死锁。
  - `FactExtractor.__init__`（约 :967）现签名：`(store, *, extract_llm, merge_llm=None, min_chars=8, cross_key_merge=False, cross_key_llm=None, embedder=None, forget_within_days=7, goal_facts=False)`。确认 WI-1 加的 3 个 kw 参数（content_dedup=False/content_ttl_s=3600/content_cache_max=256，默认 False 保 BC）不破坏现有 11 个测试调用点。
  - `_parse_extracted`（约 :1355，对"LLM 说空数组"和"坏 JSON"都返回 []）—— 确认 WI-1 的 BLOCKING-1 修法（抽出 `_try_parse_array`→None 表畸形、返回 (facts, parse_ok)，坏 JSON 撤销占位）能落地、不破坏既有调用方。
- backend/config.py：`MemoryV2Config`（约 :178）加 `extract_content_dedup: bool = True`；`MemoryV2FactsConfig`（约 :155）加 `content_dedup_ttl_s: int=3600`/`content_dedup_cache_max: int=256`。确认能被 `_load_section`/`_load_memory_v2`（约 :717-729）解析、`_MIGRATABLE_SECTIONS`（约 :813）的 ("memory","v2")+(...,"facts") 已在 allow-list。
- backend/main.py：`_FactExtractor(...)` 构造（约 :1427，附近有 `_v2_cfg = config.memory.v2`）+ `p4_fact_extractor_ready` boot-log（约 :1436-1441，现含 min_chars/cross_key_merge/goal_facts）。确认 WI-1 要求"main.py 注入 content_dedup + boot-log 追加 content_dedup="的三处接线（config True / ctor 默认 False / main.py 注入）齐全、有 boot 实证防静默漏传。
- 触发点 backend/main.py:2736-2755 `_on_message_fanout` → `asyncio.create_task(_extract_facts_bg)` → `process_message` 每条消息并发跑 —— 确认 WI-1 的并发 TOCTOU 处理（锁内"判定+抢占登记"、LLM 失败/坏 JSON 撤销占位）在这个并发模型下成立。

## 你要回答
1. **WI-1（L1）现在是不是 100% 可从代码层面执行**？逐条核对：行号/签名/控制流/flag 三处接线/并发锁/safe-fail/验收，有没有任何"plan 说但代码做不到"的（虚构方法/字段/行号、签名冲突、控制流打架、漏接线）。
2. **L2a/L2b 是否已干净移出本次范围**（不会被误执行、§10 spike 记录是否完整）。
3. **还有没有 WI-1 范围内残留的 BLOCKING/MAJOR**。
4. 明确收敛判定：**WI-1 EXECUTABLE-AS-IS（无 BLOCKING/MAJOR）** 还是 **还差 N 条**。

## 输出
1. 一句总评 + 明确判定。
2. WI-1 逐条核实表（断言 → 代码实况 → ✅/⚠️）。
3. 残留问题（若有）BLOCKING/MAJOR/MINOR + 具体修法。
只输出评审，不要改任何文件。
