# H073 / M0613 runner successor

状态：本叶子必要验证完成；两格public仅OBSERVED，不授正式PASS；原401/S3整体未完成。
基线 `60f280dc2dd674a2af42e517f9c0d04d46aec72c`；独立 `feat/typed-recall-0613`。
原 runner 树的6 tracked +1 untracked applicability WIP 未复制、未编辑；原三格仍由缺失 executor明确 BLOCKED。

## 变更与接口

CLI 不变；主资源入口包裹 runner/pytest，180秒、2GiB、单进程组串行。
fixture revision8/layers revision6（文件内为准），候选身份和摘要显式后继；旧pin和前序两个文件hash保留。
不更改原401、391public+10source、14攻击、128byte或业务oracle/阈值。
observe模式对public/source一律不得PASS，包括匹配pin的显式observe；FAIL保留。
新增三个两层传输反例（同pin、异pin、保留真实判定FAIL）和原业务字段逐项不变回归，均不是产品401验收。

## 固定候选

- Harness0.7.3 source `0282fa982995b24bc893fdf6bed69d2caacd6587`，wheel SHA256 `1a9ed5c95e6cddd4e0ccd85124320a6001008740a53213712fc89f3467cb4cd7`。
- Memory0.6.13 source `f2a6a706c5e3407e896ada3bd9e735cd9c0b77fd`，wheel SHA256 `33fcc494f0cb8c9f358e411b372d4dcdc423725563dda6f5b96684f5fa1ffd62`。
- wheel使用两SDK owner原制品，不重建。public独立installed，禁止source overlay。
- source10若后续执行需M0613 exact source clean checkout；本批不以public替代source。

## 已执行范围

必要桥接反例及原场景/阈值不变检查；随后仅两原格 `eligibility/not-suppressed` 与
`eligibility/ordinary-uncontested`，使用 `--observe-candidate`。
记录真实OBSERVED、business assertions、FAIL/BLOCKED，不授正式PASS；其余399为NOT_SELECTED。
原始日志保存在 `.local-test-evidence/2026-09-06/typed-recall-0613/`，不入Git。
旧401历史178/0/223不覆写，当前无新401结果。无模型/native/全量/机器gate。

## 实际资源接口

使用固定资源入口 `145baed3940d0a69aab1e38918efafa975e6afba` 的
`scripts/run_resource_bounded.py --evidence-dir <fresh-local-dir> --rss-mib 2048 --seconds 180 -- <command>`。
不覆盖 `--lock-file`，使用默认同一OS锁 `/Users/denny/.cache/simple_harness/test-resource.lock`。
Singer释放后经主授权串行执行；现两批进程组均已退出、共享锁释放，不再启动测试。

主审一致性补充：observe下非FAIL层显式 `status=BLOCKED`、
`reason=CANDIDATE_OBSERVATION_ONLY`，不是仅清空passed_cells。
新增断言同时约束summary、两层、401cells均无正式PASS；原FAIL不降级。已由下述必要测试验证。

## 2026-09-06 实际结果

1. 必要测试：`python -m pytest testcase/human-memory-program/tests/test_typed_recall_bridge.py testcase/human-memory-program/tests/test_typed_recall_a2_oracle.py -k 'two_layer_dispatch or 0613_successor or revision_preserves' -q -p no:cacheprovider`。
   **12passed /58deselected /0.25s**；10 transport反例/邻居+1candidate lineage+1原阈值/场景保持。
   transport测试的401合成响应仅验证runner，不是401产品运行或机器gate。
2. actual public：`run_typed_recall_public_consumer.py --consumer-python <M0613 ownvenv> --observe-candidate --cell eligibility/not-suppressed --cell eligibility/ordinary-uncontested --child-timeout 120`，exact wheel/source参数如上。
   两格均OBSERVED，原whole-item资格、完整source/projection/evidence/rank、publichash/result、durable exact replay零candidate读取四组断言通过。
   **正式0PASS/0FAIL/2BLOCKED**，reason=CANDIDATE_OBSERVATION_ONLY；其余399本次未选，source10未跑。
   summary NOT_RUN/BLOCKED，public层BLOCKED。exit3为诚实观察结果，非运行失败。
3. 两命令均由145baed3默认共享OS锁入口包裹，2048MiB/180s。
   pytest组28118 exit0，0.442s，峰值45392KiB；public组28141 exit3，0.876s，峰值130256KiB。
   两组remaining_group_members=[]、cleanup_error=null、stop_reason=null，测试槽已释放。
4. 借用M0613 artifact ownvenv（Python3.12.13），实际worker `-I`隔离；H073/M0613包源均site-packages。
   runner逐字节验证H164/M72包成员；这与此前含dist-info的H169/M75统计口径不同，不相加。
   无SDK source overlay、私有SQL/public混层、模型/native、source10、全套或新增wheel。

## 原始证据索引

resource.json记录进程组/RSS/退出事实，不含argv。命令参数见上文及下列复现命令；原始输出和DB只留本机ignored目录。
- `.local-test-evidence/2026-09-06/typed-recall-0613/bridge/command.log` SHA256 `910a779ef053567632118847229a6fa772718412cd13c8a69c33bb401ef59c7c`
- `.local-test-evidence/2026-09-06/typed-recall-0613/bridge/resource.json` SHA256 `2b9de35ac9ec3c81ca76bd970def31bd471aad9d75d26f4d310f2da2a436be23`
- `.local-test-evidence/2026-09-06/typed-recall-0613/public-resource/command.log` SHA256 `4ac9496e839eb2b93cb0ab5f072a80e6944267eb4ce832cef5898b27e524da3d`
- `.local-test-evidence/2026-09-06/typed-recall-0613/public-resource/resource.json` SHA256 `a94aa979b37b7906c7bd9f00e82b460d6030efad371af4f62457b4454bd56776`
- `.local-test-evidence/2026-09-06/typed-recall-0613/public/bridge-summary.json` SHA256 `38d6f003f148476d9bafa010b626a97159a2a7d21d9754e8200df792609ecf41`
- `.local-test-evidence/2026-09-06/typed-recall-0613/public/public-runtime.json` SHA256 `6b578a1253bb7b531e6382f98fdb86d6e0bcea12f0cf8e0a0e3f414d28275ef0`
- `.local-test-evidence/2026-09-06/typed-recall-0613/public/public-observations.json` SHA256 `5bb79418eb82115bacc4cbb33422fb92361856cae97d3b0be4339c5fb61ed521`

## 两格复现命令（必须另用fresh evidence目录；本轮不再运行）

```sh
consumer=/Users/denny/projects/simple-harness-memory-sdk-typed-short-sources/.local-test-evidence/2026-09-06/typed-short-sources/artifact/venv/bin/python
"$consumer" /Users/denny/projects/simple_harness-primary-candidate/scripts/run_resource_bounded.py --evidence-dir .local-test-evidence/2026-09-06/typed-recall-0613/reproduce-resource --rss-mib 2048 --seconds 180 -- "$consumer" -B testcase/human-memory-program/runners/run_typed_recall_public_consumer.py --consumer-python "$consumer" --artifact-dir .local-test-evidence/2026-09-06/typed-recall-0613/reproduce-public --harness-wheel /Users/denny/projects/simple-harness-sdk-operation-audit/.local-test-evidence/2026-09-06/run-operation-audit-073/build1/simple_harness_sdk-0.7.3-py3-none-any.whl --harness-wheel-sha256 1a9ed5c95e6cddd4e0ccd85124320a6001008740a53213712fc89f3467cb4cd7 --harness-source-commit 0282fa982995b24bc893fdf6bed69d2caacd6587 --memory-wheel /Users/denny/projects/simple-harness-memory-sdk-typed-short-sources/.local-test-evidence/2026-09-06/typed-short-sources/artifact/build1/simple_harness_memory_sdk-0.6.13-py3-none-any.whl --memory-wheel-sha256 33fcc494f0cb8c9f358e411b372d4dcdc423725563dda6f5b96684f5fa1ffd62 --memory-source-commit f2a6a706c5e3407e896ada3bd9e735cd9c0b77fd --observe-candidate --cell eligibility/not-suppressed --cell eligibility/ordinary-uncontested --child-timeout 120
```
