# Grok Build lane：用 SuperGrok 订阅在 Host 里跑 grok-4.6

**最后更新**：2026-09-16 CST
**结论**：不需要 console.x.ai 的 API Key、不需要买 API credits。Grok Build CLI（`grok`）登录后的订阅 token 可以直接当 Host 的 OpenAI 兼容端点用，用量记在 grok.com → Settings → Usage → **Build**（订阅周额度池）。
**证据**：`.local-test-evidence/2026-09-16/grok-build-lane/`（curl 探针、Host provider 探针、S5a real_provider 用例 1 passed / 9.18s）。

---

## 1. 一分钟上手（后续 Agent 照做即可）

前提：本机已装 Grok Build CLI 且已登录（`grok models` 能列出 grok-4.6）。

```bash
cd /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend
.venv/bin/python scripts/grok_build_runtime.py write     # 从 ~/.grok/auth.json 生成 llm_runtime.grok.json（0600）
.venv/bin/python scripts/grok_build_runtime.py probe     # 用 Host 自己的 OpenAICompatibleProvider 跑流式 + 工具调用，末行 PROBE OK
.venv/bin/python scripts/grok_build_runtime.py apply     # 备份 llm_runtime.json，切到 grok lane
.venv/bin/python -m pytest tests/sdk_adapters/test_s5a_milestone_real_provider.py -o addopts="" -m real_provider -q
.venv/bin/python scripts/grok_build_runtime.py restore   # 还原原来的 llm_runtime.json（默认删备份）
.venv/bin/python scripts/grok_build_runtime.py status    # 随时看 token/lane 状态，不打印秘密
```

`apply` 与 `restore` 之间 Host 的所有真实模型调用（real_provider 用例、原生旅程、语料批次）都走 grok-4.6。跑完必须 `restore`，否则主线默认模型被换掉。

不想动 `llm_runtime.json` 的话，单独的用户目录也行：`DESKPET_USER_DATA_DIR=<dir>`，把 `llm_runtime.grok.json` 复制成该目录下的 `llm_runtime.json`。

## 2. 它是怎么通的

| 项 | 值 |
|---|---|
| 端点 | `https://cli-chat-proxy.grok.com/v1/chat/completions`（OpenAI 兼容；stream 与非 stream 都通） |
| 鉴权 | `Authorization: Bearer <~/.grok/auth.json 第一个条目的 key>` |
| 必带 header | `X-XAI-Token-Auth: xai-grok-cli`、`x-grok-model-override: grok-4.6`、`x-grok-client-version: <grok --version>`、`User-Agent: xai-grok-cli`（少了客户端标识返回 426） |
| 模型 id | 请求写 `grok-4.6`，回显 `grok-4.6-build`（代理映射成订阅版） |
| 能力 | `reasoning_content` 增量帧、`tool_calls` + `finish_reason=tool_calls`、`stream_options.include_usage` 末帧 usage（含 cached_tokens / reasoning_tokens / cost_in_usd_ticks） |
| 订阅可用模型 | `grok models`：grok-4.6（默认）、grok-4.5 |
| 官方依据 | `~/.grok/README.md` 的 "Using auth.json for API Access" 一节（xAI 自带文档，非逆向） |

Host 侧改动（2026-09-16）：

- `backend/deskpet/provider_extra_headers.py`：从 `llm_runtime.json` 读 `extra_headers`（按 base_url 的 host 匹配）和 `extra_headers_by_host`，按 mtime 缓存。
- `backend/providers/openai_compatible.py`：`_client()` 合并上述 header；构造函数新增 `extra_headers=` 显式覆盖。
- `backend/main.py` 与 `backend/deskpet/orchestration/provider.py`：SDK 适配器的 httpx 客户端装了 request hook，同样按 host 注入，DeepSeek / 中转站端点不会收到 Grok 头。
- `backend/tests/sdk_adapters/test_s5a_milestone_real_provider.py`：自建 httpx 请求透传 `extra_headers`。
- 单元测试：`backend/tests/test_provider_extra_headers.py`（6 项）。

`llm_runtime.grok.json` 的形状：

```json
{
  "base_url": "https://cli-chat-proxy.grok.com/v1",
  "model": "grok-4.6",
  "api_key": "<auth.json key，勿外泄>",
  "max_tokens": 6144,
  "extra_headers": {
    "X-XAI-Token-Auth": "xai-grok-cli",
    "x-grok-model-override": "grok-4.6",
    "x-grok-client-version": "1.0.30",
    "User-Agent": "xai-grok-cli"
  },
  "_lane": "grok-build"
}
```

## 3. 坑与边界

- **token 寿命**：auth.json 的 `expires_at` 显示 6 小时，但过期后实测仍可用，实际寿命不明（README 另说 7 天）。代理返回 401 就跑 `grok login`，再 `write`。`status` 会显示 grok 配置里的 token 是否与 auth.json 一致。
- **不要把 token 打印、复制进对话或提交进仓库**。`llm_runtime.grok.json` 在用户数据目录，不在 repo；证据目录里的文件已做脱敏检查。
- **计费账本**：`pricing` 表没有 grok 条目，BillingLedger 按未知模型 20 元/百万 token 悲观计费，只影响 `daily_budget_cny` 上限，不影响功能。官方 API 牌价 $2 / $6 每百万 token 仅供参考，订阅走的是周额度不是账单。
- **周额度**：SuperGrok Heavy 是每周统一算力池，Chat / Build / API / Imagine / Voice 共用；长上下文批次消耗快，用完当周暂停。跑大批次前看一眼 Usage 页。
- **换模型**：`write --model grok-4.5` 会同时改 `model` 和 `x-grok-model-override`；两者必须一致。
- **另外两条通路不用**：`grok -p … --output-format json` headless 只能拿最终文本，Host 的自定义工具进不去；`grok agent stdio`（ACP）要另写客户端。都不如直连代理。
- **models_cache** 把 grok-4.6 标成 `api_backend: responses`，但 `/chat/completions` 实测可用，不必改协议。

## 4. 本次验证记录（2026-09-16 00:20 CST）

| 步骤 | 结果 |
|---|---|
| curl 直连代理，stream | 200，回显 grok-4.6-build，reasoning_content + content |
| curl 直连代理，tools | 200，`get_time({"tz":"Asia/Taipei"})`，finish_reason=tool_calls |
| curl 非 stream | 200，usage 含 reasoning_tokens=104、cost_in_usd_ticks |
| `grok -p` headless json | 5s，usage 13807 in / 44 out，modelUsage 键 grok-4.6-build |
| `scripts/grok_build_runtime.py probe`（Host provider） | PROBE OK，工具调用 4.3s |
| `test_real_provider_no_recall_single_invocation`（grok lane） | 1 passed in 9.18s，随后 restore 成功 |
| 单元 + 回归 | test_provider_extra_headers 6 passed；empty_api_key_guard / context_os_e2e_product_hooks / relay_llm_bridge / runtime_provider_seed 32 passed |
