# Gate F — Win11-only 发布验收结果

日期：2026-07-17
结论（2026-07-17 质量修复复验后）：**核心检索/建模/交付故障已修复并通过三类真实 UI 复验，TC-UI-NOW 单次立即生成主链 PASS；Gate F 总体仍为 PARTIAL，未执行项继续保持 PENDING。** 安装、离线 Playwright、durable 终态与 fail-closed 证据继续有效。

## 质量修复复验（当前事实，优先于旧失败矩阵）

本轮针对旧矩阵暴露的 0 admitted evidence、AI profile、意图路由、raw URL 投影和 elapsed 翻倍问题完成修复，并使用源码 backend + 真实 DeskPet UI 重启复验。三条代表性 root run 均走 `deep_research/v5`，UI 与 workflow DB 墙钟一致，终态投影未显示 raw query 或 URL。

| 类别 | run_id | DB / UI elapsed | 核心覆盖 | 有效/第一方来源 | 质量 | 终态 |
|---|---|---:|---:|---:|---:|---|
| AI Top 10 与优缺点 | `9670a357717b4ea9b8e6c5aef96c4b84` | 141.705s / 140s | 2 完整 + 1 部分 / 3 | 5 / 0 | 75 | `partial` |
| 国家统计局 2024 人口指标 | `517810633ee742399f1bc7622ab6d7ec` | 211.069s / 209s | 4 / 4 | 11 / 5 | 80 | `completed` |
| iPhone 17 Pro vs 小米 15 Ultra | `2b79cd8804454c33a36e0fc30bf9c0da` | 248.888s / 247s | 1 完整 + 4 部分 + 1 未覆盖 / 6 | 9 / 1 | 60 | `partial` |

关键修复：

1. 建模与检索：AI Top-N 使用技术情报维度；中文统计类型可识别国家统计局约束；query 有 240 字符硬上限，优先使用聚焦目标和维度词。
2. 产品比较：初始探针先做两机型广域发现，再做有界官网补证，不再把 `site:apple.com OR site:mi.com` 作为所有维度的唯一入口。
3. 证据与报告：确定性抽取式报告可在生成式报告失败时保留已接纳证据；partial 阈值基于实际证据，质量修复异常不会摧毁可交付结果。
4. UI/时间：三条 run 的 UI elapsed 与 DB 墙钟误差均小于 2 秒；默认进度卡只显示安全聚合字段。

自动化复验：v5 核心 `254 passed`；DeepResearch/Search Gateway/Playwright 联合回归 `559 passed, 2 timing failures`，两项隔离复跑 `2 passed`；前端 `77 files / 816 tests`；TypeScript、Vite production build、Rust `cargo check` 均通过。

## TC-UI-NOW 立即生成控制复验

- 真实 Computer Use Session `430bd129-9a45-4e8b-bb31-4813dd299dde` 在新 Session 输入国家统计局人口题，创建 v5 root run `6ea6a3a449c047e98750a84276760bb2`，并真实点击可访问性标识为“立即用现有证据生成”的按钮；按钮立即变为 disabled“已接受”。
- durable command `be61cf76a7d3195fb4ab4985c62f3633a04ad583630516f89d8dc518b5451ad3` 从 accepted 到 observed 约 0.227 秒；在 30 秒 settle deadline 后完成 settled 与 consumed，最终 delivery 为 `insufficient_evidence`。
- 长时 fetch 在运行中每 0.5 秒观察 control；到 settle fence 后被控制性取消并投影为业务 `cancelled` stage result，未再误判为节点永久失败。该 run 全部节点为 succeeded，`status=completed`、`error_json=null`；UI 显示“已完成 / 准备交付 / 硬失败 0 / 证据不足”，且提供“继续补充调研”。
- 两个诊断性失败样本保留：`28a0e947...` 暴露 command 未观察，`b3578723...` 暴露已观察 command 在 deadline 被错误投影为永久失败。对应修复覆盖前端 active-control 合并、服务端空 payload checkpoint 解析、在途 effect control polling，以及 settle-fence 取消的业务终态分类。
- 自动化证据：控制/adapter 聚焦 `50 passed`；DeepResearch v5 + control + terminal 联合回归 `226 passed`。源码启动证据为 `evidence/win11/gate-f-source-20.stdout.log` / `gate-f-source-20.stderr.log`，日志确认 Tauri 自管 `F:\projects\deskpet\backend` 源码 backend。

本次 PASS 覆盖单次真实点击的 accept→observe→settle→consume→唯一终态主链；重复点击、WebSocket 重连与 accepted 窗口强杀恢复仍保持 PENDING。

## 旧多领域失败矩阵（回归基线）

- 新增五类真人问题：AI 技术情报、产品比较、近期政策、冷门低证据、国家统计局高证据锚点。
- 真实 UI 共提交 6 次，其中 5 个不同问题创建 5 个独立 v5 root run；统计题另有 1 次意图路由重试，不计为新问题。
- 五个 v5 run 均 completed 且诚实交付 `insufficient_evidence`，但共同表现为质量分 0、核心覆盖 0、有效/第一方来源 0。
- 五个 snapshot 共记录 101 个 IO documents、104 个 query fingerprints，却全部 `passage_blob_refs=0`，所有维度出现 `winning_relevance_below_threshold`；高证据国家统计局题也未接纳任何证据。
- SC-AI-01 实际生成 `generic_*` 维度，没有进入 `technology_intelligence`。
- “请基于官方资料，调研……”漂移到普通 ReAct `web_search/web_fetch`；明确“请进行深度调研”才进入 v5。
- 普通工具路径在消息流显示 raw `web_fetch` 参数与完整 URL；v5 UI elapsed 稳定约为 workflow DB 墙钟耗时 2 倍。
- 详细 run 表、截图与审计见 `evidence/win11/manual-matrix-20260717/results.md`。

该矩阵是本轮修复前的失败基线；当前质量状态以本文件顶部“质量修复复验”为准。

## 范围

- 仅验证当前 Windows 11 x64 主机。
- 未启用、安装或调用 Hyper-V、虚拟机、ISO、Windows Sandbox；未执行需要系统重启的操作。
- Win10 支持未验证，不在本轮声明范围内。

## 自动化与构建

- v5 公平抓取、生产 evidence wiring、Search Gateway budget、identity/loop/progress：`46 passed`。
- 最终相关后端宽回归：`1274 passed, 1 skipped`；全后端套件另遇到既知 vector embedder worker hang，已清理测试进程，未伪报全量绿色。
- 前端：77 files / 816 tests；TypeScript build 与 Vite production build PASS。
- Rust：`cargo check` PASS（仅既有 warning）。
- 最终 frozen backend：1,668,586,900 bytes / 9,068 files；Playwright packaging assertion PASS。

## 安装包与离线浏览器

- 产物：`tauri-app/src-tauri/target/release/bundle/nsis/DeskPet_0.6.0-beta.9_x64-setup.exe`
- 大小：529,333,947 bytes
- SHA-256：`1495E432F4314A9B83991724FDB411B1088FF13DFF1673B02FB6F56014F26A38`
- Playwright：1.61.0；Chromium Headless Shell revision 1228
- 浏览器 executable SHA-256：`28016DF6864D302434C9231E1F9F1A8A7ECC512CB2FE3FAABB2A36130B96BCF1`
- frozen 离线动态 smoke：PASS；process-local deny proxy 记录 `offline_guard=true`，没有修改系统防火墙或网络设置。

## 真实 UI / durable E2E

- 根 run：`e5cb4a8da7a7464ebd6fbfe4be5fcafa`
- 输入：`帮我调研一下，现在中国小学现在的教育现状和国家下一步计划`
- v5 节点完成：normalize、model、plan、expand、search、direct、fetch、score、gap_evaluate、rerank、insufficient_finalize。
- 建模 LLM 返回跨阶段非法对象时，确定性 brief fallback 生效；UI 显示降级完成，workflow 未失败。
- 搜索、抓取 effect 均成功 commit；证据未达到 admission/readiness 门，最终诚实交付 `insufficient_evidence`，没有伪造完整报告。
- 真人点击“继续补充调研”创建 child run `d21259b4-03e5-526d-a7c3-951d7c146dd5`，parent/checkpoint lineage 写入成功。
- 使用同一 user-data 完整重启后，两条终态历史与 continuation 动作恢复；backend health PASS。

## 真测发现并修复

1. modeling stage 对 advisory LLM 非法 schema 未降级：现捕获 contract validation error 并使用 deterministic brief。
2. Search Gateway durable outcome 泄漏 tuple：现使用 contract `to_dict()` 严格 JSON 投影。
3. fetch 对全局结果 `[:24]` 导致后置维度饥饿：现按 locked brief 维度 round-robin 分配固定抓取预算，并加偏斜输入回归。

## 尚未声明通过

- Win10 安装/升级兼容。
- updater 签名与真实线上更新服务。
- 安装器卸载人工用例。
- TC-UI-NOW 的重复点击/重连/强杀恢复分支、完整 v4 history 兼容真人用例。
- 全部固定场景的最终 100% 追溯审计与真实完整报告人工 rubric。
