# 任务 G5 — PyInstaller spec 钉死 keyring frozen 后端（Phase 6）

你是编码 Expert。仓库根：`G:\projects\deskpet`。只改 1 个文件，**不要 git commit**。
背景见 `G:\projects\deskpet\plans\2026-06-28-userdata-path-binding-fix\00-PLAN.md` §2.5 + §5 Phase 6。

## 问题
`backend\deskpet-backend.spec` 的 `hiddenimports` 没有显式列 `keyring` 及其 Windows 后端。keyring 的后端走 entry-point 动态加载，PyInstaller 静态分析看不到 → frozen 版**有概率**整个 keyring 失效（`keyring.get_password` 静默返回 None / 抛错），导致 provider api_key 读写不可靠。属定时炸弹，钉死。

## 改 `G:\projects\deskpet\backend\deskpet-backend.spec`
在 hiddenimports 组装区（约 36-79 行，`hiddenimports += [...]` 那一段）追加：
```python
# 2026-06-28: keyring 后端走 entry-point 动态加载，PyInstaller 静态分析
# 看不到 → frozen 版可能整个 keyring 失效（provider api_key 读写不可靠）。
# 显式钉死 keyring + Windows 凭据后端及其底层 win32ctypes。
hiddenimports += collect_submodules("keyring")
hiddenimports += [
    "keyring.backends.Windows",
    "keyring.backends.null",
    "keyring.backends.fail",
    "win32ctypes.core",
    "win32ctypes.pywin32",
    "win32ctypes.core.cffi",
    "win32ctypes.core.ctypes",
]
```
注意：
- `collect_submodules` 已在文件顶部 import，直接用。
- 用 try/except 容错并非必须，但**若某子模块在当前 venv 不存在会导致 spec 解析报错**，请改成稳健写法：对 `win32ctypes.*` 这种可能缺失的，用
```python
import importlib.util as _ilu
for _m in ["win32ctypes.core", "win32ctypes.pywin32", "keyring.backends.Windows"]:
    if _ilu.find_spec(_m) is not None:
        hiddenimports.append(_m)
```
确保即使本机缺某依赖，spec 仍能正常被 PyInstaller 加载（不阻塞构建），存在的才加入。`collect_submodules("keyring")` 同样用 try/except 包住，缺失则跳过并 `print("[spec] WARN: keyring not importable, skip")`。

## 验收（自己跑通）
spec 是 Python 文件，验证它能被正常 exec / 不报语法错误：
```
cd G:\projects\deskpet\backend
.venv\Scripts\python.exe -c "import ast; ast.parse(open('deskpet-backend.spec',encoding='utf-8').read()); print('SPEC_PARSE_OK')"
.venv\Scripts\python.exe -c "import keyring, keyring.backends.Windows; print('keyring import ok')"
```
（不要求真跑 PyInstaller 全量打包——太慢。能 ast.parse + keyring 可 import 即可。）确认 `collect_submodules("keyring")` 在本机能返回非空列表（说明 keyring 已装）。完成后输出改动摘要 + 验证结果。
