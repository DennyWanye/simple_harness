# Windows identity probe 调用说明

Windows 是 TC-PS-01 / TO-R5 的 release stop gate。必须在 Windows 10/11 真机、当前提交态候选上运行计划中
checked-in 的 `verification/spikes/windows_path_identity_probe.py`；macOS
结果不能替代。

```powershell
$EvidenceDir = ".local-test-evidence\2026-08-25\project-sessions\windows"
New-Item -ItemType Directory -Force $EvidenceDir | Out-Null
py -3 verification/spikes/windows_path_identity_probe.py --kind project |
  Tee-Object "$EvidenceDir\project-identity.json"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
py -3 verification/spikes/windows_path_identity_probe.py --kind explicit |
  Tee-Object "$EvidenceDir\explicit-identity.json"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

要求输出 JSON 至少逐项报告：大小写/`.`/`..`/分隔符等价表示、同卷 rename、不同目录、Project root、explicit
execution root，以及可选 junction。等价表示与 rename 必须 identity 相同；不同目录必须不同。junction 若平台或权限
不支持可明确 `SKIP`，其余断言任一失败都停止交付。输出不得包含凭据；记录脚本 SHA-256、OS build、Python 版本、
原始 JSON SHA-256 和退出码。
