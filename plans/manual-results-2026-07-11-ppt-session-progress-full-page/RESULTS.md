# 手工验收结果

## 结论

PASS（核心 AC-19/20/21）。PPT 大纲修改语义、三类长任务 Session 进度和 image-mode 整页图片 PPT 均完成真实 Windows 点击验证。短 deck 页数契约在真测后补修，并已通过工具入口与 Durable Graph 跨层自动化；尚未再次跑完整生图 E2E。

## 真实 Session / Run

| 场景 | Session / Run | 结果 |
|---|---|---|
| PPT 修改大纲 | `ab69a842-b964-40fa-b720-218c1fca04b4` | 修改提示正确，新大纲卡出现；修改期未生图 |
| PPT 模板模式进度 | `897028cd-1fd2-48ed-8c49-3390d928f23e` | 1/12-12/12 与 Artifact 出现在同一 Session |
| DeepResearch | `11cd2ab9-c449-4cee-8770-cd74567b9fa0` | 1/7-7/7 完整可见 |
| Complex Code | `code-8syzilm7` / `e3ff6a6307114554a7f588cba6952ba2` | 真点击批准后 1/9-9/9、完成总结与文件均进入普通 Session；Run completed |
| PPT image mode | `71258713-d25f-4065-9e54-5d4640f364c6` / `a68bf00144d143efa98b552d3cba14d1` | 真点击确认大纲，7/12-12/12、视觉修订与 Artifact 完整交付；Run completed |

## 最终 PPT 证据

- DeskPet 真实交付：`C:\Users\Administrator\AppData\Roaming\deskpet\OutPut\PPT\deskpet-ppt-1783724870.pptx`
- SHA-256：`c4e7181c1f6ab0907d5de0e071a4a66f594a0ab82ffe882d646b69ccff0ac16f`
- 结构报告：`live-image-mode-pages/structure-report.json`
- 全页蒙版：`live-image-mode-montage.png`
- 结构：21 页；每页恰好 1 个 picture、0 个文本 shape、picture 精确铺满 slide bounds；嵌入图片均为 1792x1008。
- 视觉：逐页蒙版与首尾页原图检查无底部水印、黑色修补块或文字裁切。

## 真机发现并修复

1. Code 路由遗漏“创建”，且文件路径中的 `ppt` 会抢占代码意图。现先剥离路径再做领域识别，并补齐“创建”。
2. Code Graph 在 per-session provider chain 解析前使用旧 provider，审批后 `llm_proposal` 401。现复用 provider registry、session binding 与 keychain，并把完整有序 provider chain 交给 Durable Graph；首项失败会继续下一项。最终 Run completed。
3. Code 项目基础 Session 未注册/被过滤。现仅按 `code_sessions.base_session_id` 白名单进入普通消息列表。
4. PPT provider 图片底部处理曾无条件裁掉 120px，可能误伤正常内容。现不再裁切；底图先归一化到 1792x1008，再做确定性中文合成，像素测试确认未被面板覆盖的底部内容保持不变。
5. `ppt_pro` schema 与 Durable Graph 原分别限制最少 3 页，短 deck 请求无法贯通。现两层均允许 1-20 页并保留用户显式页数；Graph normalize 的 1/2 页测试已覆盖。该修复发生在 21 页真机运行之后，尚未补跑短 deck 生图 E2E。

## 自动化

- 后端宽回归：`478 passed, 3589 deselected`（workflow/PPT/SessionDB/Code Mode）。
- 最新聚焦回归：`77 passed`。
- 前端全套：`74` test files、`767` tests（本轮后续无前端改动）。
- TypeScript：`tsc --noEmit` PASS。

## 启动证据

`tauri-final7.err.log` 确认：

- `[backend_launch] Dev python=F:\projects\deskpet\backend\.venv\Scripts\python.exe backend_dir=F:\projects\deskpet\backend`
- `config_loaded ... user_data_dir='C:\Users\Administrator\AppData\Roaming\deskpet'`
- `code_sessions_restored count=1`
- `Uvicorn running on http://127.0.0.1:8100`
