# 事件 X-3：给原生跑一个按站点的内存探针（X2-F2 的测量装置）

- 日期：2026-09-09
- 分支：`worktree-mem-probe`（未合并）
- 前置：[事件 X](DECISION-X-BACKEND-MEMORY-GROWTH.md)（已并 `87b9423d`）、
  [事件 X-2](DECISION-X2-MEMORY-LANES-GROWTH.md)（已并 `b7ef7d95`）
- 收口目标：**X2-F2**（= X-F2）。两轮离线复现都证明每回合净保留只有 5.0 KiB
  （X-2 打开全部 memory 车道后仍然如此），而原生 backend 仍每个 provider 回合
  涨 250–300 MB（attempt 11：turn 15 RSS 5143 MB、`MALLOC_SMALL` 4.5 GB，
  24 回合后 4.7 GB）。**离线解释不了这笔账，只有原生测量能定。**
- 触碰文件：新增 `backend/observability/memory_probe.py`（+387）、
  新增 `scripts/native/memory_probe_report.py`（+257）、
  `backend/main.py`（+16）、`scripts/native/launch_native_candidate.py`（+13）、
  新增 `backend/tests/test_memory_probe.py`（+242，15 例）、
  新增 `backend/tests/native/test_memory_probe_report.py`（+231，10 例）

## 1. 结论先行

本轮**不修任何东西、不下任何内存结论**，只交付一件事：让下一次原生旅程的
`native.log` 自己回答 X2-F2 —— 那 250–300 MB/回合到底落在**哪一行代码**上。

| 决定 | 取法 |
|---|---|
| 探针默认状态 | **关**。`SIMPLEHARNESS_MEMORY_PROBE` 没被显式置真时 `build_memory_probe()` 返回 `None`，`main.py` 连终态监听器都**不注册**：不启 `tracemalloc`、不 import `psutil`、不碰 `gc.get_objects()`、不建目录、不写日志。 |
| 挂点 | `SdkRunToolAuthorityRegistry.add_terminal_listener` —— 事件 X（`ToolRegistry._calls` 归还）与事件 AA 用的**同一条**前台 Run 终态缝，`main.py:8208` 邻位。每个 Run 终态 = 一个"回合"。 |
| 采样频率 | 每 N 个终态一次，`SIMPLEHARNESS_MEMORY_PROBE_EVERY`，默认 1。 |
| 输出 | 每次采样往 stdout 打**一行** `memory.probe`（structlog JSON → 被发射端 drain 进 `native.log`），外加一份 `tracemalloc` 快照 pickle 到 `<userdata>/memory-probe/turn-<n>.snap`，**只留最近 30 份**。 |
| 载荷 | **永不记录**。日志与快照里只有 `file:line`、字节数、对象计数、类型名。 |

## 2. 为什么是这个形状

X-2 的离线测量已经把「产品侧每回合保留」压到 5 KiB，还顺手证明了
gc 类型普查里 `Task`/`Context`/`ExecutionLease`/`CancelToken` 都不涨。
于是原生那 250–300 MB 只可能来自离线夹具**没有**的东西：真 WeMM
（X2-F4）、FastAPI/uvicorn/WebSocket 广播缓冲（X2-F5）、真 provider 的大请求/
响应体、原生 Tauri 侧的 IPC，或者某条只在真旅程里走到的路径。

这些都**不可能**靠再写一个离线夹具猜出来 —— 逐个把嫌疑车道搬进离线复现，
成本高且永远不能证伪「还有第五个我没想到的」。所以本轮改成
**在原生进程里直接按分配站点归因**：`tracemalloc` 的 `file:line` 增量
天然回答"哪一行"，`gc` 类型普查增量回答"什么对象"，RSS 回答"这两者
加起来能不能解释 250 MB"（如果不能，说明是 C 扩展/mmap/分配器碎片，
那本身就是一条决定性结论，方向立刻从 Python 侧转到 `vmmap` 侧）。

**为什么不常开**：`tracemalloc` 25 帧会给每一次分配挂一份 25 层的栈快照，
自身内存开销与被跟踪对象数同量级，采样一次约 0.4 s（空进程；真 backend 更慢），
快照 pickle 单份在真 backend 里可能几 MB 到几十 MB。这是一次**诊断跑**的
代价，不能带进产品默认路径 —— 所以做成 opt-in，且关闭时是编译期就没有的成本
（返回 `None` 之后调用方一行都不执行），而不是"打开一个 if 判断"。

## 3. 日志行的字段

```json
{"event": "memory.probe", "probe_seq": 3, "terminal_seq": 3, "every": 1,
 "baseline": false, "run_id": "…", "rss_kb": 69568, "rss_delta_kb": 13696,
 "rss_max_kb": 72784, "tracemalloc_current_kb": 31075,
 "tracemalloc_peak_kb": 31077, "tracemalloc_delta_kb": 9778,
 "gc_objects": 15294, "gc_counts": [1, 5, 3],
 "top_sites": ["<mod>.py:13 9778.3 400", "pathlib.py:1078 0.1 1", "…"],
 "type_census_delta": ["list 3", "dict 1"],
 "snapshot": "turn-3.snap", "sample_ms": 400.3}
```

| 字段 | 口径 |
|---|---|
| `rss_kb` / `rss_delta_kb` | 进程当前 RSS（`psutil`；不可用则 `null`）与相对上一次探针的增量 |
| `rss_max_kb` | `resource.getrusage(RUSAGE_SELF).ru_maxrss`（macOS 是字节，已换算成 KiB） |
| `tracemalloc_current_kb` / `_peak_kb` / `_delta_kb` | `tracemalloc.get_traced_memory()` 与相对上一次探针的增量 |
| `top_sites` | **相对上一次探针**按字节增量排序的 top 15，每项 `file:line size_kb count`（`size_kb` 是本回合增量，`count` 是对象数增量），只收增量为正的 |
| `type_census_delta` | `gc.get_objects()` 类型普查相对上一次探针的 top 15，每项 `TypeName delta` |
| `gc_objects` | 本次普查的对象总数（X2-F2 点名要的 `len(gc.get_objects())`） |
| `baseline` | 第一条探针没有"上一次"，`top_sites` 是**绝对量**不是增量；报告脚本据此把它排除在增长统计外 |
| `snapshot` | 本次快照的文件名（写失败时 `null`，探针不因此中断） |

**站点路径做过归一**（`shorten_location`）：`/site-packages/`、`/backend/`、
`/simple_harness/` 之前的部分全部截掉，其余落到最后 3 段。这既让跨进程/跨机器
的站点可比，也顺手保证日志里**不出现用户家目录**。整行还会再过一遍
`observability/log_redaction.redact_log_event`（已验证不改这些字段）。

### 3.1 顺序上的讲究

采样内部严格按 **RSS → `get_traced_memory` → `take_snapshot` → 写快照 →
gc 普查** 的顺序：普查会临时造一个几百万元素的 list，必须发生在快照**之后**，
否则它自己会占满 `top_sites`（X-2 §2.2 的口径说明踩过这个坑）。
探针**不**持有上一次的 `Snapshot`（那会常驻几十 MB），只留一份
`file:line -> (size, count)` 的字典自己算差值；完整快照落盘供离线比对。

探针整条路径吞异常：`observe_terminal` 里任何失败都不允许影响 Run 终态。

## 4. 怎么开

### 4.1 原生旅程（推荐）

```bash
python scripts/native/launch_native_candidate.py \
    --source … --bundle … --installed-target … --evidence-root … --launch \
    --memory-probe                # 每个前台 Run 终态一条
    # --memory-probe-every 2      # 或者每 2 个终态一条
```

发射端把 `SIMPLEHARNESS_MEMORY_PROBE=1` / `..._EVERY=N` 放进 spawn 给 backend
的环境，并把 `memory_probe` / `memory_probe_every` 写进 `launch.json`（证据可追）。
**不带 `--memory-probe` 时发射端会主动 `pop` 掉继承来的同名变量**，保证跑与跑
之间确定，不会因为壳层里残留的 export 而意外打开。

### 4.2 直接跑 backend

```bash
SIMPLEHARNESS_MEMORY_PROBE=1 SIMPLEHARNESS_MEMORY_PROBE_EVERY=1 \
  backend/.venv/bin/python backend/main.py
```

| 变量 | 默认 | 含义 |
|---|---|---|
| `SIMPLEHARNESS_MEMORY_PROBE` | 关 | `1/true/yes/on` 才开 |
| `SIMPLEHARNESS_MEMORY_PROBE_EVERY` | 1 | 每 N 个 Run 终态出一行 |
| `SIMPLEHARNESS_MEMORY_PROBE_FRAMES` | 25 | `tracemalloc` 栈深 |
| `SIMPLEHARNESS_MEMORY_PROBE_KEEP` | 30 | 最多保留几份快照 |
| `SIMPLEHARNESS_MEMORY_PROBE_TOP` | 15 | 站点 / 类型各取前几 |
| `SIMPLEHARNESS_MEMORY_PROBE_CENSUS` | 开 | 置 0 关掉 gc 普查（想把采样耗时压到最低时用） |

打开时 backend 启动会多打一行 `memory.probe.started`（带 `every` 与快照目录），
旅程校验可以据此确认探针真的生效了。

## 5. 报告脚本

```bash
python scripts/native/memory_probe_report.py --log <evidence>/native.log --top 20
python scripts/native/memory_probe_report.py --log native.log \
    --snapshot-old <userdata>/memory-probe/turn-4.snap \
    --snapshot-new <userdata>/memory-probe/turn-18.snap
```

四段输出：

1. **每探针增长表** —— `rss_kb / Δrss_kb / tm_cur_kb / Δtm_kb / tm_peak_kb /
   gc_objects / Δgc / ms`，一行一个探针；
2. **汇总** —— RSS 与 tracemalloc 的增量条数、总量、均值、中位数、最大值
   （`baseline` 那条不计）；
3. **累计增长最大的分配站点** —— 把每次探针 `top_sites` 的增量按 `file:line`
   求和，给 `total_kb / kb每探针 / 出现在几次探针里 / 对象数`。
   **这一张表就是 X2-F2 的答案**：如果 250 MB/回合是 Python 侧分配，它会在这里
   现出一行 ≈ 250000 KiB 的站点；如果这张表的总和远小于 `Δrss`，那么结论同样
   决定性 —— 增长不在 Python 堆上，下一步该查 C 扩展 / mmap / 分配器碎片；
4. **累计增长最大的 gc 类型**。

`--snapshot-old/--snapshot-new` 额外用官方 `Snapshot.compare_to` 做一次跨回合
全量比对（能看见日志 top 15 之外的站点）。脚本对非 JSON 行、非 `memory.probe`
行、乱序的 `probe_seq` 都免疫（真实 `native.log` 就是这三样的混合体）；
一条 `memory.probe` 都没有时退出码 1 并提示要加 `--memory-probe`。

## 6. 测试

| 文件 | 例数 | 覆盖 |
|---|---|---|
| `backend/tests/test_memory_probe.py` | **15** | ① 默认关 / 显式关（`""/0/false/off/no` 五个值参数化）时 `install_memory_probe` 返回 `None`、**假 registry 的监听器列表恒为空**、无日志行、快照目录不被创建、`tracemalloc.is_tracing()` 不变；② 打开后 `EVERY=3` 驱 7 个假终态恰好出 2 行（`terminal_seq == [3, 6]`、`run_id == ["run-2", "run-5"]`、首条 `baseline=True`）；③ 字段集合逐字断言（17 个键），`top_sites`/`type_census_delta` 各 ≤ 15 且**逐项匹配 `file:line 数字 数字` / `类型名 数字` 正则**，并断言故意分配的 `"S3CRET-PAYLOAD"` 字符串**不出现在整行 repr 里**；④ `KEEP=3` 驱 6 个终态后目录里恰好 `turn-4/5/6.snap` 且能被 `Snapshot.load` 读回；⑤ 未 `start()` 的探针忽略终态；⑥ 监听器收到没有 `run_id` 的对象/`None` 不抛；⑦ 关掉普查时 `gc_objects is None`；⑧ `shorten_location` 归一 |
| `backend/tests/native/test_memory_probe_report.py` | **10** | 夹具 `native.log`（混入 `foreground.runtime.bound` 行、非 JSON 行、乱序 `probe_seq`）的解析与排序、增长表表头与逐格数值、汇总把 `baseline` 排除、累计站点表按 `file:line` 求和（`40960+41984=82944`，baseline 的 20000 不计）、累计类型表、`main()` 全文渲染、无探针行时退出码 1、`--snapshot-*` 必须成对、两份真快照的 `compare_to`，以及一条**生产者→消费者往返**用例：真跑 3 次探针 → `json.dumps` 成日志 → 报告脚本解析回来（保证两边格式永不漂移） |

一次单进程执行（`pytest -p no:randomly`）：

| 套件 | 结果 |
|---|---|
| `tests/test_memory_probe.py` | **15 passed in 5.40s** |
| `tests/native/test_memory_probe_report.py` | **10 passed in 1.51s** |
| `tests/test_main_service_registrations.py`（`main.py` 被改动） | **4 passed in 0.17s** |
| 三者合并再跑一次（单进程） | **29 passed in 6.40s** |

**未注册任何 service**（探针是 `main.py` 里的局部对象，不进 `ServiceContext`），
所以 `context.py::_VALID_SERVICES` 未动；`test_main_service_registrations.py`
只是因为改了 `main.py` 才跑的回归。

## 7. 手工验证（非原生）

用 4 次假终态、每回合真分配 200 × 50000B ≈ 10 MB 跑了一次探针：

| 探针 | `Δrss_kb` | `Δtm_kb` | `top_sites` 第一项 |
|---|---|---|---|
| 2 | 18752 | 10019 | `<stdin>:13 9778.4 400` |
| 3 | 13696 | 9778 | `<stdin>:13 9778.3 400` |
| 4 | 12976 | 9781 | `<stdin>:13 9778.9 400` |

分配站点被**逐字**归因到造 ballast 的那一行，量级与真实分配一致
（9778 KiB ≈ 200×50000B），采样耗时 ~0.4 s/次，快照单份 ~290 KB。
这正是原生跑里需要的那张表 —— 如果 250 MB/回合是 Python 侧的，它会以同样的
形状出现在 `top_sites` 第一行。

## 8. Followup

| 编号 | 内容 |
|---|---|
| **X3-F1** | **本轮只交付装置，没有跑原生**（纪律：不启动原生 app）。X2-F2 仍然开着，下一次原生旅程必须带 `--memory-probe` 起，收工后跑 `memory_probe_report.py` 并把三张表贴进 X2-F2 的收口备忘。 |
| **X3-F2** | 快照体积在真 backend 里未知（本轮空进程 ~290 KB/份）。若 30 份把证据目录撑爆，用 `SIMPLEHARNESS_MEMORY_PROBE_KEEP` 调小，或 `..._EVERY` 调大。原生跑前建议先看一眼头两份的大小。 |
| **X3-F3** | 挂点是 `SdkRunToolAuthorityRegistry` 的**全部** Run 终态，不只前台 Run；若某次旅程里后台 Run 也走这个 registry，`terminal_seq` 会比人眼数的"回合数"多。报告表里 `terminal_seq` 与 `probe_seq` 同时打出来就是为了让这件事看得见；真需要严格只数前台时，改挂 `foreground.runtime.closure_settled` 那条缝即可（本轮没做，因为多数几个终态不影响"每回合增长"的量级判断）。 |
