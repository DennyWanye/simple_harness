你是严苛的代码验收审计员（只读，不要改任何文件）。任务：核实一份已实现的改动是否 **100% 完成了 plan WI-1 的所有要求**。逐条对照 plan 与真实代码，揪出"漏做/做错/与 plan 不符"。这是把关，宁严勿松。

## 材料（绝对路径，Windows，git 仓库 G:\projects\deskpet）
- **plan（要求来源）**：`G:\projects\deskpet\plans\2026-06-27-factextractor-dedup\00-PLAN.md`（重点读 §2 目标、§3 Layer 1、§4 WI-1、§5 验收、§6 R5、§7）。**只验 WI-1（L1 内容哈希幂等）；WI-1.5/WI-2(L2a/L2b) 是 Phase 2、本次不做，不在验收范围**。
- **已改代码**：
  - `G:\projects\deskpet\backend\config.py`（加了 `extract_content_dedup`/`content_dedup_ttl_s`/`content_dedup_cache_max`）
  - `G:\projects\deskpet\backend\deskpet\memory\facts.py`（`_content_hash`/`_try_parse_facts`/`_parse_extracted` 薄壳/ctor 3 参/`_dedup_lock`/`_recent_content`/`_revoke_placeholder`/`clear_content_cache`/`process_message` L1 短路）
  - `G:\projects\deskpet\backend\main.py`（`_FactExtractor(...)` 构造传 3 参 + `p4_fact_extractor_ready` boot-log 加 `content_dedup=`）
  - `G:\projects\deskpet\backend\tests\test_factextractor_content_dedup.py`（T1-T9 + T8）

## 你要逐条核实的 WI-1 验收点（去读真实代码确认每条真做了、做对了）
1. **config**：`MemoryV2Config.extract_content_dedup: bool = True`；`MemoryV2FactsConfig.content_dedup_ttl_s: int=3600`/`content_dedup_cache_max: int=256`。`_load_section` 能解析（dataclass 字段）。
2. **facts.py 模块级**：`_content_hash(source, text)` 做了 strip+折叠空白+小写+含 source 的 sha1（同内容稳定）；`_try_parse_facts(raw)->(facts, parse_ok)` 区分"合法数组(含空)=parse_ok True" vs "畸形/JSONDecodeError/非list=False"；`_parse_extracted` 薄壳复用、对外行为不变。
3. **facts.py ctor**：加 `content_dedup=False/content_ttl_s=3600/content_cache_max=256`（默认 False 保 BC）+ 初始化 `_content_dedup/_content_ttl_s/_content_cache_max/_recent_content(OrderedDict)/_dedup_lock(asyncio.Lock)`。
4. **facts.py process_message**：L1 短路在 `min_chars` 后、LLM 前；**仅 `source=="user_message"`**；`async with self._dedup_lock` 内做"清过期+命中判定+抢占登记"（原子，防并发 TOCTOU）；命中打 `log.info("facts_extract_skip_dup ...")` 并 return []；**LLM 异常 → `_revoke_placeholder` 撤销占位**；用 `_try_parse_facts`，**parse_ok=False → 撤销占位**（坏 JSON 不吞事实）；parse_ok 但 0 条 → 保留占位 return []；**成功持久化后把占位时间戳刷新为完成时刻**。
5. **facts.py R5**：`clear_content_cache()` 公有 async 方法存在（锁内 clear）。
6. **main.py**：构造传 `content_dedup/content_ttl_s/content_cache_max`（取自 `_v2_cfg`）；boot-log `p4_fact_extractor_ready` 加 `content_dedup=`。
7. **测试**：T1 去重(llm 1 次)/T2 不同内容不短路/T3 TTL 过期重抽/T4 flag OFF 不短路/T5 LRU 淘汰/T6 并发只抽1次(+负向 sanity)/T7 LLM 异常撤销重抽/T7b 坏 JSON 撤销重抽/T9 summarizer 不短路/T8 clear_content_cache 自愈。逐条看测试**真断言了对的东西**（不是永真断言/假绿）。
8. **BC**：`content_dedup` 默认 False → 现有调用方不传 = 字节级不变。跑一下确认（只读跑测试可以）：
   ```
   cd G:\projects\deskpet\backend
   .venv\Scripts\python.exe -m pytest tests/test_factextractor_content_dedup.py tests/test_memory_tools_e2e.py tests/test_memory_v2_config.py -q -p no:cacheprovider
   ```

## 输出
1. **明确判定：WI-1 完成度 = 100% / 不到 100%（列出缺什么）**。
2. 逐条核实表：验收点 → 代码实况(file:line) → ✅完成/⚠️漏或错。
3. 若有漏/错，给具体补法（我来补）。
4. 重点抓：有没有 plan 要求但代码没做的；有没有测试假绿；并发/safe-fail/BC 是否真落地。
只输出审计结论，不改文件。
