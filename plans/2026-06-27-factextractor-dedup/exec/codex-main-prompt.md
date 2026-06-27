你是资深 Python 工程师。任务：把 FactExtractor 的内容去重 flag 接线到 main.py 并加 boot-log 实证。**只改 `backend/main.py` 这一个文件**。

## 仓库（绝对路径）
- 仓库根：`G:\projects\deskpet`（git，Windows）；目标：`G:\projects\deskpet\backend\main.py`；venv：`G:\projects\deskpet\backend\.venv\Scripts\python.exe`

## 前提（别的 codex 已做，你只需引用）
- `config.py`：`config.memory.v2.extract_content_dedup`(bool)、`config.memory.v2.facts.content_dedup_ttl_s`(int)、`config.memory.v2.facts.content_dedup_cache_max`(int) 已存在。
- `facts.py`：`FactExtractor.__init__` 已加 kw 参数 `content_dedup: bool=False, content_ttl_s: int=3600, content_cache_max: int=256`。

## 现状代码（main.py:1427-1441，照此修改；该段已有局部变量 `_v2_cfg = config.memory.v2`，见 :1411）
```python
            _fact_extractor = _FactExtractor(
                _facts_store,
                extract_llm=_facts_llm,
                min_chars=_v2_cfg.facts.min_user_chars,
                cross_key_merge=_cross_key_enabled,
                cross_key_llm=_facts_llm,
                embedder=_embedder,
                goal_facts=_v2_cfg.goal_facts,  # FP-4 WI-3.1
            )
            logger.info(
                "p4_fact_extractor_ready",
                min_chars=_v2_cfg.facts.min_user_chars,
                cross_key_merge=_cross_key_enabled,
                goal_facts=_v2_cfg.goal_facts,
            )
```

## 改成
```python
            _fact_extractor = _FactExtractor(
                _facts_store,
                extract_llm=_facts_llm,
                min_chars=_v2_cfg.facts.min_user_chars,
                cross_key_merge=_cross_key_enabled,
                cross_key_llm=_facts_llm,
                embedder=_embedder,
                goal_facts=_v2_cfg.goal_facts,  # FP-4 WI-3.1
                content_dedup=_v2_cfg.extract_content_dedup,            # 2026-06-27 内容哈希幂等去重
                content_ttl_s=_v2_cfg.facts.content_dedup_ttl_s,
                content_cache_max=_v2_cfg.facts.content_dedup_cache_max,
            )
            logger.info(
                "p4_fact_extractor_ready",
                min_chars=_v2_cfg.facts.min_user_chars,
                cross_key_merge=_cross_key_enabled,
                goal_facts=_v2_cfg.goal_facts,
                content_dedup=_v2_cfg.extract_content_dedup,            # boot 实证 flag 真传进去
            )
```

## 硬约束
- **只改 main.py 这两处**（构造 + boot-log）。别动别的。
- 缩进必须与现有一致（该段在 try 块内，缩进较深）。

## 验证（必须跑）
```
cd G:\projects\deskpet\backend
.venv\Scripts\python.exe -c "import ast; ast.parse(open(r'main.py',encoding='utf-8').read()); print('main.py syntax OK')"
```
（main.py 启动需整个 app，难单测；语法过 + 改动点正确即可。）

## 完成后输出
改了哪两处 + 语法验证输出。不要谎报。
