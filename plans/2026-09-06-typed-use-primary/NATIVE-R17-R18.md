# 原生 r17/r18：旧提醒送达与去重通过，新提醒确认后未展示

更新：2026-09-06。两轮固定 Host `55eb273d1d4987e0863f14369c244aaaaa9f0c7d`，H079/M618/S0313；依赖和安装控制见 [COMBINED-079618](COMBINED-079618.md)。使用相同原生二进制和 r14 原 userdata，经真实 CUA 输入/点击；未改数据库、时间或重造旧提醒。

## 验收结果

| 场景 | 真实观察 | 结论 |
|---|---|---|
| r17 旧青竹提醒恢复送达 | 输入“36加27等于多少？”，实际 prospective_ack 权限请求检查后允许一次；工具成功，最终回复63并显示“检查青竹测试清单” | 此场景 PASS |
| r17 下一轮去重 | 输入“44加8等于多少？”，只回答52，无重复提醒 | 此场景 PASS |
| r18 冷启动去重 | 正常退出后同原 userdata 新进程启动，输入“61减17等于多少？”，只回答44，无重复提醒 | 此场景 PASS |
| r18 新建请求回复 | 22:23银杏一次性提醒请求，前台回复“这条提醒请求可以处理”并准确复述时间与内容，没有再次否认能力 | 措辞观察通过，不等于送达 |
| r18 新提醒到期送达 | 实际时间已过22:23，输入无提醒关键词“29加18等于多少？”。新 prospective_ack 允许一次后 acknowledged，但最终只回答47；工具和英文前言也没有展示银杏提醒内容 | **FAIL：已确认但用户未见提醒正文** |

r17旧 occurrence `f1d9823558f7c3df2edf781e29766d54592e58704dcd42bad21e9bc64678ac49`，真实 receipt `9e95350325c3c7da4650e5d93d60b5b982dd1f9dba16008f525fe4973e84193f`，receipt hash `f96704a8a6852458c15a7184bd3841dfec9f48062311db356df1dedd3a76c74e`。

r18新 occurrence `4a059122dc505ffe589fbd2b9c8717f2bf326db5bd3d16b865129e6ef0d3d0ed`，真实 receipt `cbc58dbb86db79d0c5e5e22375166339319247e621a14652eedf5a28a4120fc9`，receipt hash `7f574275fe960bc471d4995327ef464578cb9f83b71f38f8593cd3d963cac5ca`。成功ACK不能替代用户实际收到提醒；此缺陷保持打开，不能删除或撤销原ACK来重复验收。

两次均需要应用内“允许一次”，不代表无人值守通知、OS通知或关闭应用后唤醒。r14/r16原失败保留；SDK安装控制中的有界恢复不能直接证明r17/r18模型实际走过repair分支，Run审计关联另补。240真实质量执行仍0，完整两组原生旅程未完成。

## 资源收尾

现场保存后均正常Cmd+Q：r17 PG22005 exit0，353.223秒，峰1,332,336KiB；r18 PG24016 exit0，664.68秒，峰1,325,456KiB。两组remaining=[]、cleanup_error=null，最低磁盘分别2345/2336MiB。未因锁屏、内存准入或超预算结束。原生源码和依赖运行期间未改变，未重跑已绿整套测试。

## 本机 ignored 证据索引

相对仓库根目录；原始证据不提交Git，以下仅小型索引和SHA-256。

- `.local-test-evidence/2026-09-06/native079618/primary-ui-9alhvxe3/01-reopened.ax.txt`：`48168c00e051dda2796982981dc830fc8f73242c37d6f962a85c399c53cd6a29`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-9alhvxe3/01-reopened.png`：`78c6031c5f21e733f1c730cb5a389f9f99c5e8c44151b16435a91b7906809d25`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-9alhvxe3/02-ack-request.ax.txt`：`0a6281a478fec730f8db64fb85d8d3f4ea299792ee8bd7308f5cf808798ab2f4`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-9alhvxe3/02-ack-request.png`：`edd4294bda478c116f5e2486bfd2fb40817e8f2378560b333ae3d7424812d11f`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-9alhvxe3/03-reminder-delivered.ax.txt`：`d41ffc9cce3cd423f90fbb1efa8e1b42b7aa8e8ec28bba728646156716942235`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-9alhvxe3/03-reminder-delivered.png`：`6d64daf04b513af2aaa865009d7e1f153fd8b9739f5a2300a5b4cd1d20e4fd5c`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-9alhvxe3/04-next-turn-no-repeat.ax.txt`：`d279d2c4c07a3440c914c31a0ac78ded238b4d2ec64ba739820a2097f31cf8ae`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-9alhvxe3/04-next-turn-no-repeat.png`：`bd6877a6cbd3f6b1492862e4340db9ab64bf9efbf035a86f882a986de1128186`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-9alhvxe3/launch.json`：`af58e831bb140ef4240c30066ab786c9429309bf1af24dce2465de226f93ca9a`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-9alhvxe3/native.log`：`81cc95e60a6a3aeba61c3ae64bad84d2785729c279d2f83d0ef7dce34f7a45a5`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-_2q6br2d/01-cold-reopened.ax.txt`：`d279d2c4c07a3440c914c31a0ac78ded238b4d2ec64ba739820a2097f31cf8ae`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-_2q6br2d/01-cold-reopened.png`：`dabfbd5ca0a934cde5f1a826ac5158dad0bb9be6c852d71914ed84eae1f8333b`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-_2q6br2d/02-cold-no-repeat.ax.txt`：`1b9765ee18cddb1060505cc28232f445ce1b5a7544157e0c964afd5d81b3d9a3`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-_2q6br2d/02-cold-no-repeat.png`：`f1cb266aeca9aa60b6dbacac22dc04fe3adf70436f8b1a4439f67500ad9de347`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-_2q6br2d/03-new-reminder-request.ax.txt`：`a062f65a5c6235cb24137f7cfe1ae9ffc08337069345d80da641020b6a73948f`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-_2q6br2d/03-new-reminder-request.png`：`92b4c7e2c8ed526fb82bfc4dbbe6d888407130dec997b9af0bee4da6c2cbbbb6`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-_2q6br2d/04-new-ack-request.ax.txt`：`359ddbb3ea12a1fb86a8124c47b5f55f11e25f9e5cb5fafe5e485fadff925381`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-_2q6br2d/04-new-ack-request.png`：`08e9ef0abbcfe9369f9e76f2d9bd1b836395f98c278ed4ad9371acecc6837dd1`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-_2q6br2d/05-ack-without-reminder.ax.txt`：`ce765ac0dbaa50055d2752f69a203aaa80606ebb73cfa924259ad0b81517f4cb`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-_2q6br2d/05-ack-without-reminder.png`：`8da1348931c382ea53b40dead974d8630539c2ec5db01672fab52b378dfd47d5`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-_2q6br2d/launch.json`：`37b73d3f82cf6e32c7a01270d8b2979f36b9a00181e2587eab197a1dae1448ae`
- `.local-test-evidence/2026-09-06/native079618/primary-ui-_2q6br2d/native.log`：`d0a589a11568a398aad0a648407e11ed5bc75235c0e77af5591b2809e5e5653a`
- `.local-test-evidence/2026-09-06/native079618/r17-reminder/resource.json`：`ee99ef5aedbb37d639b8af073dc81fbdf53a9f95841e5e7b6f76cad1761b1256`
- `.local-test-evidence/2026-09-06/native079618/r18-cold-ack/resource.json`：`4ccb7f679de695c48ca66b9c01dafadc8562869e2518e7da85c853f8eed7ecff`
