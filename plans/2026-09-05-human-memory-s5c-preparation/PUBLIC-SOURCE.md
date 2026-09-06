# 提醒注册接入公开来源事实

最后更新：2026-09-06。Host 产品源码 `8a91f371`，实际消费已安装 H0.7.5 / M0.6.16；注册来源和恢复局部通过，完整 scheduler 尚未完成。源事实接口/新制品已获 Dirac 限定 ACCEPT；本 Host 固定源码只读未发现新增 P0/P1，追加测试结果待复核。

`PublicRegistrationAuthoritySource` 调用 Memory 公开 `read_prospective_outbox_source`，核对实际 outbox 的身份、payload、创建时间和幂等键，使用真实历史目标的 Run、operation、mutation receipt。目标 receipt 与 outbox 生成原因分开；`outbox_cause_status=not_persisted` 不被改写为有因果证明。失效命令复用已经 ACK 的原 Host registration。首次授权与 cursor 在同一个 Host 事务中持久化后返回，并发返回实际先提交者；重试不续期。

验证仅运行新增控制，没有重跑旧 SDK/Host 绿色集合，也未新增虚拟环境。隔离 Python 借用既有通用依赖，同时导入 H075 和 M616 各自已安装 target，检查实际消费 wheel SHA 与已加载模块来源。Host 仍是源码输入，不声称完整依赖重解、生产切换或 native 测试。

| 批次 | 结果 |
| --- | --- |
| r1 | 5 PASS、2 FAIL，3.18 秒。注册来源/伪造时间与幂等键拒绝、并发、Memory 提交后丢 ACK、Host 首提交前后故障通过；两项改期 fixture 误用字符串代替 SDK 生命周期枚举，尚未执行改期。 |
| r2 | 仅修 fixture 枚举后重跑上述两项：2 PASS、5 deselected，1.04 秒。实际公开 REVISE 产生 invalidation；未 ACK 拒绝且 cursor 不动，已 ACK 则复用原 registration ref，真实 Memory 回签成功。 |

因此本集合 **7 项均已有通过结果**；没有把 r2 当第二轮全量。r1/r2 峰值分别 187232/183248 KiB，PGID47870/47913 全部回收；最低磁盘 3081MiB。无模型、网络 Provider、native 或 401/240 执行。

安装身份：H075 source `abbb0fd707f2ceadb271471da2ef906c27748420`，wheel `7969a2e5028f2c5d0b348973a5b330bca797f10c1bdfa2532ae033d352d2ee66`；M616 source `931b8c77076bb5b42ad41a3297ed4eb58bcaaab9`，wheel `00937eb5d79c1ea989112c658eaf543e434fb211106f4edfbecbc10002bca9bf`。均为已有新 target，未修改冻结旧环境。

命令：既有 `primary-m0615/venv/bin/python` 通过本树 `scripts/run_resource_bounded.py --evidence-dir <batch> -- <python> -I -B .local-test-evidence/2026-09-06/prospective-source/run_installed.py <batch>`；r2 追加 `-k actual_invalidation`。脚本仅运行 `backend/tests/memory/test_prospective_registration_source.py`。

原始文件保留本树 `.local-test-evidence/2026-09-06/prospective-source/`，不进入 Git：

| 相对路径 | SHA-256 |
| --- | --- |
| r1/command.log | 7727cb5c0b1ac74d2230c48bf894c82e00a0161f839beb5a940017d0e00ba833 |
| r1/resource.json | dd8e7284b7398924593825b8a48a70d53a072528ec49a14896bcf0cc24bb871d |
| r1/identity.json | 9e6f573d1c1dad6db0dfd8ecbfcc76df98ed7de13adb1ab9430a1812bf9d909f |
| r2/command.log | ff7d4b50f1769958287f7171bb0e5cb858de522564919f9411b6f3cef456e774 |
| r2/resource.json | 9f795c7a19fec1607e51016d9f5fa3df221561079f1ac2a0b4de506a3fcd167d |
| r2/identity.json | 9e6f573d1c1dad6db0dfd8ecbfcc76df98ed7de13adb1ab9430a1812bf9d909f |

剩余：未消费即过期的 grant 恢复策略、signal 派生 revision 的真实来源、唯一 scheduler/到期事件/occurrence 与上下文及 ack 接线、Host 持久接收 SDK 新操作观察记录。默认 Host 仍 schema49，未启用半成品 scheduler；S5c 整片及原 program 未完成。
