# Gate C — Win11 浏览器打包、运行池与 checkpoint 循环

日期：2026-07-16
结论：**PASS（Win11-only）**

## 范围

- 本门禁只验证 Windows 11；未启用或依赖 Hyper-V、VM、ISO、Win10 镜像或系统重启。
- 未触碰共享 `dist`，未运行完整 NSIS；所有 frozen smoke 使用隔离短路径。

## Task 6 — Playwright/Chromium 离线打包

- 精确依赖：Playwright `1.61.0`、Chromium Headless Shell revision `1228`、browser `149.0.7827.55`。
- acquisition/build/runtime 共用 fail-closed contract；校验 archive length/MD5/SHA256/CRC/member path、`browsers.json` hash/revision、exe length/SHA256。
- runtime owner 按 `test override > frozen > Tauri resource > dev` 选择唯一 authoritative owner；frozen/Tauri 缺资源时禁止回退到开发缓存。
- PyInstaller 收集 official Playwright driver、唯一 r1228 browser root 与许可证；原子发布失败会回收全部 staging。
- 隔离最小 onedir：`512,849,293` bytes / `545` files；deny-proxy frozen dynamic render 输出 `bundle-ok`。
- 证据：`evidence/win11/task6-production-packaging.md`、`evidence/win11/task6-production-packaging.json`。

验证：

- `backend/tests/test_playwright_bundle.py`: **16 passed**（root 独立复跑）。
- Python compileall、PowerShell parse、`uv lock --check`：通过（Task 6 证据）。

## Task 7 — Retrieval-owned PlaywrightRendererPool

- 显式 executable/root resolver；不探测全局 cache、不下载。
- 单 browser、每 render 独立 context、并发上限 2；static → bundled Playwright → Edge，v5 不回退 Jina。
- parent deadline/cancel、idle shutdown、crash 单次重启、GET/HEAD/OPTIONS、动作/页数/域名上限均已覆盖。
- CAPTCHA/security/login/app-shell 作为 invalid evidence，不用 Edge 掩盖。
- root 审计补强：
  - idle shutdown 与新 context 分配竞态；
  - PID + create-time ownership 只包含 exact browser tree/对应 driver ancestor，不误杀同期其他 Playwright driver；
  - manager start 中途 cancel 必须关闭 partial driver；
  - 每个动作后的延迟跨域导航仍由 navigation request fence 拦截。

验证：

- `backend/tests/test_playwright_renderer_pool.py`: **16 passed in 57.11s**（Task0 r1228 真浏览器，root 独立复跑）。
- 结束后 exact Task0 browser process = `0`，Node Playwright driver = `0`。
- DeskPet PID `90580` 存活且未触碰。

## Task 8 — checkpointed adaptive loop

- v5 graph 具有真实 `gap_evaluate → gap_work → gap_join` 与 `quality_audit → repair_work → repair_join` cycle；静态预算 `64/16`，`max_supersteps=384`，`recursion_limit=512`。
- 300 秒软检查、120 秒续租、900 秒 cap、两轮 plateau、generate-now/cancel fence 由 durable `LoopPolicyState` 驱动。
- query-strategy 仅在 deterministic rescue 无增益后最多 claim 一次，返回 query 实际进入下一轮 gap work。
- 零核心证据直接 `insufficient_evidence`，跳过 dimension analysis、完整 report synthesis 与 artifact persist。
- repair 只有 committed `status=completed` 才增加 counter；重放幂等。
- engine terminal `completed` 与 business delivery `completed/partial/insufficient_evidence` 分离；v4 validator 行为不变，未知版本 fail closed。
- v5 manifest 固定六类 LLM role，并锁定：
  - evidence policy `33538ebb585821eb9e158ae22df177f89e165a5c54ff6ae8d2cdafe434d62cee`
  - report rubric `2b25e991a799bfb0a378b49058f312004573871e6cbc740e9d64fac1cbec3d94`
- Playwright 更新造成全局 lock hash 改变时，v1-v4 historical dependency identity 仍冻结为旧值，四个 legacy implementation bundle hash 字节级保持不变。

验证：

- 子任务聚焦 + v4 邻接：**114 passed**；补充 v5/v4 模块集：**167 passed**。
- root 独立复跑 packaging + v5 loop/identity：**30 passed**，另 loop/identity：**14 passed**。
- scoped `git diff --check`：通过（仅 line-ending warning，无 whitespace error）。

## Gate C exit

- frozen dynamic page：PASS。
- browser lifecycle/crash/cancel/orphan：PASS。
- checkpoint/lease/plateau/control fence：PASS。
- legacy v1-v4 identity：PASS。
- Win11-only / no Hyper-V dependency：PASS。
