# H078/M617 Host 驱动选择组合

更新：2026-09-06。Host源码 `a0a44485`，H078 source `f778cba9c5ee599e7ff5ac55796d0f331d215f62`。SDK源审13abfe8限定ACCEPT；制品/Host独立审查另记录。本片已完成必要安装及Host构造验证，新的原生Run尚未执行。

## 真实问题与修复

r12原生Run `395d23b0-91ac-5e57-bfef-a26f5736aa70` 的默认后台审计确实完成98/98公开DTO条目，状态enumerated，没有现有规则finding；但公开metadata明确 `recording_coverage=unverified`，缺口 `driver_or_uow_recording_unverified`，历史partial。不能将“读完”或“无finding”写成全部操作已记录。其审计job `fc075bccd3964986e78b6f018ff6a920d0fb936d4f02f05617902b6507948898`。

原因是Host以ProductRootDriverRouter包装标准ReActDriver，SDK无法核验所执行的具体driver。后继用SDK自己的不可变StartModeDriverRouter按持久start_mode选择实际driver；普通Run直接进入原ReAct，host_control仍进入原Host验证路由，原精确authority检查不变。自定义/子类/不透明driver仍unverified，无自报contract绕过。旧r12未验证区间不追认。

r12审计只读诊断在应用退出且无WAL时复制，原库/副本SHA均 `2ee7c0604002b7317133f44ad822353ac168e491c4e5dc0ed9583b45f797882e`。本机索引 `.local-test-evidence/2026-09-06/native077617/audit-r12-inspect/`；首次原库mode=ro查询不能打开，后使用固定副本immutable只读，未写入原库。

## 固定候选与实际验证

- H078 wheel `simple_harness_sdk-0.7.8-py3-none-any.whl`，SHA `5aa1112803b5b617142b015f4989c679b455f34444f512a4ede957161b41138e`。
- manifest SHA `6a4292634fa0666593d73aba876aff223ef043687c82ec3a864310e3ae8013b6`；执行schema仍9。
- M617及S0313制品不变。旧077/旧target/原生userdata保留。
- SDK一次离线构建；4个新真实Runtime场景4PASS/0.34s。安装后普通Runtime、API精确快照、077导出保留3项PASS/0.41s，173package成员与wheel一致，128模块全来自新target；不是重复全suite。
- Host同existing Python建立小target `primary-078617/installed`，无新venv/模型。实际main factory、原Host验证分派正确/错误授权、新锁与manifest关联、候选身份共4PASS/1.91s。173/84/116个H/M/S包成员与各vendor wheel逐字节一致，178已加载模块全部来自该target。
- PG99568安装及PG99586测试均正常退出、remaining=[]/cleanup_error=null，共享锁释放；最后最低磁盘4973MiB。SDK两个group99195/99316同样清空。

命令：`<primary-m0615/venv/bin/python> scripts/run_resource_bounded.py --evidence-dir <本根>/install-resource -- <同Python> -I -B <本根>/install.py`；随后新目录r1、子脚本 `run_controls.py <本根>/r1`，均默认共享锁/2048MiB/180秒。

新原生24轮旅程、完整A7、Procedure、全操作覆盖和240质量均未因此关闭。用户主checkout未切换，未push/tag/release。

## 本地证据

原始根 `.local-test-evidence/2026-09-06/primary-078617/`，仅本结论及真实vendor制品进入Git。

| 文件 | SHA-256 |
|---|---|
| install.py | 2b72a70de19f2353bcdce449eb284828b57c281d1b4972e6a2cf4d04289cb50d |
| run_controls.py | 3fc18fc9244790714698dfcb19da0bdaea3fe35f7c217338edefbc806b90dbaa |
| install-resource/resource.json | a1026b6fa74d0f26a6753133af60a6162178ee1e216bbe37d93eff0aa2f33e10 |
| r1/command.log | fbe7f61d13a0aebf048eaadc1eda7c2d1283f3761254f635e8bcc4933c808ff2 |
| r1/resource.json | 3215dcdf208e464860367136a008e3c09860569abffc7be1335da4eb6e48866a |
| r1/identity.json | 0436f114afe20acaa3d9b21d5e5ab2fe67f8b57d9e0b89c776cf52a78de1141e |
