你是资深 Python 工程师。任务：给 DeskPet 的 config 加 3 个字段，支撑 FactExtractor 的内容哈希幂等去重。**只改 `backend/config.py` 这一个文件**。

## 仓库（绝对路径）
- 仓库根：`G:\projects\deskpet`（git，Windows）
- 目标文件：`G:\projects\deskpet\backend\config.py`
- venv：`G:\projects\deskpet\backend\.venv\Scripts\python.exe`

## 要加的字段

### 1. `MemoryV2FactsConfig`（约 config.py:155，现有内容如下）
```python
@dataclass
class MemoryV2FactsConfig:
    """``[memory.v2.facts]`` — facts 抽取调参（记忆系统升级）。"""
    min_user_chars: int = 8       # 字数采样门，取代 facts.py 硬编码 <8
    facts_weight: float = 0.2     # facts 路进 RRF 的权重
    model_override: str = ""      # 留空 = 用主 LLM
    # Stage 2 D8 v2：entity 路 RRF 权重；v1 是 0.15，保守降为 0.10。
    entity_weight: float = 0.10
```
在 `entity_weight` 后追加两行：
```python
    # 2026-06-27 内容哈希幂等去重（FactExtractor Layer 1）：TTL 与缓存上限。
    content_dedup_ttl_s: int = 3600        # 相同内容近期(秒)抽过则跳过重抽
    content_dedup_cache_max: int = 256     # in-process 内容哈希 LRU 上限
```

### 2. `MemoryV2Config`（约 config.py:178）
这是 `[memory.v2]` 的 dataclass，里面是一排 bool flag（如 `facts_extract: bool = True`、`goal_facts: bool = True`、`auto_learnings: bool = True` 等，2026-06-27 测试阶段大多已默认 True）。在这些 flag 中**追加一行**（放在 `auto_learnings` 附近、`facts`/`forget` 子字段之前）：
```python
    # 2026-06-27 内容哈希幂等去重（FactExtractor Layer 1）：相同内容跳过重抽，防重复事实累积。
    # 测试阶段出厂点亮（与 facts_extract 同档）。OFF 时 FactExtractor 不启用内容去重 = 字节级 BC。
    extract_content_dedup: bool = True
```
**注意**：别加在 `facts:` / `forget:` 这两个嵌套 dataclass 字段之后（它们用 `field(default_factory=...)`，必须在末尾）。`extract_content_dedup` 是普通 bool，放在那些 bool flag 之间即可。

## 验证（必须跑）
```
cd G:\projects\deskpet\backend
.venv\Scripts\python.exe -c "from config import MemoryV2Config, MemoryV2FactsConfig; c=MemoryV2Config(); print('extract_content_dedup=', c.extract_content_dedup); print('ttl=', c.facts.content_dedup_ttl_s, 'max=', c.facts.content_dedup_cache_max)"
.venv\Scripts\python.exe -m pytest tests/test_memory_v2_config.py tests/test_config.py tests/test_config_feature_flag_backfill.py -q -p no:cacheprovider
```
期望：打印 `extract_content_dedup= True` / `ttl= 3600 max= 256`；config 测试全绿（`_load_section` 自动解析 dataclass 新字段，`_MIGRATABLE_SECTIONS` 已含 `("memory","v2")`+`(...,"facts")`，无需改 allow-list）。

## 硬约束
- **只改 config.py**。不改 facts.py/main.py。
- 不破坏现有 config 测试。

## 完成后输出
- 改了哪几行；验证命令真实输出。不要谎报，跑了再说。
