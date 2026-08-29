# TC-GS-10 执行结果 — 2026-08-29

- 状态：PASS（macOS 当前源码 debug bundle；Windows 不在本轮范围）
- 绑定：TO-A9、TO-A10、TO-R3、TO-R6
- SDK：Harness 0.6.2 / Memory 0.5.2 / Service 0.3.12
- Provider：`deepseek-v4-flash`
- 权限：`authorization_mode=auto`

## 结果

1. Chat 使用未固定 ref 的 GitHub URL，真实触发 GitHub REST 403；SDK Run 在约 41 秒内进入 terminal，UI 显示结构化失败，不再无限“安装中”。
2. 使用完整 commit `3a094db39db558dc72127938a377dccd8463c475` 后直接请求 codeload，安装 `plan-bs/plan-task/plan-test` 成功。
3. 统一能力中心能力数 127→130，搜索 `plan-` 显示三项健康 Skill；兼容 Skill Store 已安装页显示同三项。
4. 第二个普通 Session 输入 `/plan-` 显示三项命令，证明全局 catalog 不是单 Session 前端缓存。
5. 自动化覆盖稳定 failure identity、显式 retry generation、auto approval receipt、versioned status/cancel ack、no-ack 不假 idle、catalog user scope 和 exact commit API bypass。

## 证据

- 原始 UI 截图与运行日志：`.local-test-evidence/2026-08-29/auto-skill-install-current-build/`（ignored）
- 后端相关回归：175 passed，1 个既有 unknown timeout mark warning
- 前端相关回归：101 passed
- TypeScript/Vite/Tauri debug build：PASS
