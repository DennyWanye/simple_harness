# 交付回执：手机 Linux 隔离区兼容（SDK opt.201）

日期：2026-10-21。对应 AIPhone 补充交接《opt.200 在手机 Linux 上的 namespace 启动阻断》（证据包 `AIPhone-opt200-phone-sandbox-blocker.zip`）。
AIPhone 的证据材料只留在本机私有目录，不进仓库。**没有手机真机：手机实测全部未执行，由 AIPhone 完成。**

## 1. 新候选身份

| 项目 | 值 |
|---|---|
| 版本 | `0.13.0.dev20260925+opt.201`（不覆盖 opt.200） |
| SDK 源码提交 | `1cda89241bf4e8147434860f249fb4cdb7534224` |
| Host 钉版提交 | `c78891df7` |
| wheel | `backend/vendor/simple_harness_sdk-0.13.0.dev20260925+opt.201-py3-none-any.whl` |
| wheel SHA-256 | `35b9c3e8530af454aa23d31d3ba959b31fb126d8113c8b06a68bf1b6829d1fa9` |
| 候选清单 | 同目录 `...opt.201.candidate-manifest.json`，SHA-256 `576b7f0fc1ee78ce01205c9fd5c7193014ad2e9cba3c49d4d2e30e7b5c578df8` |
| Python / 依赖 | 与 opt.200 相同（`>=3.11`；已测 3.12、3.13） |
| 验收门 | 发版门 167 条通过，清单 `VALIDATED`；独立核验回执 `review-014109.json`（结论 PASS，必须处理清单为空） |

## 2. 对阻断的判断

证据成立。bubblewrap 在同一次克隆里同时建用户与进程命名空间，之后挂 `/proc` 在这台手机的 4.14 内核上报 EBUSY；先建用户命名空间、再由 bwrap 建进程等命名空间能成功。具体内核缺陷没有去确认，修法不依赖它。

## 3. 改了什么

| 项目 | opt.200 | opt.201 |
|---|---|---|
| 启动方式 | 一个 bwrap：`--unshare-all --unshare-user --disable-userns …` | 两层 bwrap：外层 `--unshare-user --disable-userns --uid 0 --gid 0 --cap-add ALL --dev-bind / / --die-with-parent`，内层 `--unshare-pid --unshare-net --unshare-ipc --unshare-uts --unshare-cgroup-try --die-with-parent --new-session --cap-drop ALL --info-fd … --proc /proc --dev /dev …` |
| 不再提权 | 只靠 systemd 的 `NoNewPrivileges` | 宿主起 bwrap 前自己设 `PR_SET_NO_NEW_PRIVS`；系统 bwrap 带 setuid 位也按普通用户方式运行（虚拟机里把 bwrap 临时设成 setuid 实测） |
| bwrap 版本 | 没有 `--disable-userns` 也能起 | **低于 0.8 直接判不可用**（隔离区里禁止再开用户命名空间是硬要求） |
| 启动检查 | 8 项 | 9 项：新增"特权"——隔离区里开不了新用户命名空间、自身与 exec 出的子进程能力位都是 0。去掉外层 `--disable-userns` 或内层 `--cap-drop ALL` 这一项都会失败 |
| 没有 pidfd（4.14） | 退回"核对后直接发信号" | 同上；补了模拟 ENOSYS 的测试：后台进程收尾、宿主被杀后的冷恢复、进程号被复用不误杀 |
| 隔离区里的身份 | 宿主用户号 | 用户命名空间里的 0 号（映射到启动它的普通用户），没有能力位；个别检查"是不是 root"的工具行为会变（pip 会警告） |

两层都用 bwrap 的原因：Ubuntu 24.04 的 AppArmor 只放行 bwrap 建用户命名空间，用 `unshare` 或 Python 先建会被拒。这样手机和发行版共用一条启动路径。

为什么外层不是可利用面（独立核验实测）：外层只用来 exec 内层 bwrap；内层监视进程已清空能力，隔离区看不到外层进程，旧根已卸掉；mount、重挂、umount、setns、ptrace、chroot、mknod、写 sysctl、带文件能力和 setuid 程序 exec 后的能力位，新旧两版结果一致。

## 4. 本机证据（虚拟机，不是手机）

环境：Lima 虚拟机，Ubuntu 24.04 ARM64，内核 6.8，bubblewrap 0.9.0，AppArmor 限制开着（已装放行规则）。

| 项目 | 结果 |
|---|---|
| 模拟手机内核规则（假 bwrap：同一次同时建用户与进程命名空间就报 EBUSY） | 新启动方式起来，9 项检查全过，任务代码能跑 |
| 源码测试 | Linux 隔离区 25 项 + 宿主检查 12 项通过（含无 pidfd、模拟手机规则） |
| 只装 opt.201 wheel，`systemd-run --user -p NoNewPrivileges=yes` 跑 `python -m agent_orchestrator.host.sandbox_doctor`（Python 3.13） | `ok: true`，`no_new_privileges: 1`，9 项全过 |
| 只装 wheel，脚本模型任务（Python 3.12 / 3.13） | COMPLETED，`code_execution=sandboxed`，事件里有 bwrap 执行回执 |
| macOS Seatbelt | 没有退化（34 项通过） |
| 宿主 SIGKILL / 超时 / 取消 / setsid 两次 fork / 不可转储 | 全部收干净；冷恢复按开机号 + 启动时刻 + 命名空间认领 |

## 5. 请 AIPhone 在手机上做

1. 在最终的 systemd 用户单元里先单独跑外层命令，确认能起：
   ```sh
   bwrap --unshare-user --disable-userns --uid 0 --gid 0 --cap-add ALL --dev-bind / / --die-with-parent -- /usr/bin/true
   ```
   原因：外层 `--dev-bind / /` 会把每个子挂载补上 nosuid 再重挂，手机的挂载表比发行版复杂；旧命令在挂 proc 那一步就失败，`--disable-userns` 写 sysctl、再建第二层这一段在 4.14 上从来没跑到过。
2. 同一单元里跑 `python -m agent_orchestrator.host.sandbox_doctor`，9 项都要 `ok`。失败时把整份 JSON 交回。
3. 过了再做交接里列的其余验收：真实计数、新库启动与同库重开、越界/网络/IPC 拒绝、资源限制、`pytest:` 核验、超时/取消/SIGKILL 清理、服务与手机重启恢复、三项 DomainTool 接入、真实 DeepSeek 任务、语音并行。

## 6. 支持前提与已知限制

- bubblewrap 0.8 以上；内核允许普通用户开用户命名空间；手机需要 `/proc/sys/user/max_user_namespaces` 可读。
- 外层要能 `--dev-bind / /`：挂载表里有不能重挂 nosuid 的子挂载时外层会失败（检查命令会把 bwrap 原话带出来）。
- 隔离区里身份是 0 号。
- 内存与进程数仍是软限制，没用 cgroup。
- 仍未做：大文件/二进制产物（`artifact_read` 正文 256 KB 上限）、手机"预组装 site-packages 层"的发行方式。

## 7. 未执行

手机真机上的全部项目；真实模型任务在手机上；语音并行。这些不能用虚拟机结果抵扣。
