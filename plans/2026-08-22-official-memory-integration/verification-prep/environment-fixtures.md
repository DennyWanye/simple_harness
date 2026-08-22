# Fixture 与环境准备方案（black-box）

本文件只定义测试需要的外部能力和安全边界，不规定私有实现方式。准备脚本由实现轨完成后按这些
可观察契约落地；脚本必须默认 dry-run/describe，不能访问真实用户数据。

## 1. 目录与进程隔离

- 每个 run 使用 `.local-test-evidence/2026-08-22/<run-id>/` 保存原始证据。
- 每个 UI root 使用独立、带 marker 的 user-data 目录；清理只接受该 marker 下的绝对路径。
- 备份/恢复、migration 与 corruption fixture 使用独立临时目录，绝不复制或覆盖真实用户库。
- 启动前确认目标端口无残留；只启动一个 Tauri，由 Tauri 管理唯一 Vite/backend。
- UI 结束后仅停止本 run 的 App-owned processes；保留证据，清理测试数据前核对 marker。

## 2. 身份与数据种子

- 生成稳定但非真实的 deployment A/B、household A/B、actor A1/A2/B1、session 各一组。
- canary 必须含 run-id，分别种入 personal、family、恶意 Memory、日志敏感扫描、跨 household 数据。
- `LegacyIdentityMap` fixture 包含：唯一映射、缺失映射、歧义映射三类；后两类必须 fail closed。
- 大数据 fixture 只通过公开批量/导入接口创建，记录规模、identity/scope 与内容 digest，不记录正文到 Git。

## 3. Fault fixture 外部契约

DEV 隔离环境提供一次性、可复位的故障控制面，至少支持：

- next recall timeout/transient/corruption/invalid bounded result；
- next release transient；
- next record transient、apply 后确认前断开；
- terminal/outbox/record 三类 crash window；
- backup corruption、lineage drift、second active writer；
- fault state 可查询但只返回计数与 stable code，不返回 Memory 内容。

每个 fault 必须有 unique fault-id、armed/consumed/cleared receipt。清理故障后必须再跑一个正常 Turn，
防止把仍处于 fault 模式的环境误当恢复成功。

## 4. Provider / Tool / Artifact

- SH-M1～M6 使用真实 Provider；凭据由操作者在可见 UI 输入，截图与日志不得包含凭据。
- PPT 使用无隐私的固定标题 fixture，输出目录位于隔离 user-data；ArtifactCard 必须真打开/预览。
- 不把 fake Provider 的脚本结果作为 positive-value root evidence；确定性 doubles 仅用于 SDK 自动化故障门。

## 5. 冷路径与恢复

- SH-M5：从未启动过的隔离 user-data → 首次登录/初始化 → 首个 Memory Turn → 全进程退出 → 同目录重启。
- SDK-R01：clean Python 3.11/3.12/3.13 环境，禁用 editable/path imports，记录 wheel origin 与 SHA。
- SDK-M01：从隔离 v3 fixture 复制出输入，执行 backup-first coordinator；每个 crash phase 使用独立副本。

## 6. 测后核对

- 输出：run/scenario IDs、命令/动作、exit、脱敏摘要、相对证据索引、逐文件 SHA-256。
- 隐私扫描：password、API key、cookie、Authorization、token、Memory 原文 canary、embedding、绝对用户路径。
- Git 只保存小型文本结论与 hash；截图、日志、DB、录屏不进入 Git。
