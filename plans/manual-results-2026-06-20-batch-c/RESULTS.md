# Batch C 真机测试结果（windows-mcp 真模拟点击+输入）

> 对应用例：[`testcase/2026-06-20-agent-loop-batch-c/batch-c-manual-test.md`](../../testcase/2026-06-20-agent-loop-batch-c/batch-c-manual-test.md)
> 执行：2026-06-20，windows-mcp 真机；环境 HARD GATE 通过（Dev python）
> 被测 commit：`4b2d25d`(WI-7)

---

## 结果汇总

| TC | 范围 | 方式 | 判定 | 关键证据 |
|---|---|---|---|---|
| TC-0 | 环境 HARD GATE | 启动日志 | ✅ PASS | `[backend_launch] Dev python=G:/projects/deskpet/backend/.venv/Scripts/python.exe` |
| TC-1 ★ | WI-7 完整问答闭环（弹窗渲染） | windows-mcp 真测 | ✅ PASS | code 模式 agent 真调 `ask_clarification`（日志 `name='ask_clarification' args={"options":["backend/version.txt","README.md"],"question":"要改哪个文件的版本号..."}`）→ **ClarificationDialog 真弹出**（标题"澄清请求"+question 文本+2 个 options 按钮+自由输入框+确认按钮，§16.5 设计完整呈现） |
| TC-2 ★ | WI-7 options 按钮路径（真点击答题） | windows-mcp 真测 | ✅ PASS | **真鼠标点击 "backend/version.txt" 选项按钮** → 弹窗关闭 → agent 收到答案，回复"你选择了：backend/version.txt..." |
| TC-3 ★ | WI-7 防竞态（答题不被 cancel，§13.7 H1） | windows-mcp 真测 + 自动化 | ✅ PASS | 弹窗期 code session 显示 **"running"+停止按钮**（agent task 阻塞挂起）→ 真点击答题后 **agent task 据答继续**（future 经独立 control 通道 resolve、task 未被 cancel）→ 收到选择继续工作 |
| TC-4 | WI-7 超时降级 | 自动化 | ✅ | `test_wi7_clarify.py::test_clarify_timeout` 绿 |

**结论：Batch C（WI-7）完整问答闭环 windows-mcp 真机端到端 PASS。**工具调用→弹窗渲染→真点击答题→答案经独立 control 通道回灌→挂起的 agent task 据答继续（未被 cancel）全链路验证。

---

## 真机观察 + 改进（commit 见下）

**organic 调用倾向**：首次给纯歧义请求（"帮我把那个文件里的版本号改一下"）时，模型**倾向在聊天文字里反问**而非调 ask_clarification 工具。显式指示"请用 ask_clarification 工具问我"后，模型正确调用并弹窗（上述 TC-1~3 PASS）。
**改进**：为让模型 organically 优先用工具（对齐 plan §8"主动反问"意图），在 code persona 澄清步加引导：「关键信息缺失/多候选时优先调 ask_clarification 工具（弹窗等答）而非只文字反问」。WI-3 persona 测试仍绿。

---

## 自动化基线
`test_wi7_clarify.py`(3: 阻塞等待/超时/防 chat-cancel 竞态) 全绿；`tsc --noEmit` exit 0；后端 BC 回归 69 passed。
