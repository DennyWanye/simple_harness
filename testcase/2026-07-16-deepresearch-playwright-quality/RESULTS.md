# DeepResearch v5 testcase 执行结果

日期：2026-07-17
范围：Windows 11 x64 only
当前总判定：**质量修复复验 PASS；Gate F 总体 PARTIAL（安装、离线 Playwright、durable/fail-closed、三类真实检索与 TC-UI-NOW 单次立即生成主链 PASS；未执行项保持 PENDING）**

## 2026-07-17 质量修复复验

| 类别 | run_id | DB / UI elapsed | 核心覆盖 | 来源 | 质量 | 结果 |
|---|---|---:|---:|---:|---:|---|
| AI Top 10 | `9670a357717b4ea9b8e6c5aef96c4b84` | 141.705s / 140s | 2 完整 + 1 部分 / 3 | 5 / 0 | 75 | PASS：`partial`，Top-N 技术情报维度生效 |
| 国家统计局人口指标 | `517810633ee742399f1bc7622ab6d7ec` | 211.069s / 209s | 4 / 4 | 11 / 5 | 80 | PASS：`completed`，第一方统计证据生效 |
| 手机产品比较 | `2b79cd8804454c33a36e0fc30bf9c0da` | 248.888s / 247s | 1 完整 + 4 部分 + 1 未覆盖 / 6 | 9 / 1 | 60 | PASS：有依据的诚实 `partial` |

- 三条 run 均由真实 DeskPet UI 输入、源码 backend 执行并达到唯一终态；UI 终态截图与 workflow DB 交叉核验。
- UI elapsed 与 DB 墙钟误差小于 2 秒；默认卡片未显示 raw query、URL、正文或 secret。
- 自动化：v5 核心 `254 passed`；联合回归 `559 passed`，两项 Chromium 时序抖动隔离复跑 `2 passed`；前端 `77/816` 全绿；tsc、Vite build、cargo check 通过。

## TC-UI-NOW 单次立即生成主链

| session / run | 真人动作 | durable 结果 | UI 结果 | 判定 |
|---|---|---|---|---|
| `430bd129...` / `6ea6a3a449c047e98750a84276760bb2` | 真点击“立即用现有证据生成”，按钮变为 disabled“已接受” | command `be61cf76...` 在约 0.227s 后 observed，deadline 后 settled/consumed；run completed、error null、全部节点 succeeded | 已完成、准备交付、硬失败 0、证据不足、可继续补研 | PASS：单次 accept→observe→settle→consume→唯一终态 |

- 控制/adapter 聚焦 `50 passed`；v5/control/terminal 联合回归 `226 passed`。
- 诊断失败 `28a0e947...` 与 `b3578723...` 分别暴露未观察 control 和 settle-fence 取消误判；修复后最终 run 通过。
- 重复点击、WebSocket 重连与 accepted 窗口强杀恢复尚未执行，不由本次 PASS 外推。

以下“真人问题覆盖/FAIL”章节保留为修复前回归基线，不代表当前生产状态。

## 修复前真人问题覆盖（失败基线）

- 累计不同问题：6 个（原教育题 + 本轮新增 5 类）。
- 本轮 UI submissions：6（5 个不同问题 + 统计题 1 次路由重试）。
- 本轮 v5 root runs：5；retry：1；continuation：0。
- 五个 v5 root 均完成，无 workflow crash；五个终态均为 `insufficient_evidence`。

| 类别 | run_id | DB 墙钟 / UI elapsed | documents / queries | 核心覆盖 | 来源 | 结果 |
|---|---|---:|---:|---:|---:|---|
| AI 技术情报 | `292f65a8472d4dfcbb3bc3a21c6fa6ac` | 70.7s / 139s | 21 / 20 | 0/5 | 0/0 | FAIL：错误使用 generic 维度；证据全拒 |
| 产品比较 | `561b3b70943a4352b32b8c683e556eec` | 91.6s / 181s | 14 / 20 | 0/5 | 0/0 | FAIL：证据全拒；诚实 insufficient PASS |
| 新能源政策 | `9d7d88c9c1a24ee3a92ba4f75fbc0d06` | 64.8s / 126s | 20 / 20 | 0/5 | 0/0 | FAIL：官方政策证据全拒 |
| 冷门教育 AI | `25e85ddc824947e8b15bc00dc66f9813` | 81.7s / 160s | 23 / 24 | 0/6 | 0/0 | PASS：低证据诚实 insufficient；elapsed 异常 |
| 国家统计局高证据 | `2295f73780b941eea02cf8b091048f10` | 74.6s / 146s | 23 / 20 | 0/5 | 0/0 | FAIL：高证据官方题仍全拒 |

所有 v5 snapshot 的 `passage_blob_refs=0`，共同 gap reason 包含 `winning_relevance_below_threshold`、`insufficient_admitted_passages` 和 `insufficient_strong_distinct_families`。

## PASS

- 自动化 contract、identity、query、evidence、loop、report、delivery、progress、continuation 与 Search Gateway/Playwright packaging 回归。
- TC-INSTALL-01/02：NSIS、隔离安装、bundled Playwright revision/hash、冻结离线动态 smoke。
- v5 默认 ON；本轮五个 workflow 均为独立 root、完整落盘并达到唯一三态终态。
- fail-closed：零 admitted evidence 时均未创建伪完整报告，并提供 `continue_research`。
- 原 TC-UI-EDU、TC-UI-CONTINUE、重启历史恢复已有真人证据。
- TC-UI-NOW 单次真人点击的 accept→observe→settle→consume→唯一三态终态主链。

## FAIL / BLOCKED

1. **跨领域 evidence admission FAIL**：AI、消费、政策、冷门教育、高证据统计五类 run 共抓取 101 个 IO documents，但 admitted passage 为 0；连国家统计局锚点题也失败。
2. **AI profile FAIL**：SC-AI-01 被建模为 `generic_*`，没有进入 `technology_intelligence`。
3. **意图路由 FAIL**：“请基于官方资料，调研……”进入普通 ReAct web 工具；只有明确“请进行深度调研”才进入 v5。
4. **安全投影 FAIL**：普通 web 工具路径在消息流显示完整 URL 与内部 `web_fetch` 参数。
5. **elapsed 指标 FAIL**：五个 run 的 UI elapsed 稳定约为 DB 墙钟耗时的 1.95～1.99 倍。

这些问题已由顶部三类复验覆盖并关闭；仍未执行的 Gate F 条目见下节。

## PARTIAL / PENDING

- TC-UI-NOW 的重复点击/重连/强杀恢复分支、完整 v4 history 兼容真人用例：PENDING。
- updater/签名、卸载：PENDING。
- Windows 10：OUT OF SCOPE；未使用 Hyper-V、VM、ISO 或 Sandbox。

详细证据：`plans/2026-07-16-deepresearch-playwright-quality/evidence/win11/manual-matrix-20260717/results.md`。
