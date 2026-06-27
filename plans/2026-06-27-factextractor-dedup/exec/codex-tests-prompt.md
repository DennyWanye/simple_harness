你是资深 Python 测试工程师。任务：为 FactExtractor 的「内容哈希幂等去重(Layer 1)」写单测。**只新建 `backend/tests/test_factextractor_content_dedup.py` 这一个文件**，不改别的。

## 仓库（绝对路径）
- 仓库根：`G:\projects\deskpet`（git，Windows）；新建文件：`G:\projects\deskpet\backend\tests\test_factextractor_content_dedup.py`；venv：`G:\projects\deskpet\backend\.venv\Scripts\python.exe`

## 被测对象（别的 codex 已实现，你照接口写测试）
`backend/deskpet/memory/facts.py` 的 `FactExtractor`：
- ctor：`FactExtractor(store, *, extract_llm, merge_llm=None, min_chars=8, cross_key_merge=False, cross_key_llm=None, embedder=None, forget_within_days=7, goal_facts=False, content_dedup=False, content_ttl_s=3600, content_cache_max=256)`。
- `extract_llm` 是 `async def (prompt: str) -> str`，返回 LLM 原始文本（应是 `[{...}]` JSON 数组）。
- `async def process_message(self, *, message_id: int, content: str, role: str, source: str = "user_message") -> list[dict]`。
- 行为：当 `content_dedup=True` 且 `source=="user_message"` 时，**相同归一化内容**近期(content_ttl_s 内)抽过 → **整次跳过**（不调 extract_llm，return []，并打 `log.info("facts_extract_skip_dup ...")`)。LLM 异常 或 坏 JSON(非合法数组) → 撤销占位、下次同内容可重抽。
- 内部用 `self._extract_llm`（mock 它来数调用次数）、`self._recent_content`（OrderedDict[hash,float]）、`self._dedup_lock`。

## setup 参考（看现有测试怎么构造真 FactsStore + mock LLM）
先读 `backend/tests/` 下现有 facts 测试（如 `test_facts_extractor.py` / `test_memory_*`）确认：FactsStore 怎么用 tmp_path 建、process_message 怎么调、ExtractedFact 的 JSON 格式。**复用同款 fixture 风格**（pytest_asyncio + tmp db）。extract_llm 用 `unittest.mock.AsyncMock` 或自定义 async 函数计数。一个最简合法返回值：`'[{"category":"profile","subject":"user","key":"name","value":"小王","confidence":0.9,"evidence":"x"}]'`。

## 要写的用例（async，pytest.mark.asyncio）
- **T1（核心）**：`content_dedup=True`。对**同一 content** 调 `process_message` 两次（role="user", source="user_message"）→ 断言 extract_llm **总调用 1 次**（第二次被内容哈希短路）；第二次返回 `[]`。
- **T2**：两条**不同 content** → extract_llm 调 2 次（不误短路）。
- **T3**：`content_ttl_s=1`，发同 content → 等 >1s（或 monkeypatch `time.time`/直接改 `extractor._recent_content` 里的时间戳为过期）→ 再发同 content → 重新抽（extract_llm 第二次被调）。
- **T4**：`content_dedup=False` → 同 content 两次 → extract_llm 调 2 次（flag OFF 不短路）。
- **T5**：`content_cache_max=2` → 发 3 条不同 content → `extractor._recent_content` 长度 ≤2（LRU 淘汰最早）。
- **T6（并发）**：`content_dedup=True`，mock extract_llm 内 `await asyncio.sleep(0.01)` 制造交错；`asyncio.gather(process_message(同content), process_message(同content))` → 断言 extract_llm **总调用 1 次**（锁内抢占登记防 TOCTOU 双抽）。**再加负向 sanity**：把 extractor 的 `_dedup_lock` 临时替换成 `contextlib.nullcontext()`（不加锁）跑同样 gather → 断言会复现 **2 次**调用（证明 ==1 是锁的功劳，不是协程顺序巧合）。
- **T7（LLM 异常撤销）**：mock extract_llm 第一次 `raise RuntimeError`，第二次正常返回 → 同 content 发两次 → 断言两次都调了 extract_llm（第一次异常撤销占位 → 第二次重抽，不被误短路）。
- **T7b（坏 JSON 撤销）**：mock extract_llm 第一次返回**畸形串**（如 `"not a json array"`），第二次返回合法数组 → 同 content 发两次 → 断言两次都调 extract_llm（坏 JSON parse_ok=False → 撤销占位 → 第二次重抽）。
- **T9（summarizer 不短路）**：`content_dedup=True`，用 `source="summarizer"`、`role="system"` 发**同一** content 两次 → extract_llm 调 **2 次**（summarizer 不进内容短路）。注意 summarizer 路径 role 白名单要 `role="system" and source="summarizer"`。

## 硬约束
- **只新建那一个测试文件**。不改 facts.py/config.py/main.py。
- 测试不连真 LLM（mock extract_llm）、不连网络。
- 每个断言要真能证伪 bug（别写永真断言）。

## 自测（必须跑，绿了才算完成）
```
cd G:\projects\deskpet\backend
.venv\Scripts\python.exe -m pytest tests/test_factextractor_content_dedup.py -q -p no:cacheprovider
```
若因被测实现细节（如 log 事件名、字段）与你假设不符导致挂，**以 facts.py 真实实现为准调整测试**（去读 facts.py 的 process_message 实际逻辑），不要改 facts.py。

## 完成后输出
- 写了哪些用例 + pytest 真实输出（pass 数）。不要谎报，跑了再说。若某用例因被测方实现未就绪而挂，如实说明哪条、为何。
