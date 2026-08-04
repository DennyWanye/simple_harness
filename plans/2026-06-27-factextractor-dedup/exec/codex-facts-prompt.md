你是资深 Python 工程师。任务：在 DeskPet 项目里给 `FactExtractor` 实现「消息级内容哈希幂等去重」(Layer 1)。**只改 `backend/deskpet/memory/facts.py` 这一个文件**，不要碰别的文件。

## 仓库与文件（绝对路径）
- 仓库根：`G:\projects\deskpet`（git 仓库，Windows）
- 目标文件：`G:\projects\deskpet\backend\deskpet\memory\facts.py`
- venv python（自测用）：`G:\projects\deskpet\backend\.venv\Scripts\python.exe`

## 背景
`FactExtractor.process_message`（facts.py 约 :1006）是后台抽取入口：用户每发一条消息，main.py 用 `asyncio.create_task` **并发**调它 → LLM 抽取事实 → 持久化。Bug：用户**重发完全相同的话**，每次都重新 LLM 抽取，而 LLM 对同内容两次抽出的 (key/value/category) 会漂移 → 累积重复记录。修法 = 在 LLM 抽取**之前**按「归一化内容哈希」短路：相同内容近期抽过就整次跳过（直接不抽 = 不增重复）。这是 Claude memory（内容寻址 ID）+ Hermes（精确重复拒绝）验证过的工业做法。

## 现状代码（process_message 关键段，facts.py:1033-1054，照此修改）
```python
        if not content or len(content.strip()) < self._min_chars:
            return []
        # FP-4 WI-3.1: select prompt based on goal_facts flag
        _prompt_tmpl = _EXTRACT_PROMPT_WITH_GOALS if self._goal_facts else _EXTRACT_PROMPT
        try:
            raw = await self._extract_llm(_prompt_tmpl.format(content=content[:2000]))
        except Exception as exc:  # noqa: BLE001
            log.warning("FactExtractor.extract LLM failed: %s", exc)
            return []
        extracted = _parse_extracted(raw)
        if not extracted:
            return []
        # Stage 2 D13 v2：summarizer 来源 → category override
        if source == "summarizer":
            for f in extracted:
                f.category = "episodic_summary"
        async with self._persist_lock:
            return await self._persist_extracted(extracted, message_id)
```
`process_message` 签名是 `async def process_message(self, *, message_id: int, content: str, role: str, source: str = "user_message")`。

## 现状 ctor（facts.py:967-1004，在末尾追加 3 个 kw 参数 + 初始化字段）
现签名：`def __init__(self, store, *, extract_llm, merge_llm=None, min_chars=8, cross_key_merge=False, cross_key_llm=None, embedder=None, forget_within_days=7, goal_facts=False)`。

## 现状 `_parse_extracted`（facts.py:1355-1391）—— 需重构出 parse_ok
现函数对「空 raw / 无 `[...]` / JSONDecodeError / 非 list」都返回 `[]`，无法区分"LLM 说空数组(成功)" vs "坏 JSON(失败)"。

## 要实现的改动（全部在 facts.py 内）

### 1. 顶部 import
确保有 `import hashlib` 和 `from collections import OrderedDict`（没有就加；`asyncio`/`time`/`json` 已有）。

### 2. 模块级新增函数
```python
def _content_hash(source: str, text: str) -> str:
    """内容寻址哈希：归一化(strip+折叠内部空白+小写)后含 source 做 sha1。
    同一内容恒得同一 hash → 重发完全相同内容可被短路。"""
    norm = " ".join(text.strip().split()).lower()
    return hashlib.sha1((source + "\x00" + norm).encode("utf-8")).hexdigest()


def _try_parse_facts(raw: str) -> tuple[list["ExtractedFact"], bool]:
    """返回 (facts, parse_ok)。parse_ok=False 表示 raw 畸形/无数组/JSONDecodeError/非 list
    （应撤销占位、允许重抽）；parse_ok=True 表示成功解析出合法 JSON 数组（即便为空=LLM 说无可抽）。"""
    # —— 把现 _parse_extracted 的解析逻辑搬来，但区分失败与空数组 ——
    if not raw:
        return [], False
    text = raw.strip()
    if text.startswith("```"):
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    text = _strip_reasoning_blocks(text)
    lb, rb = text.find("["), text.rfind("]")
    if not (0 <= lb < rb):
        return [], False
    try:
        arr = json.loads(text[lb:rb + 1])
    except json.JSONDecodeError:
        return [], False
    if not isinstance(arr, list):
        return [], False
    out: list["ExtractedFact"] = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        try:
            out.append(ExtractedFact(
                category=str(item.get("category", "")),
                subject=str(item.get("subject") or "user"),
                key=str(item.get("key", "")),
                value=str(item.get("value", "")),
                confidence=float(item.get("confidence", 0.5)),
                evidence=str(item.get("evidence", "")),
            ))
        except (TypeError, ValueError):
            continue
    return out, True
```
并把现有 `_parse_extracted` 改为薄壳复用（**保持其对外行为不变**，已有测试断言 `_parse_extracted("not json") == []`）：
```python
def _parse_extracted(raw: str) -> list[ExtractedFact]:
    facts, _ = _try_parse_facts(raw)
    return facts
```

### 3. ctor 追加参数 + 字段
签名末尾加 `, content_dedup: bool = False, content_ttl_s: int = 3600, content_cache_max: int = 256`（默认 False 保 BC，不破坏现有调用）。ctor 体加：
```python
self._content_dedup = bool(content_dedup)
self._content_ttl_s = int(content_ttl_s)
self._content_cache_max = int(content_cache_max)
self._recent_content: "OrderedDict[str, float]" = OrderedDict()
self._dedup_lock = asyncio.Lock()
```

### 4. 新增私有方法 `_revoke_placeholder`
```python
async def _revoke_placeholder(self, h):
    if h is not None:
        async with self._dedup_lock:
            self._recent_content.pop(h, None)
```

### 5. 改 `process_message`（核心，把 1033-1054 段改成下面逻辑）
```python
        if not content or len(content.strip()) < self._min_chars:
            return []

        # —— Layer 1 内容哈希幂等短路（仅 user_message；summarizer 等不短路）——
        h = None
        if self._content_dedup and source == "user_message":
            h = _content_hash(source, content)
            async with self._dedup_lock:                 # 命中判定 + 抢占登记 原子（防并发 TOCTOU 双抽）
                now = time.time()
                while self._recent_content:               # 清过期（最旧端）
                    k0, t0 = next(iter(self._recent_content.items()))
                    if now - t0 >= self._content_ttl_s:
                        self._recent_content.popitem(last=False)
                    else:
                        break
                hit_t = self._recent_content.get(h)
                if hit_t is not None:
                    log.info("facts_extract_skip_dup seen_ago=%.0fs", now - hit_t)
                    return []
                self._recent_content[h] = now             # 抢占登记占位（LLM 前）
                self._recent_content.move_to_end(h)
                while len(self._recent_content) > self._content_cache_max:
                    self._recent_content.popitem(last=False)

        # FP-4 WI-3.1: select prompt based on goal_facts flag
        _prompt_tmpl = _EXTRACT_PROMPT_WITH_GOALS if self._goal_facts else _EXTRACT_PROMPT
        try:
            raw = await self._extract_llm(_prompt_tmpl.format(content=content[:2000]))
        except Exception as exc:  # noqa: BLE001
            log.warning("FactExtractor.extract LLM failed: %s", exc)
            await self._revoke_placeholder(h)             # LLM 异常 → 撤销占位，可重试（不丢事实）
            return []
        extracted, parse_ok = _try_parse_facts(raw)
        if not parse_ok:                                  # 坏 JSON → 撤销占位（防 relay 偶发坏响应吞事实 1h）
            await self._revoke_placeholder(h)
            return []
        if not extracted:                                 # parse_ok 但 0 条（LLM 确说无可抽）→ 保留占位
            return []

        # Stage 2 D13 v2：summarizer 来源 → category override
        if source == "summarizer":
            for f in extracted:
                f.category = "episodic_summary"

        async with self._persist_lock:
            persisted = await self._persist_extracted(extracted, message_id)

        # 成功处理完 → 把占位时间戳刷新为"完成时刻"(TTL 基准对齐) + LRU 提鲜
        if h is not None:
            async with self._dedup_lock:
                if h in self._recent_content:
                    self._recent_content[h] = time.time()
                    self._recent_content.move_to_end(h)
        return persisted
```

## 硬约束
- **只改 facts.py**。不改 config.py / main.py / 测试（别的 codex 负责）。
- `content_dedup` 默认 False → 不传时行为字节级不变（现有所有测试调用 FactExtractor 不传这 3 个参数，必须全绿）。
- 别破坏 `_parse_extracted` 对外行为（薄壳复用）。
- 并发：cache 的判定/登记/撤销/刷新都在 `self._dedup_lock` 内；LLM 抽取在锁外（不串行化）。`_dedup_lock` 与 `_persist_lock` 不嵌套。

## 自测（必须跑，绿了才算完成）
```
cd G:\projects\deskpet\backend
.venv\Scripts\python.exe -m pytest tests/test_memory_tools_e2e.py tests/test_facts_extractor.py tests/test_memory_v2_config.py -q -p no:cacheprovider
```
（若 `test_facts_extractor.py` 不存在就跑 `tests/` 下所有含 facts 的测试文件）确保现有 facts 相关测试**全绿**（证明 content_dedup=False 默认不破坏现状）。语法用 `.venv\Scripts\python.exe -c "import ast; ast.parse(open(r'backend/deskpet/memory/facts.py',encoding='utf-8').read())"` 先验。

## 完成后输出
- 改了哪些函数/行；自测命令的真实输出（pass 数）；有没有踩坑。**不要谎报通过**——跑了才说。
