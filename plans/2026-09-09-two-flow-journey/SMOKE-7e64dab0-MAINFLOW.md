# 主流程冒烟（2026-09-09 14:46–15:05）· 交付版本

用户 14:45 决定：memory 与 task 编排将大改，涉及两者的大量测试停止，先交付**主流程跑通的版本**。本记录是该版本的冒烟证据。

## 版本

| 项 | 值 |
|---|---|
| Host 源码 | main `b75277aa`（含 AK 重启就绪/重放循环、AH-2/AF-2 效果页引用提示、AL goal.set 两步流程修复） |
| bundle | `tauri-app/src-tauri/target/debug/bundle/macos/SimpleHarness Memory Verify 7e64dab0p18120.app`（前端自 `7e64dab0` 构建；AL 只改后端，后端从 `--source` 起，无需重建） |
| bundle id | `com.dennywanye.simpleharness.verify09077e64dab0p18120` |
| Memory SDK | 0.6.38，installed target `.local-test-evidence/2026-09-07/installed-h0710-m0638-s0313` |
| 模型 | deepseek-v4-flash，`model_overrides.toml`：`context_window = 32000`、`reasoning_mode = "fast"` |

## 冒烟步骤与结果（两轮旅程驱动 `TF_TURNS` 子集，证据 `.local-test-evidence/2026-09-09/native-smoke-7e64dab0/`）

| 阶段 | 轮 | 结果 |
|---|---|---|
| 全新 userdata 启动 | — | startup complete，主对话就绪（`空闲 · 排队 0`），无 Unknown service |
| 流程一 | T1 记偏好、T2 新建任务、T3 任务目录写 clips.md、T5 记决定、T9 记流程、T11 标记完成 | 6/6 首次发送即 COMPLETED（11–42 s/轮）；`clips.md` 落盘；证据重放 6 次 = 6 个信封，无循环 |
| 同 userdata 重启 | 按就绪文档三步杀干净后 `--userdata` 重启 → `primary-ui-vb61gjf7` | 主对话就绪（不再停在「等待主对话就绪」）；`companion_projection_history_closed_identity_unready` 带结构化收据 `durable_binding_status=ready` |
| 流程二 | T12 回忆最早偏好、T14 精确打开任务并复述决定、T16 按流程执行、T22 收尾总结 | 4/4 首次发送即 COMPLETED（26–91 s/轮）；T12 正确答出「霜降素材 / 成片」；T14 从 task scope 披露复述恢复要点与决定；重放 10 次无循环、无 `recall_timeout`、无 group_blocked |

## 已知未闭合（只记录，不再深测，属 memory/task 大改范围）

- T16 流程「霜降清点」被正确召回（memory_id/revision 均对），但任务目录未见 `inventory.md`/`inventory-backup.md` 落盘。
- T22 因子集未跑 T7（字幕校对任务未创建），模型回答"任务已完成"。
- A6 第 13 次 12/4/1 的未闭合项、两轮旅程 run2 全量、Manual run7、401 重钉、语料剩余适配：停止。

## 启动命令（不含凭据）

```bash
backend/.venv/bin/python scripts/native/launch_native_candidate.py --launch \
  --source "$PWD" \
  --bundle "$PWD/tauri-app/src-tauri/target/debug/bundle/macos/SimpleHarness Memory Verify 7e64dab0p18120.app" \
  --installed-target "$PWD/.local-test-evidence/2026-09-07/installed-h0710-m0638-s0313" \
  --evidence-root "$PWD/.local-test-evidence/<日期>/<目录>" --port 18120 \
  --model deepseek-v4-flash --env-file .local-test-evidence/2026-09-07/credentials/deepseek.env
# startup complete 后写 <E>/userdata/model_overrides.toml（见上表）；重启加 --userdata <E>/userdata
```
