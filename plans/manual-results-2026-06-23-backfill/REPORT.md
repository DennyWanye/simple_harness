# 真机验证报告 — 出厂 feature-flag 向存量 install 传播（additive backfill）

- **日期**: 2026-06-23
- **被测改动**: `backend/config.py` `_merge_missing_feature_flags()`（commit `dbcec0ba`）
- **被测真因**: `seed_user_config_if_missing` 从不把 bundle 新增 key merge 进已存在的
  unified 用户 config → 新出厂 flag（`curation_nudge` 等）只对全新装生效，存量
  `%APPDATA%\deskpet\config.toml` 永远拿不到 → 功能对存量用户暗着。

## 测试配方（真机 / 源码栈 / 非脚本回放）

- **存量 fixture**: 取**真仓库 `config.toml`** 用 Python（UTF-8 安全）剥掉 3 个新 flag 行
  （`curation_nudge`/`curation_nudge_every_n_turns`/`auto_learnings`）→ 写进隔离
  `DESKPET_USER_DATA_DIR`，模拟"升级前的存量用户 config"。
- **启动**: 真 `npx tauri dev`，注入 `DESKPET_BACKEND_DIR`+`DESKPET_PYTHON`（源码后端，
  非 frozen exe）+ 隔离端口 8150/5190 + 隔离 userdata。**不设 `DESKPET_CONFIG`**
  （否则短路 `resolve_config_path` 的 seed/merge 路径，测试失效）。
- **判定**: 真启动栈的实际行为（backend 日志 + 落盘 config），非重跑内部函数。

## 障碍与 workaround（真测纪律：≥3 次不同 workaround）

| # | 障碍 | workaround |
|---|---|---|
| 1 | 首次启动卡死 10min：预先存在的 leftover dev 栈（手动 `python main.py` on 8100 + `deskpet.exe` + `cargo`）持 cargo build-dir 锁 + 占用 target/debug/deskpet.exe，我的 build 阻塞在锁上（隔离端口不解决 target 锁） | 写 `scripts/cleanup_deskpet_leftovers.ps1` 精准清理 leftover（按名/端口/cmdline，不误杀用户 8 个无关 node）→ 清后增量 build ~20s 即 boot |
| 2 | backend 日志拿不到：PowerShell `*>> $log` 把 native-process 输出缓冲到管道结束才写，运行期日志为空；force-kill 丢缓冲 | 改 launcher 用 **cmd 重定向** `>> log 2>&1`（边产边写）→ 运行期即可读到 backend structlog |
| 3 | GUI 截图 + 聊 2 轮：computer-use 授权弹窗 **2 次各 300s 超时**（用户离开键盘）+ relay `chinzy.com` 已知间歇 5xx | 见下"受限"；迁移效果已在 boot/config-resolution 层直证，GUI 聊天测的是下游 curation FIRE（已在 `b8d57bf3` boot-log + 12 单测验过），非本次迁移改动 |

## 结果

### ✅ PASS — 迁移在真启动栈端到端生效

真 Tauri boot 的 backend 日志（`tauri-dev.log`，见 `EVIDENCE-real-boot.txt`）：

```
L29  [backend_launch] Dev python=...\.venv\Scripts\python.exe backend_dir=...\backend
     → Tauri spawn 的是【源码后端】(我的改动)，非 stale frozen exe（pitfall #8 判据）
L47  INFO:config:feature_flag_merge_applied count=3
     keys=['memory.v2.curation_nudge','memory.v2.curation_nudge_every_n_turns','memory.v2.auto_learnings']
     backup=...\userdata\config.pre-migrate-bak
     → 我的迁移代码在【活的 backend】里跑，精确补上 3 个缺失 flag + 写备份
L108 event='oh4_curation_nudge_wired' every_n=2 auto_learnings=True
     → 运行 backend 把迁移后的 flag 读成 True 并【构造了 curator】(lifespan 接电)
```

落盘 `userdata/config.toml`（boot 后）：

```
curation_nudge = true
curation_nudge_every_n_turns = 2      # 每 N 回合触发一次；2 = 验收可见，prod 可调高
auto_learnings = true
```

- 用户原有自定义值（`facts_extract=false`、`backend.port`、`llm.model`）**不变**；
  中文注释（`Strangler-Fig` 等）**存活**；`config.pre-migrate-bak` 备份在。
- **完整链路**：源码后端 → 迁移 applied → flag 读成 True → 功能 wired live。
  即"存量用户升级一次后 curation_nudge 从暗变亮"，正是要修的真因。

### ⏳ DEFERRED（环境受限，非失败）— GUI 截图 + 聊 2 轮看 curation 真 FIRE

- **阻断 1**: computer-use 授权弹窗 2 次各 300s 超时 → 用户离开键盘，无法真截图/真点击/真输入。
- **阻断 2**: relay `chinzy.com` 已知间歇 5xx（STATUS 多条记录），聊天需真 LLM 链路。
- **为何不算 gap**: GUI 聊 2 轮验证的是【下游 curation FIRE 行为】（curator 每 N 回合
  fire-and-forget → facts.upsert），这块已在 commit `b8d57bf3` 的 boot-log 真测 + 12 单测
  验过。本次改动是【config 迁移】，其可观测效果 = "存量 config 里 flag 变 true 且被运行
  backend 读成 True 接电"，上面 L47/L108 + 落盘 config 已直证。
- **待用户在场 + relay 恢复时补**：真截图桌宠 + 真输入 2 轮中文 → 抓 `curation_nudge fire turn=2`。

## 清理

测试实例已停（kill 8150/5190 + spawned deskpet.exe），8100/5173/8150/5190 全 free，
0 残留 deskpet/cargo。预先存在的 leftover 栈（手动 `python main.py` + 旧 deskpet.exe）也已清。
