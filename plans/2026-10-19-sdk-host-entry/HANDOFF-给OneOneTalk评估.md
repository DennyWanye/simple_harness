# 交接：Simple Harness SDK 任务编排能不能接到 OneOneTalk 的 Mac 桌面端

- 日期：2026-10-19
- 写给：OneOneTalk 项目的评估会话
- 来源仓库：`/Users/taiwan/PROJECTS/SimplaHarness/simple_harness`（私有，不要推到公开 GitHub）
- 当前版本：SDK `0.13.0.dev20260925+opt.195`（提交 deb9e006c），Host 钉版 097bd443c，已推送 origin main

## 一、一句话

这是一个 Python 库。装一个轮子，写几行代码，你的应用就能把一个任务交给它：它自己做分层规划、调模型执行、独立审阅、交付产物，中间的授权、预算、并发、恢复、审计都由它管。今天晚上已经在三个仓库外的空 Python 项目里只装轮子跑通过真实 DeepSeek 任务。

## 二、它做什么、不做什么

做：

- 建任务（目标 + 成功条件），自动分层规划，步骤由模型执行，每步独立审阅，通过后交付产物。
- 产物在它自己的数据目录里，用 `artifact_read` 读出来；也能发布到你授权的目录。
- 任务中途要人拍板的地方（授权规划、确认要求、批准对外操作）有接口；`auto_mode=True` 时纯内容类的自动放行。
- 预算（token 上限、尝试次数）、并发、超时、崩溃后恢复、事件审计，都在库里。

不做：

- 不管界面。你要自己画任务列表、进度、审批卡片（它有 `missions` / `snapshot` / `events` / `approvals` 等读接口和 `on_write` 回调）。
- 不管模型配置界面。你把 `base_url` / `model` / `api_key` 传进来。
- 这一版只支持 DeepSeek 模型（每个执行池要一个经认证的用量计数器；别的模型起不了执行池）。
- 沙箱只在 macOS 上可用（跑模型写的代码、核对 `file:` / `pytest:` 要求都要沙箱）。别的系统能启动，但不跑代码、不核对要求。

## 三、硬性前提（先对这几条，不符合就不用往下看）

| 前提 | 说明 |
|---|---|
| 你们的桌面端能跑 Python 3.11+ | 这是 Python 库。Simple Harness 自己是 Tauri 外壳 + Python 后台进程，前端经 WebSocket 控制通道调后台。如果 OneOneTalk 没有 Python 侧进程，要加一个。 |
| macOS | 沙箱靠 macOS 的 Seatbelt。 |
| DeepSeek 线路 | 官方端点，或你声明过的转发站（`compatible_hosts`）。 |
| 首次启动能联网 | 宿主第一次启动会按钉死哈希下载 DeepSeek 分词文件（6 MB）到数据目录。也可以自己放好传 `tokenizer_path`。 |
| 依赖 | 装轮子时自动带上 `httpx`、`pytest>=8.3,<9`、`deepseek-recipe==0.1.1`。你们环境里若已有 pytest 9 会冲突。 |

## 四、接入步骤（今晚在空项目验证过）

```bash
pip install /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/vendor/simple_harness_sdk-0.13.0.dev20260925+opt.195-py3-none-any.whl
```

```python
import asyncio
from pathlib import Path
from agent_orchestrator.host import ModelConfig, OrchestrationHost, Principal

async def main():
    host = await OrchestrationHost.open(
        Path("./orchestration-data"),            # 它自己的数据目录（库、账本、工作区、产物）
        principal=Principal("user-1", "我"),      # 谁在用
        model=ModelConfig(
            base_url="https://api.deepseek.com/v1",
            model="deepseek-flash",
            api_key="<密钥>",
            # 经转发站时：compatible_hosts=("转发主机",), response_model_aliases=("回显名",)
        ),
        auto_mode=True,                           # 纯内容类决定自动放行
    )
    try:
        receipt = host.create_mission({
            "goal": "写一份简短的中文说明，介绍三种家庭省电办法，保存为 NOTES.md。",
            "success_criteria": ["file:NOTES.md", "NOTES.md 列出三种办法，每种一句话"],
            "idempotency_key": "notes-1",
        })
        final = await host.run_until_settled(receipt["mission_id"])
        print(final["snapshot"]["mission"]["status"])
        for a in final["snapshot"]["artifacts"]:
            print(a["path"], host.artifact_read(a["id"])["content"])
    finally:
        await host.close()

asyncio.run(main())
```

常驻应用不用 `run_until_settled`，用 `host.start_driver()` 起后台循环，前端轮询或靠 `host.on_write` 回调刷新。

完整说明：`sdk/simple-harness-sdk/docs/orchestration-host.md`（安装、最短程序、入口做了什么、操作一览、要求怎么写、等人的时候、进度、数据目录、常见问题）。
可运行例子：`sdk/simple-harness-sdk/examples/orchestration-host/demo.py`，`--scripted` 不联网也能跑通。
参照实现（一个真实桌面端怎么接的）：`backend/deskpet/orchestration/wiring.py`、`service_domains/core.py`——Simple Harness 桌面端自己也是用这同一个入口。

## 五、接口一览（`OrchestrationHost`）

- 生命周期：`open` / `start` / `start_driver` / `run_forever` / `run_until_settled` / `close`；`status()`。
- 建任务：`create_mission`、`create_mission_with_sources`（附参考资料）、`source_command`（换资料）。
- 人工环节：`pending_planning_authorizations` / `planning_authorization`、`answer_planning_question`、`approve_operation_completion_spec`、`amend_requirements`、`submit_operation_intent` / `operation_intent_status`、`decide`、`takeover`、`resolve_unknown`、`comment`、`cancel`。
- 读：`missions`、`snapshot`、`events`、`approvals`、`artifact_read`、`method_library`、`global_budget_status`、`context_profiles`。
- 边界：只许从 `agent_orchestrator/public_api.py` 列的公开模块导入。

## 六、今晚的证据（可以复核）

| 项 | 结果 | 在哪 |
|---|---|---|
| 仓库外空项目只装轮子，真实 DeepSeek 一局 | COMPLETED，13 万 token，约 2 分钟 | `~/sh-ext-accept3/`（含自动下载的分词文件） |
| 同上，脚本模型（不联网） | COMPLETED | `~/sh-ext-accept2/` |
| Simple Harness 桌面端真机一局（同一入口） | COMPLETED | `.local-test-evidence/2026-10-19/host-entry-real-click/` |
| 独立核验（只读子代理，五轮） | 全部 PASS，无阻断 | `plans/2026-10-19-sdk-host-entry/独立核验-opt194.md` |
| SDK 宿主测试 / mypy / 公开边界 | 通过 | `sdk/simple-harness-sdk/tests/orchestrator/host/` |

## 七、已知的坑与限制（评估时要看）

1. **线路会坑人。** 今晚一条第三方 DeepSeek 转发站把 `system` 消息丢了，规划器看不到格式说明，整局失败。判法：把请求原样直发，看 `prompt_tokens` 是否接近真实长度。换线路后正常。
2. 只支持 DeepSeek；思考模式默认开（可关）。
3. pytest `<9` 的上限随 SDK 压给宿主应用。
4. Windows 上装得上，但 DeepSeek 计数配方没有 Windows 轮子，起不了执行池。
5. 一个任务的 token 预算默认 2000 万、每步 1000 万、尝试 12 次；一局简单任务实际花 10～15 万 token。
6. 没有发到 PyPI，轮子在 `backend/vendor/`；换版本要重装。
7. 工具说明、提示词模板、并发默认值是执行池的身份，改了已有的池起不来。宿主应用不要碰这些。

## 八、建议的评估步骤（半天）

1. 对第三节前提表。Python 侧进程有没有、能不能起。
2. 在 OneOneTalk 的 Python 环境里装轮子，跑 `demo.py --scripted`。
3. 配一条 DeepSeek 线路，跑 `demo.py` 真实一局（环境变量 `SH_BASEURL` / `SH_APIKEY` / `SH_MODEL`，经转发站再加 `SH_COMPATIBLE_HOSTS`）。
4. 想清楚界面要显示什么：任务列表、步骤进度、等人的卡片、产物。对照 `docs/orchestration-host.md` 第 4、6、7 节看读接口够不够。
5. 想清楚数据目录放哪、密钥从哪来（只进内存，别落盘）。

## 九、架构一句话（为什么它值得接）

判断交给模型，库只管约束与秩序：怎么拆任务、下一步做什么、结果好不好由模型判；状态、权限、预算、并发、重试、恢复、审计由库保证。每一步都是"提案 → 验证 → 提交 → 事件记录"，模型的错不会直接变成系统事实。
