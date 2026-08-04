# 任务 G2 — backend/config.py 存量装机自愈迁移（Phase 3）

你是编码 Expert。仓库根：`G:\projects\deskpet`。只改下面文件，**不要 git commit**，不碰其他文件。
背景见 `G:\projects\deskpet\plans\2026-06-28-userdata-path-binding-fix\00-PLAN.md` §2（根因）+ §5 Phase 3。

## 问题
relay-cloud LLM provider 是登录时写进 `<user_data>/config.toml` 的 `[[llm.endpoints]]`。历史路径漂移 bug 导致它可能被写到**另一个** userdata 目录（如 `%AppData%\deskpet\config.toml`），而现在运行的 backend 读的是 portable `<install>/userdata\config.toml`（没有该 endpoint）→ `get_chain()` 空 → 聊天报空 Bearer。需要：启动时若 canonical config 没有可用 endpoint，就从其他已知候选位把 enabled 的 endpoints 迁回来，让存量用户**无需重登**即恢复。

## 改 `G:\projects\deskpet\backend\config.py`

新增函数 `_recover_orphaned_endpoints(canonical_path: Path) -> bool`（放在 `seed_user_config_if_missing` 之后、`resolve_config_path` 之前）：

逻辑：
1. 读 canonical_path（tomli）。若其 `llm.endpoints` 是非空 list 且**至少有一个 `enabled != False`** → 直接 `return False`（已健康，幂等 no-op）。
2. 枚举候选源路径（去重、排除 canonical 本身、只取存在的文件）：
   - `platformdirs.user_data_dir("deskpet", appauthor=False, roaming=True)/config.toml`（经典 AppData）
   - 若 `sys.frozen`：`<install_root>/userdata/config.toml`、`<install_root>/backend/userdata/config.toml`、`<drive>\userdata\config.toml`（盘邻居，历史脏写位）。`<install_root>` = backend exe 上 1 级（`Path(sys.executable).parent.parent` 当 parent.name=="backend"，否则 `Path(sys.executable).parent`）。
3. 对每个候选源读 tomli，找出 `llm.endpoints` 里 `enabled != False` 的项。取**第一个有 enabled endpoints 的源**。
4. 用 **tomlkit**（保留注释）把该源的**整个 `[[llm.endpoints]]` 段**并入 canonical：若 canonical 无 endpoints 段则整体插入；若有（但都 disabled）则替换为源的 endpoints。写前 `shutil.copyfile(canonical, canonical.with_suffix(".pre-recover-bak"))`。
5. `logger.info("endpoints_recovered_from src=%s count=%d", src, n)`，`return True`。
6. **全程 try/except 包裹**：任何异常 `logger.warning("endpoints_recover_failed: %s", e)` + `return False`，**绝不抛**（不能阻塞 backend 启动）。tomlkit 不可用同样降级 return False。

在 `resolve_config_path()`（现 1052-1081）里，`seeded = seed_user_config_if_missing()` 之后、`return seeded` 之前，插入：
```python
if seeded is not None and seeded.is_file():
    try:
        _recover_orphaned_endpoints(seeded)
    except Exception as e:  # noqa: BLE001 — 自愈失败绝不阻塞启动
        logger.warning("endpoints_recover_unexpected: %s", e)
    return seeded
```

注意：keychain key 是系统级（非路径绑定），无需迁移；迁回 endpoints 后 `resolve_api_key` 自然能拿到 key（拿不到则由别的任务的空 key 护栏友好报错）。

## 测试 `G:\projects\deskpet\backend\tests\test_config_resolution.py`（追加，勿删现有）
1. `test_recover_merges_enabled_endpoints_from_appdata` — canonical 无 endpoints、AppData config 有 1 个 enabled relay-cloud endpoint → 调用后 canonical 含该 endpoint、生成 `.pre-recover-bak`、log `endpoints_recovered_from`。用 monkeypatch 把 `platformdirs.user_data_dir` 指到 tmp。
2. `test_recover_noop_when_canonical_healthy` — canonical 已有 enabled endpoint → return False、不改文件、不生成备份。
3. `test_recover_idempotent` — 连调两次，第二次 no-op。
4. `test_recover_ignores_all_disabled_source` — 源里 endpoints 全 `enabled=false` → 不迁、return False。
5. `test_recover_never_raises_on_garbage` — 候选源是坏 toml → 不抛、return False。
6. canonical 其余 section（如 `[memory]`）迁移后**逐字保留**。

## 验收（自己跑通）
```
cd G:\projects\deskpet\backend
.venv\Scripts\python.exe -m pytest tests/test_config_resolution.py -q
```
全绿。`.venv\Scripts\python.exe -c "import config"` 不报错。完成后输出改动摘要 + 测试结果。
