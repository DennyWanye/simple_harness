# 新ACK提醒条目：源码与必要控制

更新：2026-09-06。独立 `feat/prospective-ack-user-notice`，base `55eb273d`；产品 `1355c5b7`，测试后继 `a308fc60`。H079/M618/S0313固定制品未改、主候选/原userdata未改。本叶默认生产service接线，无新通知ledger/SDK API；[契约](CONTRACT.md)、[原生审计摘要](NATIVE-AUDIT.md)。

## 行为和边界

新ACK在原同事务proof中附versioned marker。UI通过真实Host primary page/detail读出独立 `reminder` 角色与稳定notice_id，正文取公开occurrence.action_text；实际模型可以只回答47。SDK messages/terminal S1不改，此条目不作为USER/assistant或新short来源写入。旧无markerACK仍精确重放原hash，不补发r17/r18旧通知。

读取核真实owner/Run/presentation/snapshot/ACK/exit，已有终态必须与public SDK terminal及settled proof精确一致。ACK后SDK尚无终态或随后FAILED也能读出同条目，不伪造成功终态。来源来自public mutation/signal outbox及真实S1；公共inbox/最终history visibility拒绝当前suppression。合法REVISE造成current-head hash变化时撤下旧notice、旧detail明确unavailable，普通history可读；origin/action篡改仍拒绝。

仅当前已登录SELF USER_REVIEW页面。server projection、组件渲染和真人已见分开；没有OS通知、无人值守送达或显示已读回执。原r18真实FAIL保留，后继新组合native另验。已有page/detail批次仍有跨库时窗；本批forget是两次读之间的真实改变，不宣称在途并发原子保证。Memory source/inbox扫描上限及成本见契约，不宣称P99已达标。

## 实际分批结果

- backend-r1：**6 PASS / 1 FAIL，13.37s**。正向真实public mutation→timer→Harness tool ACK→final47，公开page/detail独立提醒；实际ACK后终态前page、Provider400后的FAILED；重复读/新service+Memory handle重开稳定；旧ACK不补发；memory-only及evidence suppression使page无正文/旧detail `primary_message_unavailable`；foreign ACK与错误public terminal拒绝。原失败日志保留。
- 首轮唯一红是新增REVISE fixture使用了occurrence的原revision，SDK正确拒绝 `cognitive_target_revision_stale`。`a308fc60`仅捕获真实timer public apply结果的 `committed_revision` 作后续exact target，不改产品/阈值/SDK。
- backend-r2：只原红 **1 PASS / 6 deselected，2.35s**。真实REVISE改期/正文后currenthead hash变更，原occurrence/action固定；page仍有普通USER、无旧notice，旧detail精确 `primary_message_unavailable`，ACK/terminal commitments未改。六绿未重跑。后端合计七个唯一新场景。
- ui-r1：**2 PASS / 9既有例未选中，44ms测试体/902ms总duration**。真实组件消费typed wire，显示独立“提醒”与正文且模型47保留；display invalidation立即撤下；缺notice_id不显示。这是jsdom组件控制，非原生/全量typecheck。

重开控制限定新Host service对象及Memory manager handle，SDK stack未冷重建；不得称整个native冷启动送达PASS。真机r18旧失败不因本批绿色改写。Dirac源码挑战P1已修；最后固定源与分批结果终审待回。

## 资源和载体

默认共享锁，2GiB/180s、1GiB磁盘准入/256MiB停止，无override。PG34308（首红批）exit1/14.064s/峰260992KiB；PG34476（UI）exit0/1.292s/峰350848KiB；PG34505（唯一红重验）exit0/3.001s/峰219072KiB。三组均remaining=[]、cleanup_error=null、无强制资源停止；最低磁盘3188MiB。已释放给Carver，不再重复绿色。

复用主 generic Python `.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python` 及已安装 H079/M618/S0313小target `.local-test-evidence/2026-09-06/primary-079618/installed`；本叶Host源码由限定carrier载入。没有建立新venv、安装或重做旧全成员identity核验，不声称本树独立install。UI借用主node_modules，只在本叶ignored目录放缓存。

复跑命令（在本叶根；每次选未存在的证据目录）：

```sh
ROOT=/Users/denny/projects/simple_harness-typed-recall-context-use-full
PRIMARY=/Users/denny/projects/simple_harness-primary-candidate
PY="$PRIMARY/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python"
EVIDENCE="$ROOT/.local-test-evidence/2026-09-06/prospective-ack-notice/backend-new"
"$PY" -I -B "$PRIMARY/scripts/run_resource_bounded.py" --evidence-dir "$EVIDENCE" -- "$PY" -I -B "$ROOT/.local-test-evidence/2026-09-06/prospective-ack-notice/run_backend.py" "$EVIDENCE/cases"
# 仅重验改期：在上述命令末尾添加 -k legal_public_reschedule
"$PY" -I -B "$PRIMARY/scripts/run_resource_bounded.py" --evidence-dir "$ROOT/.local-test-evidence/2026-09-06/prospective-ack-notice/ui-new" -- /opt/homebrew/bin/node "$PRIMARY/tauri-app/node_modules/vitest/vitest.mjs" run --config "$ROOT/.local-test-evidence/2026-09-06/prospective-ack-notice/vitest.config.mjs" src/views/PrimaryChatView.test.tsx -t 'renders a typed Host reminder|rejects reminder text without' --maxWorkers=1
```

## 本机ignored索引

以下相对本叶根，SHA-256；原始日志和fixture DB不入Git。

- `.local-test-evidence/2026-09-06/prospective-ack-notice/run_backend.py`：`7b346d9620df7098128245fee53b28a0e04c07608d6c517d163ac7b500aeaa0d`。
- `.local-test-evidence/2026-09-06/prospective-ack-notice/vitest.config.mjs`：`0b303beb071015037e2aa751a43626caa1bf450900b7c53ac62f8ba42dc0c7e6`。
- `.local-test-evidence/2026-09-06/prospective-ack-notice/backend-r1/command.log`：`3425e8bc9efd5e0cb1443b64974b173fa400d3d49ea9110ce7b1dd37299e2e29`。
- `.local-test-evidence/2026-09-06/prospective-ack-notice/backend-r1/resource.json`：`d224ca8ade8e3985a2f54cfae57a3a31be6a7d64a5d9018d29c4a9c8f279d126`。
- `.local-test-evidence/2026-09-06/prospective-ack-notice/ui-r1/command.log`：`0cf236fd4f8da253664a6c5ab27b369b6f748fce90da548068bd3f382bb39cc8`。
- `.local-test-evidence/2026-09-06/prospective-ack-notice/ui-r1/resource.json`：`4cefa2d356695f72e251a6d8970856e4590396de4c9f7f4a511f4d1959686904`。
- `.local-test-evidence/2026-09-06/prospective-ack-notice/backend-r2/command.log`：`98de67add2f1919f2f8eb38816c29e29fc648ef1c341db9287ac2cc30427c53f`。
- `.local-test-evidence/2026-09-06/prospective-ack-notice/backend-r2/resource.json`：`f7db5beb4235b7adea76f1380b83dda91d35774380d2bc6538b34a216d17a9be`。
