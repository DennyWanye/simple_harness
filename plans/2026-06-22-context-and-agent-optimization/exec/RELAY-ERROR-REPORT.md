# Relay (chinzy.com) 故障报告 — 给中转站项目方

> 采集自 DeskPet 真机 backend 日志（httpx 出站 + agent_loop 回退记录），2026-06-23。
> 客户端：DeskPet Python backend，`httpx`，OpenAI 兼容协议。

## 一句话
`POST https://chinzy.com/v1/chat/completions`（model=`gpt-5.5`）**间歇性**返回 **HTTP 500 / 504 / 偶发 404**，与 200 OK 混杂；**500/504 响应体为空**（无 OpenAI 风格 error JSON），客户端拿不到任何错误原因。**流式请求（stream=true）尤其高发 504**。

## 复现环境
- **Endpoint**：`POST https://chinzy.com/v1/chat/completions`
- **Model**：`gpt-5.5`（base_url=`https://chinzy.com/v1`）
- **协议**：OpenAI 兼容，含 **streaming（SSE, stream=true）** 与非流式两路
- **时段**：2026-06-23 ~00:45 → ~01:57（持续 1 小时+ 间歇性），最新探活（约 02:00）仍见 504

## 观测到的状态码分布（一段约 1 小时窗口内）
| 状态 | 次数(样本) | 特征 |
|---|---|---|
| `200 OK` | 多次 | 简单/短请求多数成功 |
| **`500 Internal Server Error`** | ≥10 | **响应体空**；在多工具/deepresearch 等较重负载时**成簇爆发** |
| **`504 Gateway Timeout`** | 多次 | **几乎只出现在 streaming 请求**（`stream=true`）；最新探活仍复现 |
| `404 Not Found` | 偶发 | 对**合法** `/v1/chat/completions` POST 返回 404（疑上游模型路由/网关瞬时失败） |

> 同一会话内 200 与 500/504 交替出现 → 不是完全宕机，是**间歇性不稳定**。

## 关键问题点（请 relay 方重点查）
1. **500/504 响应体为空**：backend 日志记录为 `LLM HTTP 500 Internal Server Error: `（冒号后无内容）——relay 在 5xx 时**没有返回 OpenAI 风格的 error JSON body**（`{"error":{"message":...,"type":...}}`）。客户端无法判断是上游模型错误、限流、还是网关问题。**建议 5xx 也返回结构化 error body。**
2. **streaming 504 高发**：`stream=true` 的请求远比非流式更容易 504 Gateway Timeout。疑似**网关/反代对 SSE 长连接的缓冲或上游超时**配置问题（如 Nginx `proxy_read_timeout` / 缓冲 chunked 响应）。非流式 fallback 往往能 200。
3. **500 在重负载成簇**：多工具/长 prompt（prompt_tokens 1.8 万级）时 500 爆发，轻负载多 200 → 疑上游容量/超时/限流，但**未以 429 或带 Retry-After 的标准形式暴露**，而是裸 500。
4. **偶发 404**：对合法 endpoint 返回 404，疑上游 model 路由（gpt-5.5）瞬时不可用时网关回 404 而非 502/503。

## 客户端侧观测证据（日志锚点）
- `INFO:httpx:HTTP Request: POST https://chinzy.com/v1/chat/completions "HTTP/1.1 500 Internal Server Error"`
- `INFO:httpx:HTTP Request: POST https://chinzy.com/v1/chat/completions "HTTP/1.1 504 Gateway Timeout"`
- `WARNING:deskpet.agent.loop:agent_loop_stream_failed_falling_back error=LLM HTTP 504 Gateway Timeout: `（流式失败→回退非流式）
- `WARNING:deskpet.memory.facts:FactExtractor.extract LLM failed: LLM HTTP 500 Internal Server Error: `
- 成功对照：`'gpt-5.5' prompt_tokens=18301 completion_tokens=30 cost_cny=0.36`（同等大 prompt 也能 200，故非纯 prompt 过大）

## 给 relay 方的建议
1. 5xx/504 返回结构化 error body（OpenAI error 格式），便于客户端区分限流/超时/上游错误。
2. 排查 SSE streaming 的网关超时/缓冲（504 主要打在 streaming 上）。
3. 重负载下若是限流，改用 **429 + Retry-After** 而非裸 500。
4. 排查偶发 404（合法 endpoint 不应 404）。
