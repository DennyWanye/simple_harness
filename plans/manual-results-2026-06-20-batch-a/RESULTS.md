# Batch A 真机测试结果（windows-mcp 真模拟点击+输入）

> 对应用例：[`testcase/2026-06-20-agent-loop-batch-a/batch-a-manual-test.md`](../../testcase/2026-06-20-agent-loop-batch-a/batch-a-manual-test.md)
> 执行：2026-06-20，windows-mcp 真机模拟鼠标点击 + Clipboard 中文粘贴输入
> 环境：Tauri dev 注入 `DESKPET_BACKEND_DIR=G:/projects/deskpet/backend` + `DESKPET_PYTHON=<worktree venv>`
> 被测 commit：`6eb55f4`(Batch A) + `dff43d0`(B5测试) + `98b56a9`(真机修复)

---

## 结果汇总

| TC | 范围 | 方式 | 判定 | 关键证据 |
|---|---|---|---|---|
| TC-0 | 环境 HARD GATE | 启动日志 | ✅ PASS | `[backend_launch] Dev python=G:/projects/deskpet/backend/.venv/Scripts/python.exe backend_dir=G:/projects/deskpet/backend`（非 Bundled exe） |
| TC-1 | BC 主路 chat + 工具调用 | windows-mcp 真测 | ✅ PASS | 真点击输入框+粘贴"生成PPT大纲"→桌宠经 `chat_stream_with_tools`(WI-1改的方法)→relay 200 OK→正常回复《季度工作总结》大纲；code 任务真调 `run_shell/read_file/write_file/todo_write` 全部成功派发 |
| TC-2 ★ | WI-3 code 收尾自查 | windows-mcp 真测 | ✅ PASS | code 模式真发"建 batcha_hello.txt 写hi确认后删"→TODOS 5/5 全✓(含"读取确认内容"+"确认已不存在"两个**验证**步)→收尾回复=自查清单结构(列核心需求→逐项已完成→给验证证据"已读取验证内容为hi/最终检查验证已不存在"→汇报结果)。**没空口"做完了"** |
| TC-3 | companion 无自查(BC负向) | windows-mcp 真测 | ✅ PASS | companion 聊天 PPT/闲聊回复为自然口语，**无** code 模式逐项打勾+验证证据式工程自查清单 |
| TC-4 | WI-2 trace 生成 | windows-mcp 真测驱动+文件 | ✅ PASS | 开 `[agent] iteration_trace_enabled=true` 重启→真点击发"生成PPT"→`<user_data>/traces/6f568ff1-….jsonl` 生成，含 `iter_start/llm_out/tool_result/gate/end` |
| TC-5 | WI-2 trace 完整性 | 文件验证 | ✅ PASS | jsonl 逐行 `json.loads` **bad_json=0**(全合法);`ppt_create` 工具 args **完整未截断**(完整 title/outline 数组) |
| TC-6 | WI-2 flag off 无 trace(BC) | 文件验证 | ✅ PASS | 默认 config([agent] 无 iteration_trace_enabled)→跑 3 轮对话(PPT/code×2)→**无 traces 目录** |
| TC-7 | WI-1 flag off(BC) | 自动化覆盖 | ✅ | `test_force_finish_flag_off_never_forces_none` 绿(纯 BC 切换,真机行为字节一致) |
| TC-8 | tier3/verify 强制收尾深层 | 自动化覆盖 | ✅ | `test_wi1_tool_choice.py` 三路径强制 none + verify_exhausted 末轮 9/9 绿 |

**结论：Batch A 全部用例 PASS。核心 user-facing 功能(TC-0~TC-6)全部 windows-mcp 真机模拟点击+输入验证通过。**

---

## 真机测试抓到并修复的 2 个真 bug（真机测试的价值体现）

1. **WI-2 flag 命名碰撞**（commit `98b56a9`）：`[agent].trace_enabled` 与已有 `[context.assembler].trace_enabled`(Context Trace UI/P4-S11) 同名易混。→ WI-2 改用 `iteration_trace_enabled`，真机验证 trace 正常生成。
2. **build_agent cfg.raw BC 回归**（commit `98b56a9`）：codex 的 WI-1/2 改动让 build_agent 无条件 `cfg.raw.get(...)`，破坏老测试 `_CfgStub`(无 .raw 属性)→8 个 `test_build_agent_verify_wiring` 失败。→ 改 `getattr(cfg,"raw",None)` 安全取，回归 74 passed。

> 这两个 bug 单测/协议层都没暴露(单测 mock 不走真 config；codex 自跑的窄 BC 集没含 test_build_agent_verify_wiring)，是真机测试 + Lead 全量回归才揪出的。

---

## 自动化基线（同被测代码）
- `test_wi1_tool_choice.py` 9 passed / `test_wi2_trace.py` 2 passed / `test_wi3_persona.py` 2 passed
- BC 回归(agent loop/gate/ctx/provider chain/verify/compaction/build_agent wiring) 全绿
