---
id: TC-PS-01
purpose: Verify Git-root preview, explicit child registration, folder registration, deduplication, and path identity
status: superseded
surface: desktop-ui
type: hybrid
obligations:
  - TO-A1
  - TO-R5
tags:
  - project
  - registration
  - path-identity
entrypoint: project picker
revision: 2
replacement: TC-HM-09
---

# TC-PS-01 — 项目注册与路径身份

## 前置

- 使用隔离 user-data 启动当前候选构建。
- fixture 含一个 Git 仓库（内有两级子目录）、一个非 Git 目录、指向该 Git 仓库的符号链接，以及一个不同目录。
- 本轮目标平台是 macOS。Windows identity probe 保留为后续工作，不属于本 revision 的通过条件（用户 2026-08-25 确认暂不考虑 Windows）。

## 步骤与预期

| 步骤 | 操作 | 预期结果 |
|---:|---|---|
| 1 | 从可见 UI 打开“添加项目”，用系统文件夹选择器选择 Git 仓库内的两级子目录。 | 确认前显示最终 `project_root` 预览为 Git 仓库根；此时尚未出现新 Project 或 Session。 |
| 2 | 保持默认 Git 根模式确认注册。 | 只出现一个 Project 分组；显示路径为预览的 Git 根，未创建指向子目录的第二个 Project。 |
| 3 | 再次选择同一子目录，这次显式选择“将所选文件夹注册为独立项目”并确认。 | 新 Project 的 `project_root` 为所选子目录；它与仓库根 Project 分离。 |
| 4 | 选择非 Git 目录并确认。 | 预览与注册后的 `project_root` 都是所选非 Git 目录。 |
| 5 | 依次通过 `..` 等价写法、符号链接和平台支持的大小写/分隔符等价表示重复注册仓库根。 | 每次返回既有 Project；项目总数、分组与身份不增加，显示路径保持规范化。 |
| 6 | 尝试空路径、文件而非目录、已删除目录和无权限目录。 | 每次均显示明确错误；Project/Session 数量不变，没有半创建分组。 |
| 7 | 在 macOS 对同卷 rename 前后路径执行 identity probe；再对不同目录执行 probe。 | 同一目录 rename 前后 identity 相同；不同目录 identity 不同。 |

## 通过条件与证据

- 步骤 1～7 全部满足即通过；Windows 结果不计入本轮判定。
- UI primary：选择器、预览、确认结果、错误态截图；每个动作前记录坐标/动作/期望。
- runtime primary：每次注册的 request/session/project correlation、规范化结果和项目计数；不得记录不透明 filesystem identity 原值或凭据。
