# TaskGraph 主体阶段结果（2026-09-23）

状态：主体阶段测试 PASS；独立交接核验 PASS；2026-09-23 02:47 CST已成功通知第三部分（01a0c5e5-ad91-71a2-930f-9d7ddbc66600）接入开发。用户2026-09-23限定的小规模主体检查已完成，原完整TaskGraph验收并未通过或取消。

## 范围与结果

| 项目 | 实际结果 | 边界 |
|---|---|---|
| 主体代码 | TG-A–E生产入口静态闭合，独立只读挑战及本轮实际故障修复完成 | 不把静态审查当全场景覆盖 |
| 原Host真实DeepSeeker | Mission `mission-24dfcf57110f9dbe` 正式COMPLETED；107.08秒、11物理调用、49646 tokens、0未知；实际模型请求/回显均 `deepseek-v4.1-flash` | 一条CONTENT_ONLY单文件Mission，单并发；含规划、显式授权、原APPLIED、Worker、pytest、叶/根评审及最终Mission检查 |
| 真实交付 | NOTES.md VERIFIED，230 bytes，SHA-256 `50b9c1313421460f5d835711670d7dc76e2823c7976a7fcfc710391e8640c719` | 校验实际产物字节，不仅模型自述 |
| SDK/Host只读与冷恢复 | snapshot/why/diff/convergence + 同候选rebuild通过；前后1 Attempt/6 intents/106 events/1 revision/11 calls完全相同 | 不包含大规模、跨版本升级或统计测试 |
| 基本原生图界面 | 真Tauri点击通过当前图、两视图切换、SVG节点原因、节点/根结果展开、历史只读、1→1零差异、空收敛、错误版本保留旧图及返回当前；深色节点/提示可读 | 原HTN Tauri二进制载体 + 当前candidate23 backend/installed wheel +最终Host UI2源；不是新打包安装器或完整UI矩阵 |
| UI读取副作用 | native启动先消费原8条尾部followup（107–114）；正式UI检查前后1 Attempt/6 intents/114 events/1 revision/11 calls不变，0新增模型调用 | 启动后台收尾与只读按钮的副作用严格分开 |
| 定点故障 | 复用candidate19已具名的原Worker响应后强退/Host UNKNOWN冷恢复；新增来源证据及Mission judge身份定点检查 | 未重跑批量单测或全部强退矩阵 |

## 本阶段实际发现并修复

1. 根评审没有拿到已通过的原测试回执：加入有界、身份闭合的原VerificationPassed事件摘要；未绑定scope标UNBOUND，独立评审不被替代。
2. Mission judge的view ID误作Attempt ID：限定原critic、正整数judge ordinal、原注册workspace/预算事件与artifact producer身份，并继续核fence。
3. 新图与相邻面板React key重复：document/taskgraph/diagnostics各自独立key，原生复测只出现一个图。
4. 深色主题节点白底浅字：改用已有--sh-surface/--sh-warning变量，截图确认可读。

SDK最终 `taskgraph.23`，598包文件；wheel `e1d34a46bcabb43d82be39bf8bb39755838d29993e81f1e1b95b92507e004c77`，manifest `185debc0f62edffb417dcff9419bc53bb9d59db9b819692d07b518efa0ecde5d`，deployment `331a8b947bcc5be329d971da6a0937188fee711e4d39cb7ed6995200bd2d35ea`。最终Host增量位于 `candidates/taskgraph-23-host-ui2/host`，运行前端 `tauri-app/`；SDK/backend没有因CSS/key修复重跑模型。

## 失败保留及后置门

candidate19无seed与doc错误场景、seeded根证据缺失、candidate22最终judge误挡均保留为失败；candidate22 9calls/40754tokens/0unknown。candidate19-seeded 24calls/131337已知tokens/1unknown，不能冲销。candidate20/21未真实测试，不继承PASS。native前两次分别缺资源包/安装来源不匹配，补隔离环境；第三次暴露重复key和对比度。一次复制错误多拷了node_modules导致磁盘耗尽，只删除当次新建的冗余依赖副本，未删除原证据或共享cache；端口遗留只清理自有PID/进程组。当前自有native/backend/Vite已退出。

重型42组完整矩阵、mutation、stateful、全H1/legacy回归、多领域/多seed/统计/性能、完整原生矩阵全部DEFERRED_BY_USER；原TaskGraph acceptance manifest保持NOT_RUN。当前阶段PASS只允许第三部分在此源码基础上接入，不代表三部分整体产品验收。

共享Host/HTN、依赖与DB未替换，未发布或提交；取消的自动化未重建。

## 本地证据索引

原始证据根 `.local-test-evidence/2026-09-23/taskgraph/`。Git仅保留此文字索引和摘要。

| 相对路径 | SHA-256 |
|---|---|
| `real-main-c23/result.json` | `5eb5fb303cecf8f2576bb3a308d589d5c33a22cb46b1d2266c974f3c01ce73dd` |
| `real-main-c23/current-snapshot.json` | `22e3f95424b50a0128d95ca00ab8f400dbf1bddd1a711a894414d3b40b025740` |
| `real-main-c23/historical-snapshot.json` | `557b1351dccd033e376ae57650ecfdaf0b0de5fb51f666665c3180fbd4a75d21` |
| `real-main-c23/run.log` | `5d6bc253394024c2316a27564d34a8a9d7845c72978364265f87c85d45316f13` |
| `native-main-c23-ui1/result.json` | `1d089e6f9a79def3d569889b33b7e0c0b224ebda79acb8a750d231e2f4f0e45b` |
| `native-main-c23-ui1/ui2-current-visible.png` | `249bd4eec997bbeb56a5b2a231cf32e48aefbedd88d5855f78d0a35e8a6d1086` |
| `native-main-c23-ui1/ui2-invalid-history.png` | `05f6e0148947af38a195a00263972b3db8fdd42a3e1118fa87136280d5b9872a` |
| `native-main-c23-ui1/ui2-restored-current.ax.txt` | `bdb1cbb6b3e0e586bab928165251ed7a1eaee535aab94e572326df82a33c0348` |
| `native-main-c23-ui1/native.log` | `b2f8c89c4c9a260a81d5c08aafb3258e9b389996681ee13ec55235f1e5585bac` |
| `native-main-c23-ui1/vite-ui2.log` | `eda9c8975a7a090675c5a972d5ea6bc2d1538f64a149493df8bccc5117c79363` |
| `real-main-c22/failure-summary.json` | `977127c3410e2b1bd27d4fb1e635184864e5f3db21c1cbd0220b4a76f55accec` |
| `real-main-c19-seeded/failure-summary.json` | `3dd17db1cf21f1d99e21bc4b43701a3f0619b2f5c95759d9b64849ff684620c3` |

最后字节核验：真实Mission使用的candidate23/env与原生vendor安装来源的native-env，598包文件逐字节均匹配同一wheel；本地证据`final-installed-identity.json`。源码不因安装来源元数据不同而混用；前端UI2的256源文件hash保持不变。

独立交接评估：full / round1 / PASS，无block；评估原文`.local-test-evidence/2026-09-23/taskgraph/handoff-evaluation-r1.json`，SHA-256 `1261edbc7791eeab2fa16b0b145c8eacd020b556d4c55c4c002d877a639c8e94`。评估员独立提取CUA原始配对38步/0未配对并看图，提取SHA-256 `15c14776e9a1fc51765fd1139706673d1c132b1137280cc0b38791e311c9530e`。
