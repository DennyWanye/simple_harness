# 子代理通道：Grok Build 与 Codex（无人值守）

指挥者（Claude）只派任务与检查；实施、核验、诊断、文档交给这两个通道。两者接口同形：

```
grok_task.sh  <任务名> <工作目录> <任务书.md> [推理强度=high] [最大轮数=80] [续接会话号]
codex_task.sh <任务名> <工作目录> <任务书.md> [推理强度=high] [沙箱=workspace-write] [续接会话号]
```

- 产物目录：`$AGENT_TASK_OUT/<任务名>/`（缺省 `~/.cache/simpleharness-agent-tasks/{grok,codex}/`）。stdout 末段是一行摘要（`GROK_TASK …` / `CODEX_TASK … session=…`）加最终回复尾部；摘要里的会话号用于续接同一会话做修复或复核。
- 两个脚本都内置纪律：不 push / stash / checkout / reset；不读不打印任何密钥文件；只在给定目录工作；记录文档用中文；测试先行；最终回复以「## 结果」收尾。
- Grok：`grok` 命令行，模型 grok-4.6，走 SuperGrok 订阅（见 `docs/GROK-BUILD-LANE.md`；做真实模型验收时另需 `grok_build_runtime.py`，与本脚本无关）。
- Codex：`/Applications/ChatGPT.app/Contents/Resources/codex`（随 ChatGPT 桌面应用自带，可用 `CODEX_BIN` 覆盖）。模型与服务端点由 `~/.codex/config.toml` 决定（cc-switch 管理）；2026-09-18 时为自定义端点上的 deepseek-v4.1-flash。必须关闭标准输入（脚本已做），否则会挂住等输入。沙箱缺省 `workspace-write`：只能写工作目录、git 公共目录与 uv 缓存，命令无网络；纯只读任务传 `read-only`。
- 分工建议：核心代码实施与独立核验用 Grok（实施与核验分属不同会话）；Codex 用于并行的低风险任务——文档与台账编纂、数据表、只读排查、测试清单、对 Grok 结论的第二意见。需要升级时才用 Claude 子代理。
- 首次冒烟（2026-09-18）：Codex 在 SDK 主树跑 `test_repeated_failure_early_stop.py` 12 通过，17 秒，主树保持干净。
