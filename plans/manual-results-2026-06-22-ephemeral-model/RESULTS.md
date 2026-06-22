# 真机验证 — `ephemeral_subagent_model` 配置生效（boot-log 档）

> 日期: 2026-06-22 · 分支: master · 验证人: Claude (Opus 4.8)
> 关联修复: `backend/main.py` `_resolve_ephemeral_provider` + `build_agent` 接电
> 证据来源任务: plans/2026-06-22-context-and-agent-optimization R2 对抗审查

---

## 1. 修复对象（shipped 真 bug）

配置项 `[tools.verifier].ephemeral_subagent_model`（`backend/config.py:271`，默认 "haiku" +
白名单校验 VG-INVARIANT-5）**从未被消费**。自我纠错闭环（verify-gate 失败/停滞升级到
ephemeral 子代理重校验）在 `build_agent` 注入 ephemeral verifier 时直接用
`local_llm or cloud_llm`，**没读 config**，导致用户/默认配的专用模型永远复用主 LLM。

修法：新增 `_resolve_ephemeral_provider(base, model_name)` —— 按配置克隆出专用 model 的
`OpenAICompatibleProvider`（中转站按 model id 路由，复用 base 的 base_url/api_key/temperature/
sanitize），缺省/同名/克隆失败回退 `base`（保持旧行为为兜底）。

## 2. 真机环境（真实运行栈，非脚本/非协议注入）

- 启动方式: `scripts/dev-worktree.ps1` 同款隔离 dev —— **Tauri 自己 spawn** 源码后端
  （`DESKPET_BACKEND_DIR=backend` + `DESKPET_PYTHON=backend/.venv`），隔离端口 8200/5273 +
  隔离 `DESKPET_USER_DATA_DIR=.dev-userdata`，**不碰**用户主实例。
- 后端身份: 监听 8200 的进程 = `python.exe` 跑 `backend/main.py`（源码，含本次修复），
  非 frozen bundled exe。
- 测试配置: `.dev-userdata/config.toml` 拷自用户真实 config，仅改一行
  `ephemeral_subagent_model = "sonnet"`（区别于主 LLM `gpt-5.5`，便于判定）；
  `verify_gate_mode = "shadow"` + `emit_receipts = true`（用户真实配置原值）。
- 触发: 真 UI —— 恢复桌宠窗 → 在聊天框输入消息 → 点「发送」（computer-use 真模拟点击，
  非 WebSocket 注入）。`build_agent` 在每个 chat task 起始构造 verify-gate + ephemeral verifier。

## 3. 判定证据（real backend log，Tauri dev 重定向）

```
event='startup complete'        @ 07:14:00  port=8200  (源码后端起来了)
event='ephemeral_verifier_model' @ 07:16:22  model='sonnet' base='gpt-5.5'   ← 关键
event='verify_gate_init'         @ 07:16:22  mode='shadow' patterns=9
```

- **`model='sonnet'`** = 配置的 `ephemeral_subagent_model`，**不是** `base='gpt-5.5'`（主 LLM）。
  修复前此处恒为 base（gpt-5.5），配置永不生效；修复后真正解析出专用模型。
- 同回合 `verify_gate_init mode='shadow'` 证明 verify-gate 确实构造（ephemeral verifier 接在其上）。
- 桌宠聊天回复「我当前底层模型是 gpt-5.5」= 主对话用主模型（正确），与 ephemeral 救援
  子代理用 sonnet 互不影响（职责分离正确）。

完整日志: `tauri-dev.run3.log`（run1 首跑 `tauri-dev.run1.log`）· 抽取: `evidence-loglines.txt`
· 桌宠窗截图见本会话 transcript（已连接 + 聊天回复「我当前底层模型是 gpt-5.5」）

## 4. 单测/集成测（同提交）

`backend/tests/test_ephemeral_subagent_model_wiring.py`（15 用例全绿）:
- 解析器 4 分支: 覆盖配模型→克隆 / 空→回退 / 同名→返回 base / base=None→None / 克隆异常→回退。
- build_agent 集成 2 例: 配 "sonnet" → ephemeral verifier 真用 sonnet；空 → 回退主 LLM。
- 回归 `test_build_agent_verify_wiring.py` 全绿。

## 5. 判定

**PASS** —— 真实运行栈（Tauri spawn 源码后端）+ 真 UI 触发 → backend log 实证
ephemeral verifier 按 `config.ephemeral_subagent_model` 解析出 `sonnet`（而非复用主 LLM）。

### 环境备注

- dev 桌宠窗在多显示器/DPI 下首启最小化（`window_geometry 221x30 below MIN` 循环），
  用 Win32 `ShowWindow(SW_RESTORE)` + 重定位到主显示器后正常渲染可点击（workaround 1 成功）。
- 为释放 `target/debug/deskpet.exe` 文件锁（os error 5），经用户授权停掉其运行中的桌宠
  实例（PID 65548）后增量构建。**用户需手动重启自己的桌宠。**
