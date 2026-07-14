# Plan: PPT Outline Semantics, Session Progress, Full-Page Image Decks

## 主要矛盾

决定成败的核心问题不是增加更多提示文字，而是让 durable workflow 的内部节点状态通过稳定、去重、脱敏的产品事件进入原 session，同时让 PPT image mode 真正以“完整页面位图”为渲染原子。当前 Trace 与 slide image 都已经持久化，但前者没有中间进度投影，后者仍只是背景图并由 python-pptx 叠字。

## 关联验收标准

- AC-19: PPT 大纲决定语义。
- AC-20: Workflow 进度投影到 Session。
- AC-21: PPT 整页生图装配。
- 回归约束: AC-3/4/5/7/9/10/15/16/18。

## 方案选择

- 采用现有 `WorkflowOutbox` 作为进度唯一投递通道，不从 Trace UI 抓日志，也不让各 workflow 直接操作 WebSocket。
- 进度只投影白名单关键节点；原始 prompt、工具参数、文件内容继续只留在 Trace。
- 每个阶段使用由 `run_id + node_id + transition` 派生的稳定 event key，复用 Outbox 的 append-once delivery 与 SessionDB `workflow_event_id` 去重。
- PPT image mode 改为显式 `full_page_images`：图片模型只生成无字视觉底图；生产 compositor 裁掉 provider 标识带，再把已确认文案确定性绘制到最终 16:9 位图；一页 PPT 只放一张全幅图片，不复用旧 `images` 背景图语义。
- 不删除模板模式。生图 provider 不可用时明确提示并走现有模板回退。
- 不承诺整页位图内的元素可编辑；保留 `.pptx`、speaker notes、预览、视觉评测与 Artifact。

## 文件影响清单

| 文件 | 职责 | 本次改动 |
|---|---|---|
| `backend/main.py` | Session UI bridge | 按 accept/modify/reuse/cancel 输出不同确认文案 |
| `tauri-app/src/code-panel/PPTOutlineCard.tsx` | 大纲卡 | 显示具体决定状态，修改提交不冒充“确认” |
| `backend/deskpet/workflows/contracts.py` | Workflow port 契约 | 增加通用 `progress` port |
| `backend/deskpet/workflows/progress.py` | 新增用户进度投影 | 三类 workflow 节点白名单、中文阶段、稳定 Outbox 事件与失败/等待状态 |
| `backend/deskpet/workflows/launcher.py` | accepted-async driver | 为初跑、resume、recover 注入 progress reporter |
| `backend/deskpet/workflows/definition.py` | 通用 node wrapper | 在 observer 同步点调用 progress port，不阻断业务节点 |
| `backend/deskpet/workflows/definitions/ppt_pro_nodes.py` | PPT stage adapter | 构造完整页面 prompt、full-page render mode、问题页重生成 |
| `backend/deskpet/workflows/definitions/v1/ppt_pro.py` | PPT graph | 锁定 modify revision 状态机；视觉修订后回到 image map，只重做问题页 |
| `backend/deskpet/workflows/adapters/ppt_runtime.py` | PPT production operations | full-page mode 调用整页图片装配器 |
| `backend/deskpet/tools/ppt_tools.py` | PPTX primitive | 新增“一张图一页”的 16:9 装配函数 |
| 后端/前端 PPT 与 workflow tests | 回归 | 覆盖语义、进度、幂等、整页图、局部重生 |
| `testcase/2026-07-11-ppt-session-progress-full-page/` | 长期测试 | 自动化与 Windows 真人点击步骤、期望、证据 |

## 任务清单

### Task 1 - 修复大纲决定语义 [AC-19]

- 后端根据 action 选择消息：
  - `modify`: `收到修改意见，正在修改 PPT 大纲。修改完成后会在当前会话展示新版本，请再次确认。`
  - `accept`: `大纲已确认，正在生成 PPT...`
  - `reuse`: `已采用历史大纲，正在生成 PPT...`
  - `cancel`: `已取消本次 PPT 生成。`
- 前端保存提交动作并在卡片 resolved 区显示“已提交修改/已确认生成/已取消”，而非统一“已提交决定”。
- 后端拒绝空 feedback 且保持原 decision/card awaiting；合法 `modify` 必须走 `revise_outline -> 新 outline revision -> 新 decision/card -> waiting`，修改期间 image/render 调用数为 0；cancel 不生成；重复响应仍由 nonce/version CAS 拒绝。
- 验证：后端 wiring + PPT graph revision 状态机测试 + `PPTOutlineCard` vitest。

### Task 2 - 通用 Workflow Session Progress Projector [AC-20]

- 新建 transport-neutral `WorkflowProgressReporter`，由 launcher 持有 Outbox/service，不让 graph core 依赖 SessionDB/WebSocket。
- 为 PPT、DeepResearch、Complex Code 建立用户安全的关键节点白名单与中文阶段顺序；未知/内部节点默认不投影。
- 固定公开映射（未列出的内部节点不投影）：
  - DeepResearch / 7: `normalize` 理解任务；`plan` 规划调研；`search` 搜索资料；`synth` 整理研究结论；`cite` 检查引用；`persist` 保存研究报告；`finalize` 准备交付。
  - PPT Pro / 12 精确 ordinal: `normalize`=1 理解 PPT 需求；`research_plan`=2 规划调研；`research_search`=3 收集资料；`research_synth`=4 整理研究内容；`outline`=5 生成 PPT 大纲；`wait_outline_decision`=6 等待确认大纲；`revise_outline`=6 根据反馈修改大纲；`preflight`=7 检查生成环境；`image_map`=8 生成完整页面；`render`=9 组装 PPT 文件；`preview`=10 生成预览；`visual_evaluate`=11 检查页面质量；`visual_revise`=11 修订问题页面；`publish`=12 发布 PPT。stage key 固定等于上述 node id，修订节点共享 ordinal 但不共享 event identity。
  - Complex Code / 9: `intake` 理解代码任务；`clarify` 确认需求；`plan` 制定执行计划；`wait_approval` 等待计划确认；`llm_proposal` 准备代码修改；`tool_execution` 执行代码修改；`test` 运行测试；`audit` 完成质量审计；`finalize` 准备交付。
- `definition.py` node wrapper 在 node start、waiting、failed、cancelled 调 reporter；进度投影异常只记日志，不改变 workflow 结果。
- launcher 在 launch/resume/recover 构造 context 后注入 reporter，targets 复用原 session message/websocket 目标。
- resume/recover 从持久化 `delivery` session ref 重建 targets，不再退回普通 `workflow_runs.session_id`，确保 epoch guard 不丢弃进度。
- 只在白名单阶段 `started` 时投影正常进度；不把 observer 的 `succeeded_pending` 冒充已完成。waiting/failed/cancelled/final 显式投影。
- 每个稳定事件只持久化一次；attempt 不进入用户事件 identity，WebSocket 重连或 checkpoint resume 不重复刷同一阶段。用户消息只含 workflow 名、阶段文案、序号/总阶段数和稳定状态，不含 trace/span/task/checkpoint id。
- 验证：单元测试遍历三定义并断言公开映射覆盖必需类别、文案不含内部 id；launcher 新实例 `recover_pending`/resume 仍投递到持久 delivery session；三类 workflow 各自至少一个真实 compiled run 将进度持久化原 session；SessionDB history 后同一 WS event 不重复。

### Task 3 - 完整页面 Prompt 与稳定页面效果 [AC-21]

- `stable_slide_records(..., full_page_images=True)` 为每页生成无文字的 16:9 视觉底图 prompt，并把 title/subtitle/bullets/left/right/quote/cite 纳入稳定 pre-hash；speaker notes 不进入 prompt/hash。精确文案由确定性 compositor 绘制，避免图片模型生成乱码。
- full-page mode 对每页都产生 pending image，包括原先没有 `image_prompt` 的页；尺寸使用 image provider 已支持的 `1792x1024`，effect 内归一化为 `1792x1008` 精确 16:9。
- 新 run 显式写 `full_page_images=true` 并得到 `render_mode=full_page_images`；缺少该字段或已持久化 `render_mode=images` 的旧 v1 checkpoint 继续走旧背景图渲染语义，不能误交给新 assembler。直接 `ppt_create(image_full)` 也保持旧行为。
- `prepare_slides_handler` 在创建 full-page effect identity 前通过 `ppt_tools.full_page_image_model_identity()` 解析 configured/default model，写入 record 并显式传给 `PptRuntime.generate_slide_image(model=...)`；runtime 调用 `generate_images(..., model=model)`，不允许 full-page 路径底层再次隐式换模型。`model` 参数保持 optional：旧 `render_mode=images` checkpoint/record 没有 model 时传 `None`，runtime 沿用旧 `generate_images` 隐式解析，绝不把旧背景图 effect 当 full-page effect。
- 保留 stable slide id、pre/post hash 与 effect journal，恢复复用已经生成的页面；pre-hash 纳入实际 image model、full-page prompt schema version、normalizer version、目标尺寸、render mode 和 page revision。committed effect 指向的图片文件若缺失则明确失败，不把失效路径当成功。
- 验证：prompt 精确包含文案且不包含内部 notes；所有页均 pending；hash 对内容变化敏感。

### Task 4 - 一图一页 PPTX 装配 [AC-21]

- 新增 `_normalize_full_page_image()`，在 image effect 提交前处理 EXIF、色彩模式与精确 16:9 派生文件；checkpoint 的 path/post-hash 必须指向该最终页面资产，不能在装配阶段生成未记账裁切文件。
- 新增 `_render_full_page_images()`：空白 16:9 deck，每页验证归一化图片存在并按 slide width/height 全幅插入；不添加标题、正文、页码、页脚或装饰 shape；speaker notes 可保留。
- `PptRuntime.render_ppt()` 在 `full_page_images` 模式直接调用该装配器；装配阶段不再次 probe 或 generate，避免网络瞬时波动丢弃已完成页面；模板路径保持原样。
- 任一页面图缺失时不交付半成品；返回稳定错误或按 provider-unavailable 路径回退模板。
- 验证：EXIF 旋转、RGBA、CMYK、非 16:9 输入均归一化为 1792x1008 且 post-hash 对应派生文件；解压/解析 pptx，页数等于图片数，每页只有一张 picture，其 x/y/cx/cy 精确等于 slide bounds，无文本 shape，speaker notes 保留。

### Task 5 - 视觉问题页局部重生成 [AC-21]

- 视觉评测在 full-page mode 除 overflow/occlusion 外，还对照每页 expected title/body 检查错字、漏字、额外文字和不可读文字，问题动作统一为重新生成该页。
- `apply_visual_revision` 先按 stable slide id 分组；同一页同一评测轮无论命中几个 issue 只递增一次 page revision。full-page mode 对该页做一次原子失效：按规范化 action code 排序去重、重建 prompt/pre-hash，并清空 `record.image.path/post_hash/error` 与 `record.slide.image_path` 后置为 pending；不得把 evaluator 自由文本直接拼进 prompt/hash。
- graph 的 `visual_revise` 回到 `image_map`；无 pending 页面时自然进入 render。
- 验证：第 N 页 issue 只新增第 N 页 image effect；其他 stable slide output hash 保持不变；最多两轮预算仍生效。
- 兼容验证：旧 `render_mode=images` checkpoint 可恢复且不进入 full-page assembler；legacy `ppt_create` 与模板模式不变。
- 额外恢复用例固定在旧 checkpoint 的 `prepare_slides` 已完成、`image_map` 尚未开始处，断言缺 model 字段仍按 legacy `images` 继续。

### Task 6 - 测试、真机与文档闭环 [AC-18/19/20/21]

- 便宜门：py_compile/tsc、聚焦 pytest/vitest、workflow/PPT 相邻回归。
- 真机：在真实 session 发起 PPT Pro；修改大纲并看到正确文案和新卡；确认后看到调研/生图/渲染/检查等实时进度；最终打开生成 PPT，抽查多页为全幅整页图。另对 DeepResearch、Complex Code 各发起一次短 smoke，确认原 session 至少出现规划/执行/检查类进度。
- 失败边界：任一页面缺失时没有 artifact/final success delivery；committed 文件丢失是恢复错误，只有 provider connectivity/model-unavailable 才允许整套模板回退。
- 更新 testcase/index、ARCHITECTURE、STATUS/PPT.md、STATUS/status.md 与手测报告。

## 可追溯矩阵

| AC | Task | 代码 | 自动化 | 真机 |
|---|---|---|---|---|
| AC-19 | 1 | main + card | wiring + vitest | 修改后文案/新卡 |
| AC-20 | 2 | progress + launcher + wrapper | outbox/launcher/reducer | session 实时阶段 |
| AC-21 | 3/4/5 | nodes + runtime + ppt_tools + graph | pptx structural + recovery | 多页视觉抽查 |
| AC-18 | 6 | 全链 | 全门禁 | Windows Computer Use |

## 执行结果（2026-07-11）

- AC-19/20/21 全部完成并真机通过。
- PPT image-mode Session `71258713-d25f-4065-9e54-5d4640f364c6` 显示 1/12-12/12；DeepResearch Session `11cd2ab9-c449-4cee-8770-cd74567b9fa0` 显示 1/7-7/7；Complex Code Session `code-8syzilm7` 真点击批准后显示 1/9-9/9 并完成。
- DeskPet 真实交付 `deskpet-ppt-1783724870.pptx`：21 页，每页恰好一张全幅 picture、无文本 shape；完整蒙版与结构报告见 `plans/manual-results-2026-07-11-ppt-session-progress-full-page/`。
- 真测补修 Code 路由路径污染/“创建”漏词、Code Graph provider chain 401；独立审计后补齐完整 provider fallback chain、`ppt_pro` 工具入口到 Durable Graph 的短 deck 页数契约，并移除底图无条件裁底。
- 自动化：后端宽回归 478 passed；最新聚焦 77 passed；前端 74 files / 767 tests；tsc PASS。
