# TaskGraph 23 最终 Host UI2 源码增量

这些是最终候选相对捕获 HTN Host 基线的9个业务源码文件，不是原始测试证据。没有复制候选 venv、数据库、日志或旧 wheel。

当前根 Host 仍固定 HTN wheel；本目录未自动安装。继续接入前读仓库根 `HANDOFF-2026-09-23.md`，按 `SOURCE-MANIFEST.json` 的 base_sha256/current sha256 逐文件三方核对。不可直接覆盖另一台电脑的后续改动。原补丁/真实验收边界见 `plans/TaskGraph/V1.2.1/`。

后端新增 `taskgraph.py`，handlers/service/hierarchical 负责入口与启动装配，SDK candidate verifier 需与新 package identity 对齐；前端含 store、图组件/CSS 与 MissionsView 接线。部署新 SDK 后再激活这些入口，保留 startup_assembly 在恢复前的顺序。
