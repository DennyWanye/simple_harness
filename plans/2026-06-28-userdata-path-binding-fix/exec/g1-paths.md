# 任务 G1 — backend/paths.py 路径解析确定化 + 记忆化（Phase 1）

你是编码 Expert。仓库根：`G:\projects\deskpet`。只改下面列出的文件，**不要 git commit**，不要碰其他文件。
完整背景见 `G:\projects\deskpet\plans\2026-06-28-userdata-path-binding-fix\00-PLAN.md`（§2.3 是根因，§5 Phase 1 是本任务）。

## 改 `G:\projects\deskpet\backend\paths.py`

### 1. `_portable_userdata_dir()`（现 59-117 行）—— 去掉危险盘邻居回落 + 记忆化
现逻辑 `candidates_up = (1, 2, 0)`，其中 steps_up=2 会落到盘根邻居（如 `F:\userdata`），是脏数据源。改为**确定化**：
- name=="backend" → **只认** `<install_root>/userdata`（base.parent，1 级上）。
- 否则（standalone）→ 只认 `<exe_dir>/userdata`（base 本身）。
- 选定目录 `mkdir(parents=True, exist_ok=True)` + 探针写 `.deskpet-write-probe`（写完删）。可写 → 返回；不可写 → `logger.error("portable_userdata_unwritable dir=%s err=%s — falling back to AppData", candidate, e)` 后 `return None`（让 `user_data_dir()` 回落 AppData，**不再**落盘邻居）。
- **sentinel 固化**：成功后在该目录写空文件 `.deskpet-portable`（exist_ok，失败忽略）。下次进入本函数时，若 candidate 目录已存在 `.deskpet-portable`，即便探针写偶发失败也**无条件返回**它（用户意图：钉死安装目录）。
- **记忆化**：模块级 `_PORTABLE_CACHE: Path | None | "_UNSET"`（用一个哨兵区分"没算过"和"算过=None"）。首次计算后缓存结果；后续直接返回缓存。

### 2. `user_data_dir()`（现 179-202 行）保持 **env 每次优先**
不要缓存整个 `user_data_dir()`（否则破坏测试对 `DESKPET_USER_DATA_DIR` 的 monkeypatch）。保持：env(`DESKPET_USER_DATA_DIR`) 每次都先查 → 再 `_portable_userdata_dir()`（已缓存）→ dev-mode → AppData。漂移源只在 portable 探针，已被 1 的缓存消除。

### 3. 新增 `reset_path_cache()` 供测试
```python
def reset_path_cache() -> None:
    """Tests call this to clear memoized portable resolution."""
    global _PORTABLE_CACHE
    _PORTABLE_CACHE = _UNSET
```
`is_portable_mode()` 继续走 `_portable_userdata_dir()`（现在带缓存）。

## 测试 `G:\projects\deskpet\backend\tests\test_paths.py`（追加，勿删现有）
现有 28 个测试必须仍绿。新增（每个用例开头/结尾 `paths.reset_path_cache()`；用 `monkeypatch.setattr(paths.sys, "frozen", True, raising=False)` + `monkeypatch.setattr(paths.sys, "executable", str(tmp_path/"deskpet"/"backend"/"x.exe"))` 模拟 frozen 安装版）：
1. `test_portable_resolves_install_userdata_only` — frozen backend exe → 返回 `<install>/userdata`，**绝不**返回盘邻居/AppData（断言路径以 install root 结尾）。
2. `test_portable_memoized_no_drift` — 连调两次返回同一对象/路径；中途把目录设只读不影响（缓存命中）。
3. `test_portable_unwritable_falls_back_appdata_and_logs_error`（用 caplog 断言 `portable_userdata_unwritable`，且 `user_data_dir()` 落到 AppData 分支）。模拟不可写：可 monkeypatch `Path.mkdir`/`write_bytes` 抛 OSError。
4. `test_sentinel_forces_binding` — 预建 `<install>/userdata/.deskpet-portable` + 让探针写失败 → 仍返回该目录。
5. `test_env_var_always_wins_over_portable` — 设 `DESKPET_USER_DATA_DIR` → `user_data_dir()` 返回 env 值，不走 portable。

## 验收（你必须自己跑通）
```
cd G:\projects\deskpet\backend
.venv\Scripts\python.exe -m pytest tests/test_paths.py -q
```
全绿才算完成。跑 ruff（若仓库用）：`.venv\Scripts\python.exe -m ruff check paths.py tests/test_paths.py` 应无新增错误。完成后输出你改了哪些行 + 测试结果摘要。
