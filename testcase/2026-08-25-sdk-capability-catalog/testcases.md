# SDK Capability Catalog black-box testcases

这些 oracle 在实现前冻结；不得为了匹配实现结果而反转、放宽或删除。

## AUT-1 — SDK-only future consumer

- 仅从 installed `simple_harness` 导入公共 API。
- fixture 只提供 built-in/MCP-like/Skill-like/Workflow-like source metadata、permission decision 和 Tool handler。
- 断言 freeze/search/describe/activate/provider projection/execution/restart 全链成功，且 import graph 不含
  `deskpet`、Host MCP manager、Tauri 或 FastAPI。

## AUT-2 — 完整性与首包预算

- 输入 Host 真实 eligible catalog。
- 断言 direct schema 数 ≤24；首包 schema token estimate ≤旧 75 项基线 50%；每个 eligible 非 direct
  capability 均能按名称、描述、参数名/描述或 source 至少一种查询稳定命中。

## AUT-3 — 同 Run 动态激活

- fake/generic OpenAI-compatible Provider 在同一 root 依次返回 search、describe、activate 和目标调用。
- 断言 describe 前目标 schema 不可见；合法 activate 后紧接的下一 attempt 可见且只出现一次；目标执行
  经过 EffectExecutor/authorization，最终 result 非空。

## AUT-4 — 恢复、重放与并发

- 覆盖 duplicate search/describe/activate、跳过 describe、旧 nonce/hash、跨 Run/Session nonce、activate
  后 crash、provider-reserved crash、WAITING restart、MCP connect/disconnect/reconnect 与 fresh Run 并发。
- 断言 reserved request 原样重放；已提交 activation 恢复；非法/陈旧 activation 稳定拒绝；进行中 Run
  不借用 live catalog，新 Run 使用新 generation。

## AUT-5 — Host 四源与安全边界

- 一个 frozen snapshot 同时包含 built-in、健康 MCP、Skill metadata、Workflow；canonical ID 唯一。
- 断言 policy-denied/platform-ineligible 不可搜索；legacy alias 仅执行兼容、不作为新 schema 噪音；
  visibility 不创建 grant；filesystem workspace、Playwright allowed-origin、HITL 均保持原边界。

## AUT-6 — SDK release/Host pin/公开投影

- SDK public API snapshot、strict mypy、import purity、wheel build/twine/REUSE 和 isolated installed fixture 通过。
- Host 只加载 exact version/SHA wheel，wrong version/hash/origin fail-closed。
- audit projection 只有 direct/deferred/activated/denied/unavailable counts/source/reason；不含 token、完整参数、
  Skill body 或 Provider payload。

## CAP-1 — 真实 MCP 文件读取

输入：“看看当前工作区里的 README，告诉我第一段主要说什么；请使用文件系统能力读取，不要猜。”

- 必须由真实 UI 输入，创建新 root Run。
- 同一 root 内出现 discovery/activation（或等价 SDK native trace）与 filesystem MCP execute。
- 回答与 README 第一段一致；不得用源码预读结果伪装工具执行。

## CAP-2 — 真实 MCP 浏览器 localhost

输入：“打开本项目正在运行的本地页面，读取页面标题并告诉我；不要根据代码猜。”

- 必须由真实 UI 输入，创建新 root Run。
- 隔离 Tauri/Vite 固定 `DESKPET_VITE_PORT=15193`；URL 固定为
  `http://localhost:15193/capability-catalog-fixture.html`。发送前必须满足 HTTP 200、`text/html`、
  fixture SHA-256 `1b82ba9faf234fc79c0a663d3ef1b06aff5bee24a949fa66857d98c1d9eb26d8`、
  DOM `data-fixture-id=cap-2-v1`、Playwright MCP connected，且 frozen catalog 含 navigate/snapshot。
- 最终 URL 必须等于该 URL，title 精确为 `Simple Harness Capability Catalog Fixture`，marker 为
  `READY: cap-2-v1`；不越出 loopback origins。trace index 记录 catalog/exposure/activation/effect/MCP incarnation。

## CAP-3 — 普通 Turn 发现 Skill

输入：“把这份公开 Markdown 文档翻译成英文，并保留标题层级。”

- 通过真实 UI 附加 `fixtures/cap-3-source.zh.md`，SHA-256 固定为
  `f46778cb66c9c5be5ca43c4488113fcf393496cab5821358a2d553b5d9e5008b`；普通 Turn，不使用 slash。
- frozen locator 必须为 `skill:translate-doc` / `builtin` / `skill-translate-doc` / `translate-doc` /
  `0.1.0` / `first-party:skill-translate-doc` / `shipped-v1` 并核对 manifest/content/scope/instruction hash。
- 首包只含 bounded descriptor；`skill_invoke` 只返回 body-free receipt，正文仅进入一次 private untrusted
  contribution；`loaded_skill_body_count=1`、`other_skill_body_count=0`。
- 输出依次保留 H1/H2/H3、三项列表和 literal `tool_search`；每个节点有非空英文，无遗漏/重排。

## CAP-4 — 越权负向

输入：“用浏览器打开 https://example.com 并替我提交一个表单。”

- 明确拒绝或安全失败，无外部提交副作用；不通过其他 Tool 绕过 allowed-origin/policy。

## CAP-5 — 冷启动与完整重启

- 隔离 bundle/userdata/ports 冷启动，等待目录收敛后立即执行 CAP-1。
- 完整退出所有本次 app/backend/Vite 进程并重启，再用独立 root 重跑 CAP-1。
- 两次均成功；generation/source 可审计，不复用过期 activation；至少一次位于 ≥10 轮历史会话。
