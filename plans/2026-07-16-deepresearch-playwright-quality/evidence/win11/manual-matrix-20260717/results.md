# 2026-07-17 多领域真人 UI 矩阵

平台：Windows 11 x64；最终 NSIS 隔离安装 `F:\deskpet-v5-matrix\app`。
范围：真实坐标输入、真实产品路由、workflow DB/snapshot 与终态 UI 交叉核验。
未使用：Hyper-V、VM、ISO、Windows Sandbox、系统重启。

## 质量修复后的源码复验（当前事实）

旧隔离安装矩阵定位出跨领域证据全拒、AI profile、路由、安全投影和 elapsed 缺陷。修复后通过真实 DeskPet UI、源码 backend 与同一 durable DB 重新执行三类代表性问题：

| 类别 | run_id | DB / UI elapsed | 核心覆盖 | 有效/第一方 | 质量 | 终态 |
|---|---|---:|---:|---:|---:|---|
| AI Top 10 | `9670a357717b4ea9b8e6c5aef96c4b84` | 141.705s / 140s | 2 完整 + 1 部分 / 3 | 5 / 0 | 75 | `partial` |
| 国家统计局人口指标 | `517810633ee742399f1bc7622ab6d7ec` | 211.069s / 209s | 4 / 4 | 11 / 5 | 80 | `completed` |
| 手机产品比较 | `2b79cd8804454c33a36e0fc30bf9c0da` | 248.888s / 247s | 1 完整 + 4 部分 + 1 未覆盖 / 6 | 9 / 1 | 60 | `partial` |

- 启动日志：`gate-f-source-14.stdout.log` / `gate-f-source-14.stderr.log`，确认 Tauri 使用 `F:\projects\deskpet\backend` 源码 backend 自管启动。
- UI 终态显示安全聚合计数、质量和三态；未显示 raw query 或 URL。
- 统计题查询被压缩为短的 `site:stats.gov.cn` 维度探针，真实访问国家统计局页面并接纳 5 个第一方来源。

## 立即生成真实 UI 复验

- Computer Use Session `430bd129-9a45-4e8b-bb31-4813dd299dde` 创建源码 v5 root `6ea6a3a449c047e98750a84276760bb2`，真实点击“立即用现有证据生成”后按钮立即显示 disabled“已接受”。
- command `be61cf76...` accepted→observed 约 0.227 秒；30 秒 settle fence 后 settled/consumed，run `status=completed`、`error_json=null`，所有节点 succeeded，最终 `insufficient_evidence`。
- UI 终态显示“已完成”“准备交付”“硬失败 0”“证据不足”，并提供“继续补充调研”；长 fetch 的控制性取消被投影为降级完成而非永久节点失败。
- 自动化：控制/adapter 聚焦 `50 passed`；v5/control/terminal 联合 `226 passed`。源码启动日志：`../gate-f-source-20.stdout.log` / `../gate-f-source-20.stderr.log`。
- 单次 accept→observe→settle→consume 主链 PASS；重复点击、WebSocket 重连与 accepted 窗口强杀恢复仍 PENDING。

以下内容保留为修复前隔离安装失败基线。

## 计数

- distinct questions：5
- UI submissions：6
- v5 root runs：5
- routing retries：1
- continuations：0
- completed workflows：5
- delivery：`insufficient_evidence` 5/5

## 运行结果

| 类别 | 问题摘要 | run_id | DB duration | UI elapsed | IO docs | query fingerprints | passage refs | 终态 |
|---|---|---|---:|---:|---:|---:|---:|---|
| AI | 最新最有价值的 AI 技术信息 | `292f65a8472d4dfcbb3bc3a21c6fa6ac` | 70.715s | 139s | 21 | 20 | 0 | insufficient |
| 消费 | iPhone 17 Pro vs 小米 15 Ultra | `561b3b70943a4352b32b8c683e556eec` | 91.594s | 181s | 14 | 20 | 0 | insufficient |
| 政策 | 2026 新能源汽车以旧换新 | `9d7d88c9c1a24ee3a92ba4f75fbc0d06` | 64.811s | 126s | 20 | 20 | 0 | insufficient |
| 冷门 | 县域小学离线 AI 作业批改 | `25e85ddc824947e8b15bc00dc66f9813` | 81.707s | 160s | 23 | 24 | 0 | insufficient |
| 高证据 | 国家统计局 2024 人口统计 | `2295f73780b941eea02cf8b091048f10` | 74.636s | 146s | 23 | 20 | 0 | insufficient |

五个 snapshot 的全部维度均出现 `winning_relevance_below_threshold`，总计抓取 101 个 documents，但没有任何 admitted passage。

## 路由变体

首次输入“请基于国家统计局官方资料，调研……”没有创建 workflow run，而是进入普通 `web_search → web_fetch`；权限等待超时后显示 `permission denied (source=timeout)`。改写为“请进行深度调研：……”后创建 v5 root `2295f737...`。该重试不计为新问题。

## UI 证据

以下五张截图均从同一隔离安装实例的真实消息历史重新定位并捕获；SHA-256 已核验为五个不同文件。

- [AI 技术情报终态](./01-ai-insufficient.png)
- [产品比较终态](./02-product-insufficient.png)
- [新能源政策终态](./03-policy-insufficient.png)
- [冷门低证据终态](./04-niche-insufficient.png)
- [统计题路由、raw URL 与 v5 终态](./05-stats-routing-and-zero-evidence.png)

## 判定

- PASS：真实 v5 root、durable 终态、fail-closed、continue capability。
- FAIL：跨领域 evidence admission、高证据锚点、AI profile、调研意图路由、raw URL 安全投影、elapsed 指标。
- 结论（修复前）：Gate F 质量验收 BLOCKED；安装/Playwright packaging 证据仍有效。当前结论以顶部源码复验为准。
