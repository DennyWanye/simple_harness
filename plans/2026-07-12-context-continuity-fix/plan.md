# Plan：上下文连续性与图片误触发修复

## 主要矛盾

当前 session 的连续对话不是一个独立、可靠、可验证的上下文基线：它会被跨 session L3 的延迟连带丢弃，还可能读取会话开头并重复当前消息。短澄清轮随后面对过宽工具面，容易被模型错误补全。

## 关联验收标准

覆盖 AC-1 至 AC-7，详见同目录 `acceptance.md`。

## 任务

### Task 1 — 修复连续历史读取与当前轮去重（AC-1/2）

- 修改 `backend/deskpet/memory/session_db.py::get_messages`：倒序取最近 N 条，再恢复正序；保持 offset 语义明确。
- 扩展 `ContextAssembler.assemble` 与 `ComponentContext` 传递精确 `current_message_id`，禁止文本去重。
- `backend/main.py::_run_chat` 将 `_user_msg_id` 传入 assemble，最终 current user 只追加一次。
- 新增独立 newest-tail API，保留旧 oldest-first 分页契约；窗口切到 tool result 时向前扩展到 assistant tool-call 边界。

### Task 2 — 隔离 L2 与 L3 失败域（AC-3）

- 修改 `MemoryManager.recall`，L3 使用剩余 deadline 的有界任务，超时仅返回空 L3；topic-shift embedding 也使用独立剩余预算。
- 不再让 `MemoryManager.recall` 中的慢 L3 阻塞已完成 L2。保留 L3 session affinity 和动态 memory block。

### Task 3 — 收紧短澄清轮工具面（AC-4/5）

- 在 ToolComponent 增加基于当前文本的显式图片意图判定。
- 对依赖前文的短澄清轮，从 wildcard 工具面剔除 `generate_image`；明确生图请求保持可用。
- 通过 meta 记录过滤原因，供 Context Trace/日志诊断。

### Task 4 — 阻止无证据的图片完成声明（AC-6）

- 扩展 VerifyGate 图片完成声明 pattern，只有 `generate_image` 的可信成功 receipt 才匹配。
- `generate_image` accepted_async 只允许“已开始/后台生成”，不能满足“已生成”；最终完成必须来自 worker 终态产物通知。
- 对未暴露 generate_image 的短澄清轮缓冲流式文本，防止最终 VerifyGate 前泄露假完成声明；重试耗尽时输出诚实说明而非原声明。

## Plan challenge round 1

- VERDICT: FAIL；已关闭：精确 id 透传、topic gate 独立限时、tool-call 边界、accepted_async 与终态区分、流式泄露与耗尽行为均补入计划。

## Plan challenge round 2

- VERDICT: FAIL；已关闭：VerifyGate 排除 accepted/pending receipt；queued image receipt 改为非完成证据；L2 边界按精确 tool_call_id 无界回查；补“画只猫”等显式意图与更广完成声明测试。

## Plan challenge round 3

- 条件 FAIL；已关闭类别化边界：排除“这幅画是什么/画质怎么样”等非生成表达；短澄清轮的无证据完成声明改为对象无关检测；确定性纠正文案不再写死“脉冲步枪”。

## Plan challenge round 4

- VERDICT: FAIL；已关闭组合命令前缀：`请帮我画只猫` 与 `请给我绘制...` 可保留生图工具。

### Task 5 — 自动化、真机与文档闭环（AC-1~7）

- 增加 SessionDB、MemoryComponent、ToolComponent、AgentLoop 聚焦测试。
- 跑相关回归；启动 DeskPet 源码 backend，Windows 真点击复现两轮对话。
- 保存 testcase/results，更新 `testcase/index.md` 和 `STATUS/status.md`。
