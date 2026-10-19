# 交接：把 Simple Harness SDK 的任务编排接到别的应用

- 日期：2026-10-19
- 写给：任何要评估或接入这个 SDK 的会话 / 开发者
- 来源仓库：`simple_harness`（私有仓库，不要推到公开 GitHub），SDK 在 `sdk/simple-harness-sdk/`
- 当前版本：SDK `0.13.0.dev20260925+opt.195`（提交 deb9e006c），已发版并验证

## 一、一句话

这是一个 Python 库。装一个轮子，写几行代码，你的应用就能把一个任务交给它：它自己做分层规划、调模型执行、每步独立审阅、交付产物；授权、预算、并发、超时、崩溃恢复、事件审计都由它管。已经在三个仓库外的空 Python 项目里只装轮子跑通真实 DeepSeek 任务。

## 二、它做什么、不做什么

做：

- 建任务（目标 + 成功条件）→ 分层规划 → 模型执行每一步 → 独立审阅 → 验收通过后交付产物。
- 产物放在它自己的数据目录，用 `artifact_read` 读；也能发布到你授权的目录。
- 需要人拍板的环节有接口（授权规划、回答规划器提问、确认要求、批准对外操作、接管、裁决）。`auto_mode=True` 时纯内容类决定自动放行。
- 预算（token 上限、尝试次数）、并发、超时、恢复、审计，都在库里。

不做：

- 不管界面。任务列表、进度、审批卡片由你的应用画；库提供读接口与写回调。
- 不管模型配置界面。`base_url` / `model` / `api_key` 由你传入。
- 这一版只支持 DeepSeek（每个执行池要一个经认证的用量计数器）。
- 沙箱只在 macOS 可用（跑模型写的代码、核对 `file:` / `pytest:` 要求都靠沙箱）。别的系统能启动，但不跑代码、不核对要求。

## 三、硬性前提（先对这张表）

| 前提 | 说明 |
|---|---|
| 能跑 Python 3.11+ | 纯 Python 库。应用本身不是 Python 的，要起一个 Python 侧进程（参照：Simple Harness 是 Tauri 外壳 + Python 后台，前端经 WebSocket 控制通道调后台）。 |
| macOS | 沙箱靠 macOS Seatbelt。Linux 能装能起，但不跑代码、不核对要求。Windows 装得上但起不了执行池。 |
| DeepSeek 线路 | 官方端点；或你在 `compatible_hosts` 里声明的转发站。 |
| 首次启动能联网 | 宿主首启按钉死哈希下载 DeepSeek 分词文件（6 MB）到数据目录；也可自己放好传 `tokenizer_path`。 |
| 依赖 | 轮子自动带上 `httpx`、`pytest>=8.3,<9`、`deepseek-recipe==0.1.1`（非 Windows）。环境里已有 pytest 9 会冲突。 |

## 四、安装

轮子在仓库 `backend/vendor/simple_harness_sdk-<版本>-py3-none-any.whl`，或自己构建：

```bash
cd sdk/simple-harness-sdk && uv build        # 产物在 dist/
pip install "<轮子路径>"
```

没有发到 PyPI。换版本要重装。

## 五、最短程序

```python
import asyncio
from pathlib import Path
from agent_orchestrator.host import ModelConfig, OrchestrationHost, Principal

async def main():
    host = await OrchestrationHost.open(
        Path("./orchestration-data"),            # 它自己的数据目录：库、账本、工作区、产物
        principal=Principal("user-1", "我"),      # 谁在用
        model=ModelConfig(
            base_url="https://api.deepseek.com/v1",
            model="deepseek-flash",
            api_key="<密钥>",                     # 只进内存，不要落盘
            # 经转发站：compatible_hosts=("转发主机名",)
            # 转发站回显的模型名不同：response_model_aliases=("回显名",)
        ),
        auto_mode=True,                           # 纯内容类决定自动放行
    )
    try:
        receipt = host.create_mission({
            "goal": "写一份简短的中文说明，介绍三种家庭省电办法，保存为 NOTES.md。",
            "success_criteria": ["file:NOTES.md", "NOTES.md 列出三种办法，每种一句话"],
            "idempotency_key": "notes-1",         # 同键重试拿回同一个任务
        })
        final = await host.run_until_settled(receipt["mission_id"])
        print(final["snapshot"]["mission"]["status"], final["snapshot"]["budget_usage"])
        for a in final["snapshot"]["artifacts"]:
            print(a["path"], host.artifact_read(a["id"])["content"])
    finally:
        await host.close()

asyncio.run(main())
```

常驻应用不用 `run_until_settled`：`host.start_driver()` 起后台循环，前端轮询读接口，或挂 `host.on_write` 回调刷新。

## 六、接口一览（`OrchestrationHost`）

- 生命周期：`open` / `start` / `start_driver` / `run_forever` / `run_until_settled` / `close`；`status()` 给状态、模型、沙箱、发布、运行档、预算默认、全局月配额。
- 建任务：`create_mission`、`create_mission_with_sources`（附参考资料）、`source_command`（换资料）。
- 人工环节：`pending_planning_authorizations` / `planning_authorization`、`answer_planning_question`、`approve_operation_completion_spec`、`amend_requirements`、`submit_operation_intent` / `operation_intent_status`、`decide`、`takeover`、`resolve_unknown`、`comment`、`cancel`。
- 读：`missions`、`snapshot`、`events`、`approvals`、`artifact_read`、`method_library`、`global_budget_status`、`context_profiles`。
- 回调：`on_write`（有写入就通知，用来刷新界面）、`on_rebuilt`（重建后重连）、`on_round`（每轮一次的轻量杂务）、`notify=`（任务结束通知）。
- 要求怎么写：普通文字一条一个要求；`file:路径` 要求产出文件；`pytest:路径` 在沙箱里跑测试；`action:` 类要对外操作，需要发布连接器与人批准。
- 边界：只许从 `agent_orchestrator/public_api.py` 列出的公开模块导入；别的模块是内部实现。

## 七、参考资料

- 接入说明（权威）：`sdk/simple-harness-sdk/docs/orchestration-host.md`——安装、最短程序、入口做了什么、操作一览、要求怎么写、等人的时候、进度、数据目录、常见问题。
- 可运行例子：`sdk/simple-harness-sdk/examples/orchestration-host/demo.py`；`--scripted` 不联网也能跑通；真实模型用环境变量 `SH_BASEURL` / `SH_APIKEY` / `SH_MODEL`（经转发站再加 `SH_COMPATIBLE_HOSTS`、`SH_RESPONSE_MODEL_ALIASES`）。
- 一个真实桌面端怎么接的：`backend/deskpet/orchestration/wiring.py`、`service_domains/core.py`（Simple Harness 桌面端自己也走这同一个入口，私有属性只是对宿主的转发）。
- SDK 总说明：`sdk/simple-harness-sdk/README.md`。
- 实施记录与核验：`plans/2026-10-19-sdk-host-entry/方案与记录.md`、`独立核验-opt194.md`。

## 八、已验证的证据

| 项 | 结果 |
|---|---|
| 仓库外空项目只装轮子、不给分词文件，真实 DeepSeek 一局 | COMPLETED，13 万 token，约 2 分钟 |
| 仓库外空项目，脚本模型（不联网） | COMPLETED |
| Simple Harness 桌面端真机一局（同一入口） | COMPLETED |
| 独立核验（只读子代理，五轮） | 全部 PASS，无阻断 |
| SDK 宿主测试、mypy、公开边界检查 | 通过 |

## 九、已知的坑与限制

1. **线路会坑人。** 有第三方 DeepSeek 转发站把 `system` 消息丢掉，规划器看不到格式说明，整局失败。判法：把请求原样直发，看回报的 `prompt_tokens` 是否接近真实长度（正常约 1.2 万，被丢只剩 3 千）。
2. 只支持 DeepSeek；思考模式默认开（`ModelConfig(thinking="disabled")` 可关）。
3. pytest `<9` 的上限随 SDK 压给宿主应用。
4. Windows 装得上，起不了执行池（计数配方没有 Windows 轮子）。
5. 预算默认：一个任务 2000 万 token、每步 1000 万、尝试 12 次、全局每月 20 亿；简单任务实际 10～15 万 token。
6. 工具说明、提示词模板、并发默认值是执行池的身份；改了已有的池起不来。宿主应用不要改这些。
7. 数据目录一次只许一个进程打开（实例锁）。
8. 没有发到 PyPI。

## 十、评估步骤（半天）

1. 对第三节前提表，尤其是有没有 Python 侧进程。
2. 装轮子，跑 `demo.py --scripted`。
3. 配一条 DeepSeek 线路，跑 `demo.py` 真实一局。
4. 列出界面要显示什么（任务列表、步骤进度、等人的卡片、产物），对照接入说明第 4、6、7 节看读接口够不够。
5. 定数据目录放哪、密钥从哪来（只进内存，别落盘）。

## 十一、架构一句话

判断交给模型，库只管约束与秩序：怎么拆任务、下一步做什么、结果好不好由模型判；状态、权限、预算、并发、重试、恢复、审计由库保证。每一步都是"提案 → 验证 → 提交 → 事件记录"，模型的错不会直接变成系统事实。
