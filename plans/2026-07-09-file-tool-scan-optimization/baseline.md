# Baseline：file_glob 扫描降噪与加速

## 当前基线

执行前先记录与本切片直接相关的后端 file 工具测试。

```powershell
cd F:\projects\deskpet\backend
& 'F:\projects\deskpet\backend\.venv\Scripts\python.exe' -m pytest tests/test_deskpet_tools_file.py -q
```

## 结果

```text
27 passed in 0.70s
```

备注：当前 shell 的裸 `python` 指向 WindowsApps 占位符，无法用于本仓库测试；本轮基线使用项目 `.venv` 解释器。
