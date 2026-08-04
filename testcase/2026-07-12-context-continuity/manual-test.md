# DeskPet 上下文连续性与图片误触发测试

## TC-1：原始脉冲步枪复现

1. 通过源码启动路径启动 DeskPet，确认 Tauri 使用 `backend/.venv/Scripts/python.exe main.py`。
   - 预期：源码 backend 启动，桌宠显示“已连接”。
2. 真点击“消息”与输入框，输入：`你能帮我调研一下，命运2 高阶暴君这个武器最好的词条是什么呢？`，点击发送。
   - 预期：回复围绕命运2武器识别/词条，不出现图片生成卡片。
3. 真点击输入框，输入：`是一个脉冲步枪`，点击发送。
   - 预期：回复承接前文，明确按脉冲步枪继续识别；不得声称已生成图片，不得调用 `generate_image`。
4. 检查 `state.db` 最近消息及本次 receipt。
   - 预期：第二轮 user/assistant 连续；assistant 无 tool_calls；本次无 generate_image receipt。
5. 检查 Tauri stderr。
   - 预期：即使出现 `memory_manager.l3_timed_out`，L2 上下文仍生效，回复不漂移。

## TC-2：工具意图边界（自动化）

- `是一个脉冲步枪`、`这幅画是什么`、`画质怎么样`：过滤 `generate_image`。
- `画只猫`、`请帮我画只猫`、`请生成一个头像`：保留 `generate_image`。
- accepted/pending 图片 receipt：不得满足“已经生成”完成声明。
- terminal success 图片 receipt：允许满足完成声明。

## TC-3：搜索型自然承接

1. 在同一 session 先讨论一个名称不确定的对象。
2. 输入：`你能自己去查一下吗？中文网站里面之类的，应该都有的，我也不知道英文叫什么`。
   - 预期：`web_search` bundle 保留至少覆盖最近 3～4 个完整问答的连续 L2，模型能识别上文对象，不得反问“它是什么”；必须实际出现 `web_search` 工具结果，查询词包含“高阶暴君/命运2/脉冲步枪”，不得声称没有联网搜索工具。
3. 日志应显示该轮 `task_type=web_search` 且 `l2_count_in` 足以越过紧邻的错误 assistant，恢复原始对象。
