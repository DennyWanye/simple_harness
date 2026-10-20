# 第 8 块：Linux x86_64 / NixOS——一页方案

日期：2026-10-21

## 现状

- Linux 隔离区（bubblewrap 两层）已在本机 Lima 虚拟机 `sh-linux`（Ubuntu 24.04，**ARM64**）上实测：Python 3.12 / 3.13 装轮子跑任务 COMPLETED，启动检查 9 项全过（opt.198 / opt.201）。
- x86_64 没测过；NixOS 没写过要点。

## 做法

1. **x86_64 虚拟机**：Lima 再开一台 `sh-x86`（`--arch x86_64`，QEMU 软模拟，慢但够跑检查），Ubuntu 24.04 + Python 3.12 + bubblewrap。同一套做法：共享 `~/lima-share`，只装发版轮子，不碰源码。
2. **跑五项**：代码执行（`run_tests`）、文件核对、pytest 核对、超时清理（脱离进程树的后台进程也收掉）、越界拒绝（读 HOME / 别的临时目录 / 联网 / 写工作目录外）。用 opt.201 的启动检查命令 + 一局带 `run_tests` 的脚本模型任务。
3. **NixOS 要点**（写进接入说明 §3a）：
   - `bwrap` 装在 `/run/current-system/sw/bin/bwrap`（`environment.systemPackages = [ pkgs.bubblewrap ]`），不是 setuid；要 `boot.kernel.sysctl."kernel.unprivileged_userns_clone" = 1`（NixOS 默认开）。
   - `/nix/store` 要以只读方式挂进隔离区（Python 解释器与轮子都在里面）：外层 `--ro-bind /nix/store /nix/store`；现在的两层做法外层 `--dev-bind / /` 已经覆盖，内层 `--cap-drop ALL` 不影响。
   - `/etc/ssl`、`/etc/resolv.conf` 不需要（隔离区断网）。
   - 需要的内核设置：user namespaces、pid namespaces、`no_new_privs`、pidfd（5.3+；没有时退回命名空间核对）。
4. **缺依赖时**：`bwrap` 找不到 / 版本 < 0.8 / 开不了用户命名空间 → `status()["sandbox"]` 写清原因，`code_execution="off"`，任务里 `run_tests` 等按名拒绝；**不悄悄关掉隔离**（已有行为，补一条 x86 上的检查）。

## 验收

- `sh-x86` 上：启动检查全过；一局带 `run_tests` 的任务 COMPLETED；五项逐条有输出。
- 说明里有 NixOS 要点与缺依赖时的表现。
