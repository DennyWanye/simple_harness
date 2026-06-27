# 任务 G3 — 空 API key 护栏 + provider 可观测（Phase 4 + 5 的 provider 部分）

你是编码 Expert。仓库根：`G:\projects\deskpet`。只改下面 3 个文件 + 1 个新测试文件，**不要 git commit**，不碰 main.py / paths.py / config.py。
背景见 `G:\projects\deskpet\plans\2026-06-28-userdata-path-binding-fix\00-PLAN.md` §2.4 + §5 Phase 4/5。

## 问题
装机版聊天报 `Illegal header value b'Bearer '`：空 api_key 被直接拼进 `Authorization: Bearer ` 头 → httpx `LocalProtocolError`，把对话刷崩且对用户不可读。要在 provider 层加统一拦截点：空/占位符 key 时**抛类型化异常**，不发请求。

## 1. `G:\projects\deskpet\backend\llm\provider_registry.py` — 新增异常 + registry 可观测
- 在 `KeyMissingError` 旁新增：
```python
class EmptyApiKeyError(RuntimeError):
    """Provider has no usable api_key (empty/placeholder) — caller should
    surface a 'login required / configure provider' message instead of
    sending an illegal empty 'Bearer ' header."""
    def __init__(self, provider_id: str = "?") -> None:
        self.provider_id = provider_id
        super().__init__(f"empty api key for provider {provider_id!r} — login or configure required")
```
- 加进 `__all__`。
- `LLMProviderRegistry.__init__` 末尾（`self._load_from_toml()` 之后）加一条 INFO 日志，便于线上确认加载情况：
```python
logger.info(
    "provider_registry_ready n=%d enabled=%d ids=%s",
    len(self._entries),
    sum(1 for e in self._entries if e.enabled),
    [e.id for e in self._entries],
)
```

## 2. `G:\projects\deskpet\backend\llm\openai_adapter.py` — 发请求前拦空 key
先读该文件理解 `OpenAICompatibleProvider`（构造 `self._api_key = api_key or get_api_key("openai")`，第 59 行附近；`available` 第 65 行；client 构造 78 行附近）。
定义占位符集合（与 main.py 一致）：`_PLACEHOLDER_KEYS = {"", "ollama", "from-keychain", "from-env", "your-key-here"}`。
在**真正发起补全请求的入口方法**（complete/stream，构造 openai client / 拼 header 之前）加守卫：
```python
if (self._api_key or "").strip().lower() in _PLACEHOLDER_KEYS:
    # 本地 ollama（base_url 含 localhost/127.0.0.1）允许占位 key，云端不允许
    if not self._is_local_base_url():
        from llm.provider_registry import EmptyApiKeyError
        raise EmptyApiKeyError(getattr(self, "_provider_id", "local"))
```
- 加 helper `_is_local_base_url()`：base_url 含 `localhost`/`127.0.0.1`/`0.0.0.0` 返回 True（Ollama 不需要真 key，"ollama" 占位合法）。
- 不要改变正常（非空真 key）路径的任何行为（向后兼容）。
- 若 `available()` 当前用 `bool(self._api_key)`，保持不变。

## 3. 测试 `G:\projects\deskpet\backend\tests\test_empty_api_key_guard.py`（新建）
- `test_empty_key_cloud_raises_empty_api_key_error` — base_url=远端、api_key="" 或 "from-keychain" → 调 complete/stream 抛 `EmptyApiKeyError`（用 monkeypatch/stub 避免真网络；断言在构造 client/发请求前就抛）。
- `test_placeholder_key_local_ollama_allowed` — base_url 含 localhost、api_key="ollama" → **不**抛（本地放行）。
- `test_real_key_unaffected` — 给个真 key（如 "sk-xxx"）→ 不抛 EmptyApiKeyError（走原路径；可在更外层 stub 掉网络）。
- `test_registry_ready_log` — 构造一个有 1 enabled provider 的 `LLMProviderRegistry`（用临时 config.toml），caplog 断言出现 `provider_registry_ready`。

## 验收（自己跑通）
```
cd G:\projects\deskpet\backend
.venv\Scripts\python.exe -m pytest tests/test_empty_api_key_guard.py tests/test_p5s2_provider_registry.py -q
```
新测试全绿 + provider_registry 现有测试不回归。`.venv\Scripts\python.exe -c "from llm.openai_adapter import OpenAICompatibleProvider; from llm.provider_registry import EmptyApiKeyError"` 不报错。完成后输出改动摘要 + 测试结果。
