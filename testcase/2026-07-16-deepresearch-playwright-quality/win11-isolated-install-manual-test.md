# Windows 11 隔离安装、离线 Chromium 与清理真人测试

> 状态：**PENDING**
> 平台：当前 Windows 11 x64 真机
> 明确不使用：Windows 10、Hyper-V、VM、ISO、Windows Sandbox、系统重启

## 前置与隔离

- 使用 Gate F 产出的 NSIS 安装包和 hash；不要使用开发 browser cache 证明随包交付。
- 安装目录使用新的短路径，例如 `F:\deskpet-v5-e2e\app`。
- 用户数据目录使用新的短路径，例如 `F:\deskpet-v5-e2e\userdata`。
- 开始前记录 DeskPet、Chromium、Node/Playwright driver 的 PID+create-time 基线。
- 先确认没有手工启动 backend/vite；只启动一套 Tauri 产品栈。
- 断网步骤只做当前进程/适配器的可逆隔离，不安装系统组件，不改 Hyper-V，不重启。

## TC-INSTALL-01 — 隔离安装与资源清单

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 校验安装包 hash、签名/版本、Playwright/Chromium manifest 和 notices。 | hash 与 Gate F 构建证据一致；Playwright 精确版本、revision、browser executable hash 相互匹配；许可证随包。 |
| 2 | 在新的隔离安装目录运行 NSIS 安装。 | 安装成功，不请求 Hyper-V/虚拟化/Windows 功能，不要求系统重启。 |
| 3 | 检查安装目录资源。 | PyInstaller backend、Playwright driver、完整 Chromium new-headless 和所需资源均存在；无依赖用户全局 cache 的路径。 |
| 4 | 记录安装体积与文件清单 hash。 | 体积、文件数、browser hash 可审计且与构建 manifest 对应。 |

## TC-INSTALL-02 — 断网首启与动态页

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 临时断开外网或用可逆规则拒绝 Playwright CDN，保持本地 fixture 可达。 | 主机无需重启；本地 fixture 仍可访问，外网/CDN 不可访问。 |
| 2 | 以隔离用户数据目录首次启动已安装 DeskPet。 | 应用与 backend 正常启动，不弹浏览器下载提示，不访问 Playwright CDN。 |
| 3 | 通过产品真实链路请求固定动态页。 | static 抽取判为空壳后 bundled Playwright 渲染出预期正文；诊断报告 bundled version/revision/path/hash。 |
| 4 | 请求普通静态页。 | static 路径成功且不启动新的 Playwright page/browser 工作。 |
| 5 | 退出应用并恢复网络。 | app/backend/browser/driver 在有界时间内退出；无本次 owned orphan。 |

## TC-INSTALL-03 — 更新保留与旧历史恢复

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 在隔离用户数据创建 v4 历史 fixture 和一个 v5 checkpoint。 | 两类数据均落入隔离目录，不污染常用 DeskPet 用户数据。 |
| 2 | 安装本轮 updater/更新包。 | 更新成功，无 Hyper-V/重启要求；Chromium/Playwright contract 仍匹配。 |
| 3 | 启动更新后的应用并加载历史。 | v1～v4 历史可读，v5 checkpoint 可恢复；旧 run 不被新节点误执行。 |

## TC-INSTALL-04 — 卸载与 owned-process 清理

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 启动一次动态页任务，在运行中正常取消后退出应用。 | 当前 page/context 关闭，取消不丢已提交证据。 |
| 2 | 运行卸载器。 | 安装目录产品文件被移除；卸载不要求重启；隔离 userdata 的保留/删除行为与产品提示一致。 |
| 3 | 对比 PID+create-time 与进程树。 | 本次应用拥有的 Chromium/driver/backend 均不存在；同期无关浏览器进程未被误杀。 |
| 4 | 检查系统状态。 | 未启用/修改 Hyper-V、Windows Sandbox 或虚拟机功能；没有待重启要求由本测试产生。 |

## 结果

`TC-INSTALL-01..04`：**PENDING**。执行时保存安装截图、资源清单、网络/CDN
审计、动态页截图、更新/卸载结果和 PID+create-time 对比，链接到 `RESULTS.md`。
