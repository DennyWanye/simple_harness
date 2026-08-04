# DeepResearch v7 调研 Spike 结果

> 日期：2026-07-19～2026-07-20
> 结论：**本轮“快速简化 + 一个调研 Spike + UI/文件交付”范围 PASS；报告业务结果为 partial，来源权威性仍需下一轮改进。**

## 真实入口与最终样本

- 输入：`请对 Tokio 异步运行时做深度调研，覆盖架构、核心组件、适用场景、常见陷阱，并给出选型建议。`
- 入口：源码 Tauri 桌宠，Computer Use 真点击、真输入；backend 由 Tauri 以
  `DESKPET_BACKEND_DIR=F:\projects\deskpet\backend` 启动。
- 最终 UI 复测 run：`aa61dccfaa794f69a502d978adfb5ca0`。
- engine 终态：`completed`；业务终态：`partial`，两者分开判定。
- 主 Agent 拆出 4 个子方向；3 个 valid、1 个 insufficient；`dr-2` 首轮失败后按诊断续跑，
  attempt 2 成功并带回 2 个来源；`dr-1` attempt 2 超时后诚实止损。
- 最终报告：5 个来源、3 个域名、3164 字符；报告/Artifact/final_assistant 各 1 次。
- Artifact：`F:\projects\deskpet\DeepResearch\请对-Tokio-异步运行时做深度调研，覆盖架构、核心组件、适用场景、常见陷阱，-aa61dccfaa794f69a502d978adfb5ca0.md`。
- 总耗时：412.49 秒，其中 plan 23.01 秒、children/join 309.88 秒、统一 synth 78.72 秒。
- UI 证据：最终气泡显示报告标题和正文开头，不再被通用 `(完成)` 占位覆盖。

## 关键中间样本

| run | 结果 | 暴露的问题 / 处理 |
|---|---|---|
| `ea3b167647614733ae7db8ecadecbd36` | insufficient，0 来源 | Search Gateway 被 CAPTCHA/超时熔断；增加有界候选 URL + 真实抓取验证兜底。 |
| `08b049c4501f45a7be427100eac6e629` | insufficient，HTTP 200 但 0 admitted | 定位 `FetchDocument.to_dict()` 无 legacy `ok` 且时间为 ISO 的跨层契约漂移；共享 passage 边界兼容两种契约。 |
| `865757204daf400398022b4f35488d1f` | partial，3/4 子方向有效，3 来源 | 后端统一报告、Artifact 和 exactly-once delivery PASS；发现宠物气泡仍被 `(完成)` 覆盖。 |
| `aa61dccfaa794f69a502d978adfb5ca0` | partial，3/4 子方向有效，5 来源 | UI 最后一公里修复后 PASS；实时 final_assistant 可见。 |

## 人工报告 Review

- PASS：报告包含 TL;DR、Key findings、Analysis、Caveats、Conclusion 和脚注引用。
- PASS：覆盖架构、竞品/生态、生产陷阱、选型建议，并明确未完成子方向。
- PASS：事实只来自被子报告接纳的来源；耗尽项没有编造结论。
- 已知质量边界：本次有效来源以中文技术博客为主，来源权威性弱于官方 Tokio 文档；
  下一轮价值 spike 应把“至少一个第一方来源/有效方向”提升为质量门。

## 测试证据

- 最终 parent-topic 契约修复后后端相关回归：`72 passed`。
- focused 子代理与 v7：`23 passed`。
- 前端 workflow final 展示、WS 与消息流：`24 passed`；TypeScript `tsc -b` PASS。
- 日志：`tauri-v7-contract-fixed.stderr.log`、`tauri-v7-final-ui.stderr.log`。
- 独立完成度复审：初审发现 parent-topic 契约与验收场景漂移；修复后复审 `VERDICT: PASS`，
  本轮剩余 blocker 为 0。

## 2026-07-20 AC-9～AC-11 真实 UI 增量验收

- 真实入口：源码 Tauri + Computer Use 真坐标点击/输入；Session
  `124b9030-ddd5-4f09-9dec-7be85c6d32fe`，新 run
  `9c42a6a145304123979f8ec6ae25cba0`。
- 过程可见 PASS：单张进度卡显示“主 Agent 拆出的 4 个子方向”和四个真实问题；每行显示
  queued/running/retrying/valid/insufficient、attempt 上限与来源数。最终 3 个方向首轮 valid，
  1 个方向原位推进到 attempt 2 后 insufficient；通用子代理面板没有把 5 个 attempts 重复渲染成 5 行。
- 文件卡 PASS：最终只有 1 张 `text/markdown` 文件卡，显示“打开 / 另存为 / 在文件夹中显示 /
  复制路径”。“打开”成功移交 Windows；本机没有 `.md` 默认关联，因此系统显示应用选择器。
  “在文件夹中显示”打开 Explorer 并选中新报告；“复制路径”显示“路径已复制”。
- 保存目录兼容 PASS：原报告继续落到
  `F:\projects\deskpet\DeepResearch\请对-Tokio-异步运行时做深度调研，覆盖架构、核心组件、适用场景、常见陷阱，-9c42a6a145304123979f8ec6ae25cba0.md`，
  大小 8065 bytes，SHA-256
  `ACA7965492130FB8756A438CD7417168DB9AA1515A03D20C4887141DA4C2A2D0`。
- “另存为”PASS：真实系统保存对话框生成
  `F:\projects\deskpet\DeepResearch\ui-save-as-9c42a6a1.md`；大小同为 8065 bytes，SHA-256 与原文件一致。
- 重启恢复 PASS：完整退出后使用相同
  `DESKPET_USER_DATA_DIR=F:\projects\deskpet\backend\userdata` 从源码重启；历史 Session 仍为 1 张
  terminal 主卡、4 个唯一方向和 1 张文件卡，主卡保持“已完成 · 6/6 · 100%”。启动日志
  `tauri-ac9-11-restart.stderr.log` 明确记录源码 backend 路径和 `Application startup complete`。
- UI 截图已归档到
  [`testcase evidence`](../../../testcase/2026-07-19-deepresearch-simplification/evidence/)：包含重启后四方向、
  唯一文件卡、Windows 打开方式、Explorer 选中项、原生另存为对话框和复制路径反馈。
- 最终自动化门：后端 focused `81 passed`，含默认配置/注册/恢复相邻面的 10 文件套件
  `142 passed`；前端 focused `80 passed`；TypeScript、Vite production build 与目标 lint PASS。
  完整目标 lint 只保留本次改动前已存在的问题。

### AC 兑现表

| AC | 结果 | 证据 |
|---|---|---|
| AC-1～AC-6、AC-8 | PASS | v7 focused/manager 自动化、Tokio root run、child records、报告人工 review 与本文件分阶段记录。 |
| AC-7 | PASS | 唯一报告/Artifact/final_assistant；旧 run 文件卡恢复；同 userdata 重启后新 run 投影不重复。 |
| AC-9 | PASS | 新 run 真实 UI 显示 4 个稳定方向；attempt 2 原位更新；重启后仍恰好 4 行。 |
| AC-10 | PASS | 四个文件操作均可见；打开、Explorer、另存为、复制路径均经真实点击，副本 hash 一致。 |
| AC-11 | PASS | canonical 绝对路径位于既有 `F:\projects\deskpet\DeepResearch`，文件名包含 topic 与同一 run id。 |

## 范围说明

用户要求的是“快速修改 + 一个调研结果 spike”。本结果证明核心链路可用，但没有把同题复跑
计作第二个 distinct 场景，也没有声称政策、市场、冷门低证据等完整发布矩阵已通过。

## 幂等与重放审查

- child 搜索/抓取/LLM 均为读型副作用；parent 在 search 节点 checkpoint 前崩溃时，尚未提交的
  child attempt 可能重放，这是本轮已声明的边界，不承诺 child attempt exactly-once。
- 最终 report/artifact/final_assistant 使用 `run_id:report`、`run_id:artifact`、`run_id:final`
  稳定 intent id，经 durable outbox 去重；两个真实 run 均各出现 1 次三类业务事件。
- 报告文件名包含 run id，同一 run 重放写向同一路径，不为同一 run 生成第二个 artifact identity。
- 前端实时 final_assistant 以 event id 做 mount 生命周期内去重；跨重启由 SessionDB 的
  `workflow_event_id` 持久化去重，历史 hydration 只过滤 lifecycle 行，不产生写副作用。
- retry 只在失败/低质量结果上继续；valid 结果立即终止该 child，失败不会被标成“已处理成功”。
