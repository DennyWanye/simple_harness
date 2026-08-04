# 原生 Workflow Engine 验收结果

日期：2026-07-11

## 结论

PASS。DeepResearch、PPT Pro、Complex Code 的公共图定义已由 DeskPet 原生内核执行；生产环境、锁文件和冻结包不再包含 LangGraph、LangChain Core 或 LangSmith。

## 自动化证据

- 全部 workflow 测试：`289 passed`。
- 产品入口/PPT/Launcher/Service：`85 passed`。
- 干净依赖后的 bootstrap/import/launcher/service/native：`25 passed`。
- 前端 TypeScript：`tsc -b` PASS。
- Session workflow/Trace focused：`39 passed`。
- 后端全量 `pytest -q` 在 240 秒上限超时，未取得完整结论；专项和产品入口测试均已独立全绿。

## 依赖与冻结包

- `uv sync --extra dev` 后：`langgraph=None`、`langchain_core=None`、`langsmith=None`。
- 锁文件由基线 414 records 降至 405 records；LangGraph/LangChain/LangSmith/ormsgpack 均不存在。
- PyInstaller 构建成功；`verify-frozen-backend.ps1` PASS。
- 冻结目录没有 `langgraph` / `langchain_core`，冻结 exe 成功启动并加载 Provider Registry。

## Windows Computer Use 真测

- Session：`b4a4e202-5ed7-43d7-ad50-1eab85de2f43`
- Run：`33a8298c2d2a4cc89e477481702d74b7`
- 进度：3/12 -> 4/12 -> 6/12 waiting -> 8/12 -> 11/12 -> 12/12 completed。
- 大纲卡真实点击“确认生成”，随后从暂停节点继续。
- 产物：`deskpet-ppt-1783776239.pptx`，3,537,245 bytes，2 slides。
- 完全重启后，从历史 Session 恢复同一张 12/12 完成卡和附件。
- 日志两次记录：`workflow_native_execute engine_kind=deskpet-native run_id=33a8298c... workflow=ppt_pro@v1`。
- 数据库 head：`checkpoint_type=deskpet-native-json-v1`、`engine_kind=deskpet-native`、`snapshot_version=1`。

## 备注

干净同步暴露并修复了运行依赖清单遗漏：`edge-tts` 与 `faster-whisper` 已加入 `pyproject.toml`。同时修复 `rebuild-backend.ps1` 未透传 PyInstaller 失败退出码的问题。
