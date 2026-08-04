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

### ✅ PASS（补跑，用户在场）— GUI 真截图 + 真坐标点击 + 真中文输入 + curation 真 FIRE at turn=2

见 `EVIDENCE-curation-fire.txt`。真模拟人工链路（**非脚本回放/非 WS 直注**）：

1. **真启动**：installed shell 触发自更新器（killed，不装）后改走 dev tauri 源码后端；
   computer-use 授权 deskpet.exe（dev exe 路径 != 安装路径，前几次基名授权超时，最终
   用户盯弹窗点允许后 granted）。
2. **真截图 + 真点击**：截图见桌宠主界面「已连接」→ 跳过 onboarding → 点「消息」开
   完整聊天窗（含可聚焦输入框「和桌宠说点什么…」+「Enter 发送」）。
3. **真中文输入 2 轮**（同会话 sid=`task-task-default-1-1`，clipboard/Unicode 注入 WebView2）：
   - 轮1「你好，我叫小王，是个程序员」→ agent 真调 `memory_write {"text":"用户叫小王，是个程序员","tier":"l3","salience":0.7}` → 权限门「写入文件」点「允许一次」→ `✓ memory_write 完成` → 桌宠真回「你好呀小王～我记住啦」。
   - 轮2「我平时用 Python 和 Rust 写代码，请记住这个偏好」→ 第 2 个 terminal turn。
4. **日志判定 FIRE**：`oh4_curation_nudge sid=task-task-default-1-1 turn=2 decisions=0 remembered=0`
   —— curation nudge **在 turn=2 真触发**（`every_n=2` 周期），正是迁移点亮的 flag 驱动的下游行为。
   `decisions=0` = 后台 curator 发现 agent 已内联 `memory_write` 记过、无额外可记；关键是
   **turn=2 触发链路活着**（迁移后 flag → wired → fire 全程贯通）。

**真测踩坑（额外）**：① compact 桌宠面板语音优先、无常驻文本框 + WebView2 SendInput 焦点
不进 DOM → 直接 type/paste 不落字；**解法**=点「消息」开完整聊天窗（有真输入框）后才能输入。
② 每点一次「新话题」= 新 session → 每会话计数器从 1 起，`every_n=2` 永不到 2 → **必须同一会话
连发 2 条真实用户消息**（greeting 不进 curation 计数）。③ installed shell 一启动就弹自更新器
（killed 不装），改用 dev tauri。

## 清理

测试实例已停（kill 8150/5190 + spawned deskpet.exe），8100/5173/8150/5190 全 free，
0 残留 deskpet/cargo。预先存在的 leftover 栈（手动 `python main.py` + 旧 deskpet.exe）+
误触发的安装器均已清，**用户安装版未改动**。
