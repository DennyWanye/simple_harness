# impact_paths 草案

状态：有意留空，待实现轨冻结变更清单后由 gate owner 填写。

black-box 准备轨不读取实现代码或 diff，无法可靠声明代码路径 glob。错误映射可能漏掉应复测场景，
因此当前全部 scenario 的 `impact_paths` 都不声明，按 fail-closed 规则执行 critical + affected + full
surface smoke 与全部 required scenarios。

在 phase-4 init 前，如果独立 impact mapping owner 能从冻结提交生成完整且可审计的路径映射，可追加到
manifest 草案；否则继续全量复测，不得凭猜测缩窄。
