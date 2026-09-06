# HTTP 拒绝原因：保留最小安全诊断

2026-09-07。主eaa72b51/H0710/M619实际C01-20 r4 HTTP400：已读原observation-trace，唯一provider为failed/provider_request_rejected，response_json=null。SDK providers/openai_compatible.py在_post_once156先_raise_for_status，400只附status_code，不附private_cause；Host_parse_response钩子没执行，外层日志仅类别/状态。故现有r4无法恢复结构化body，不能据此断言nullable或relay根因。原r3/r4保留，不自动重发。

packet prediction_observation_complete=false时extra0是缺观测下的值，不能解释成观测到零额外类型。本叶只记录这个限制，不扩scoring状态或重测。

Host _ProductOpenAICompatibleProvider只为现有借用client.post包一层返回后诊断，不改变SDK取消、状态异常、parser或重试合同。原HTTP>=400响应在SDK抛错前，仅记录request opaque ref/status、body字节数/SHA256。body≤64KiB且JSON.error是对象时，白名单选择code/type/param/message；用原SDK SecretRedactor先脱敏再截断（128/128/256/1024字符），JSON转义控制字符。非JSON/过大响应仅hash与长度，不落完整body、headers、已注入active key或任意额外字段；不赋error body新的公共权限或语义。日志失败不替换原Provider错误。

SDK/nullable/参数/模型预算保持不变，无SDK新包。唯一新增fakeHTTP组合控制覆盖结构化、非JSON、过大400；核活跃key脱敏、边界裁剪、extra/header不入日志、原异常与单请求不变、借用client仍开启。源码0be92572/60e6ea88经过Dirac窄审无确定P0/P1；唯一新增控1PASS0.01s。未来真实错误只可通过主明确安排的单次诊断捕获，不自动发LLM。


原SDK SecretRedactor只识别已注入secret的精确值，不是未知服务器凭据识别器或完整隐私过滤器。本叶没有将body字段送入模型、公共error或质量判断；日志失败（包括日志handler抛错）不替换原错误。此fallback仅源码审阅，未另扩故障矩阵。

实际H0710 installed、Host自有source控制：默认共享锁512MiB/90s，PG77306 exit0、remaining[]、cleanup_error=null；总0.449s/峰59984KiB/最低磁盘3769MiB。三个本地HTTP400响应属于同一组合控制，remote=0；不重复nullable或旧质量测试。证据目录`.local-test-evidence/2026-09-07/provider-http-diagnostic/r1/`仅ignored。

命令（已执行，不重复绿色）：

```sh
H=/Users/denny/projects/simple_harness-corpus-clock
P=/Users/denny/projects/simple_harness-primary-candidate
PY="$P/.local-test-evidence/2026-09-06/primary-m0615/venv/bin/python"
E="$H/.local-test-evidence/2026-09-07/provider-http-diagnostic/r1"
cd "$H/backend"
PYTHONPATH="$P/.local-test-evidence/2026-09-07/harness0710-artifact/installed:$H/backend" PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 "$PY" /Users/denny/projects/simple_harness-test-resource-cleanup/scripts/run_resource_bounded.py --evidence-dir "$E" --rss-mib 512 --seconds 90 -- "$PY" -B -m pytest tests/sdk_adapters/test_provider_rejection_diagnostic.py -q -p no:cacheprovider -p pytest_asyncio.plugin --basetemp "$E/cases"
```

|本树文件|SHA-256|
|---|---|
|backend/deskpet/sdk_adapters/provider.py|4b301c2cd4d5f9aee872912a42f6b93a95410fa6b1f72c54491c1437230226dd|
|backend/tests/sdk_adapters/test_provider_rejection_diagnostic.py|0443cf29ca3c02501fa8df0d901bda2e63ccda9f4f794534ff59d5aa46a9951b|
|.local-test-evidence/2026-09-07/provider-http-diagnostic/r1/command.log|4bcefcc9f6c645923b9993ccf73ad92e6b400ca25458ea14e095857fc2131342|
|.local-test-evidence/2026-09-07/provider-http-diagnostic/r1/resource.json|d62385ae711e1d74765690dc8625eabc43cc82d561868ec5e3be8d3dbf857355|
