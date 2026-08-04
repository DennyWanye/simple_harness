你是严苛的代码验收审计员（只读，不改文件）。第 2 次复评：上一轮你判 WI-1 不到 100%、提了 4 点。现在已处置，请复核是否真到 **100%**。

## 材料（绝对路径，Windows，git 仓库 G:\projects\deskpet）
- plan：`G:\projects\deskpet\plans\2026-06-27-factextractor-dedup\00-PLAN.md`（只验 WI-1 / L1，L2 是 Phase 2 不算）
- 代码：`backend/config.py`、`backend/deskpet/memory/facts.py`、`backend/main.py`、`backend/tests/test_factextractor_content_dedup.py`

## 上轮 4 点 + 本轮处置（请逐条复核代码确认）
1. **`_try_parse_facts` 顶层非 list**（你说 `{"facts":[]}` 会被当合法空数组）：**裁决=保留现有 prose 容错**。理由：现有 `_parse_extracted` 一直是"从可能含前后文字的响应里救出 `[...]`"（prose-tolerant，生产行为）。把"内含数组的 dict"判成 parse_ok=False 会让**可救的响应被误撤销重试**（更不 robust、浪费 LLM 调用）。真畸形（无括号/JSONDecodeError）仍正确 → parse_ok=False → 撤销。已加测试 `test_try_parse_facts_parse_ok_semantics` 钉住语义：真畸形/空串/无括号→False；`[]`→(空,True)；合法数组→True；prose 包裹→救出 True。**请确认这个裁决合理 + 测试钉对**（dedup 安全性：真坏响应 False→撤销重试；可救响应 True→保留，二者都正确）。
2. **`_persist_extracted` 抛异常未撤销占位**（safe-fail 缺口）：**已修**。facts.py `process_message` 持久化段现包 `try/except`，异常时 `await self._revoke_placeholder(h)` 后 `raise`（保留 "DB error → re-raise" 契约）。请确认。
3. **T6 缺负向 sanity**：**已用更正确的证明替代 + 说明**。本实现的"判定+登记"临界区**全是同步 dict 操作、无 await**，asyncio 协作调度下该块本就原子（去锁也是 ==1，故 plan 原设想的"去锁→==2"负向 sanity **无法复现**）。真正的并发保护属性是"**占位登记发生在 LLM 调用之前**"——已加 `test_content_dedup_placeholder_registered_before_llm_call` 直接断言 LLM 被调用时占位已在 cache（+T6 正向 `==1` 仍在）。请确认这个证明对并发安全的覆盖**等价或更强**于原"去锁→==2"。
4. **T5 没证淘汰 oldest**：**已加强**。T5 现在填满 cache_max=2 后，重发**第 1 条（最早、应已淘汰）→ 断言 llm.await_count 从 3→4（重抽，证 oldest 被淘汰）**；再重发第 3 条（最近、仍在缓存）→ 断言不增（被短路）。请确认。

## 你要做
- 去读真实代码复核上述 4 点处置。
- 跑测试确认全绿（只读环境若 pytest 被策略拦，就静态核对测试断言逻辑）：
  ```
  cd G:\projects\deskpet\backend
  .venv\Scripts\python.exe -m pytest tests/test_factextractor_content_dedup.py -q -p no:cacheprovider
  ```
- 全量复核 WI-1 §4 所有验收点是否齐（config/facts/main/tests）。

## 输出
1. **明确判定：WI-1 完成度 = 100% / 仍不到（列出还缺什么 + 是否同意 #1/#3 的裁决）**。
2. 4 点处置逐条 ✅接受 / ⚠️仍有问题。
3. 若仍不到 100%，给具体补法。
只输出审计结论，不改文件。
