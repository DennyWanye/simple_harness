# 2026-06-28 userdata 路径绑定与空 Bearer 手工测试

被测计划：`plans/2026-06-28-userdata-path-binding-fix/00-PLAN.md`

被测范围：装机版 `config.toml` 路径跨会话漂移修复、Rust/Python userdata 归一、存量 endpoint 自愈、空/占位 API key 友好错误、启动可观测日志、keyring frozen 后端钉死。

## 环境

| 项 | 要求 |
|---|---|
| OS | Windows 真机，建议 Windows 10/11 |
| dev 模式 | 仓库根 `G:\projects\deskpet`，`npx tauri dev` 可启动 |
| frozen 装机版 | 必须使用本次修复后重新打包的安装包，安装到自定义目录，例如 `F:\deskpet` |
| 结果目录 | 截图固定保存到 `plans/manual-results-2026-06-28-userdata-path/screenshots/` |
| 日志 | dev：tauri-dev 控制台日志通常为 UTF-16；frozen：优先查 `<userdata>\logs\backend.log` 和安装目录/用户目录日志 |
| 输入方式 | 中文输入必须使用剪贴板 `Ctrl+V`，不要依赖 IME 逐字输入 |
| 禁止事项 | 不跑 git，不修改非测试数据文件，不把失败直接判成环境受限 |

## 真机纪律

1. 严格模拟人工。每个用例执行前必须先声明本次窗口里的真实坐标，格式固定为：

   ```text
   坐标=(x,y)|动作=click|期望=焦点落在聊天输入框
   坐标=(x,y)|动作=type|期望=通过剪贴板粘贴中文消息
   坐标=(x,y)|动作=click|期望=发送按钮被点击
   ```

2. 文档中的 `CHAT_INPUT`、`SEND_BUTTON`、`SETTINGS_BUTTON` 等是坐标名，不是占位结果。执行时必须用 windows-mcp/截图读取到的当前真实 `(x,y)` 替换后再操作，并把声明写进执行记录。
3. 每个关键步骤后截图，命名建议：`TCxx-stepNN-说明.png`，保存到 `plans/manual-results-2026-06-28-userdata-path/screenshots/`。
4. 中文输入统一：复制文本到剪贴板，再点击输入框，按 `Ctrl+V`。
5. 日志 grep 优先使用 PowerShell，UTF-16 tauri-dev 日志使用 `-Encoding Unicode` 读取。例如：

   ```powershell
   Get-Content "testcase\_runtime_tauri_dev.log" -Encoding Unicode |
     Select-String "config_loaded|provider_registry_ready|empty_api_key|LocalProtocolError|Illegal header"
   ```

6. 失败后至少 retry 3 次，并尝试 3 种不同 workaround，才可标记“环境受限”。建议 workaround：重启前端、清理本用例临时 userdata、重新登录/退出登录、切换网络、重启 Windows、改用管理员/非管理员权限对照。
7. 判定必须同时看 UI、截图、日志。只有 UI 现象而无日志证据，不能判通过。

## 公共日志命令

dev 模式日志示例：

```powershell
# 如果 npx tauri dev 输出被保存为 UTF-16 日志
Get-Content "testcase\_runtime_tauri_dev.log" -Encoding Unicode |
  Select-String "config_loaded|provider_registry_ready|empty_api_key|ErrorEvent|LocalProtocolError|Illegal header"
```

frozen 装机版日志示例：

```powershell
$ud = "F:\deskpet\userdata"
Get-Content "$ud\logs\backend.log" -ErrorAction Stop |
  Select-String "config_loaded|provider_registry_ready|endpoints_recovered_from|portable_userdata_unwritable|empty_api_key|ErrorEvent|LocalProtocolError|Illegal header"
```

路径一致性对比示例：

```powershell
Select-String -Path "F:\deskpet\userdata\logs\backend.log" -Pattern "config_loaded|provider_registry_ready" |
  Select-Object -Last 20
```

## 用例总览

| ID | 名称 | 执行类型 | 优先级 | 目标 |
|---|---|---:|---:|---|
| TC01 | 空 key 友好错误 | dev 可测 | P0 | 复现用户实际崩溃场景，确认不再出现空 Bearer 崩溃 |
| TC02 | 正常聊天不回归 | dev 可测 | P0 | 有效 key 下流式回复正常 |
| TC03 | 路径跨会话不漂移 | frozen 装机版必测 | P0 | 登录期与重启期 config/userdata 路径完全一致 |
| TC04 | 存量 orphan endpoint 自愈 | frozen 或构造 | P0 | canonical 无 endpoint、AppData 有 endpoint 时自动迁回 |
| TC05 | boot 可观测日志 | 任意模式 | P1 | 启动日志含 portable/env_pinned/provider registry 摘要 |
| TC06 | 本地 Ollama 放行 | 真机优先，单测兜底 | P1 | localhost + key=ollama 不被空 key 保护误拦 |
| TC07 | 云端 + ollama 占位拦截 | dev 可测，单测兜底 | P1 | 非本地 endpoint 使用 ollama/占位 key 时友好报错 |
| TC08 | sentinel 固化绑定 | frozen 优先，单测兜底 | P1 | sentinel 存在时偶发不可写不漂移 |
| TC09 | 安装目录不可写回落 AppData | frozen 优先，单测兜底 | P1 | 不可写时记录 error 并显式回落 AppData |
| TC10 | chain 全 provider 失败 ErrorEvent | dev 可测，单测兜底 | P1 | 全失败时 error_class 透传到前端错误事件 |

---

## TC01 空 key 友好错误

| 字段 | 内容 |
|---|---|
| 执行类型 | dev 可测 |
| 优先级 | P0，用户实际撞到的崩溃 |
| 覆盖风险 | `get_chain()` 无 enabled provider 后 fallback legacy 空 key，拼出 `Bearer ` 导致 httpx `LocalProtocolError` |

### 前置

1. 使用 dev 模式启动：`npx tauri dev`。
2. 准备一个隔离 userdata，避免污染真实登录态。推荐设置 `DESKPET_USER_DATA_DIR=G:\projects\deskpet\.dev-userdata-empty-key` 后启动。
3. 确保没有可用 relay key：退出登录，或清理本测试 userdata 的 `config.toml` 中 enabled provider，或把云端 provider 的 key 设置为占位符/空值。
4. 如果 key 存在于 Windows Credential Manager/keyring，先通过应用退出登录或测试专用账号解除，不能删除真实用户凭据。

### 坐标声明

执行前记录真实坐标：

```text
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=click|期望=焦点落在聊天输入框
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=type|期望=剪贴板粘贴“你好，测试空 key 保护”
坐标=(SEND_BUTTON_X,SEND_BUTTON_Y)|动作=click|期望=消息发送
```

### 步骤

| 步骤 | 人工动作 | 期望即时现象 | 截图 |
|---:|---|---|---|
| 1 | 启动 dev 应用，等待主界面可操作 | 主窗口加载完成，没有启动崩溃 | `TC01-01-boot.png` |
| 2 | 点击 `CHAT_INPUT` | 输入框出现光标 | `TC01-02-focus.png` |
| 3 | 剪贴板写入 `你好，测试空 key 保护`，按 `Ctrl+V` | 输入框显示完整中文 | `TC01-03-paste.png` |
| 4 | 点击 `SEND_BUTTON` | 出现用户消息，随后出现可读错误提示 | `TC01-04-friendly-error.png` |
| 5 | 停留 10 秒，继续点击输入框 | 整轮对话未崩，应用仍可交互 | `TC01-05-still-alive.png` |

### 预期

1. UI 显示可读提示，语义应接近“请重新登录”或“请配置 provider/API key”。
2. 不出现 `Illegal header value b'Bearer '`。
3. 不出现 `LocalProtocolError`。
4. 不出现整轮崩溃、白屏、前端完全不可交互。

### 判定证据

必查日志：

```powershell
Get-Content "testcase\_runtime_tauri_dev.log" -Encoding Unicode |
  Select-String "empty_api_key|ErrorEvent|LocalProtocolError|Illegal header|Bearer "
```

通过判据：

| 证据 | 通过条件 |
|---|---|
| 截图 | `TC01-04-friendly-error.png` 有可读错误提示 |
| 日志 | 有 `empty_api_key` 或等价 error_class |
| 日志 | 没有 `LocalProtocolError` |
| 日志 | 没有 `Illegal header value b'Bearer '` |

---

## TC02 正常聊天不回归

| 字段 | 内容 |
|---|---|
| 执行类型 | dev 可测 |
| 优先级 | P0 |
| 覆盖风险 | 空 key 保护不能误伤有效 relay/provider；流式响应不能回归 |

### 前置

1. dev 模式启动。
2. 使用有效登录态或有效 provider key。
3. 日志中 `provider_registry_ready enabled>=1`。

### 坐标声明

```text
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=click|期望=焦点落在聊天输入框
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=type|期望=剪贴板粘贴“请用一句话回复：路径绑定回归测试”
坐标=(SEND_BUTTON_X,SEND_BUTTON_Y)|动作=click|期望=发送并开始流式回复
```

### 步骤

| 步骤 | 人工动作 | 期望即时现象 | 截图 |
|---:|---|---|---|
| 1 | 启动应用并确认已登录/已配置 provider | 主界面可用，无登录错误 | `TC02-01-ready.png` |
| 2 | 粘贴 `请用一句话回复：路径绑定回归测试` | 输入完整显示 | `TC02-02-input.png` |
| 3 | 点击发送 | 出现流式 token 或加载状态 | `TC02-03-streaming.png` |
| 4 | 等待回复完成 | 有完整助手回复，输入框恢复可用 | `TC02-04-complete.png` |

### 预期

1. 正常流式回复。
2. 没有空 key 相关错误。
3. 没有 provider registry 为空导致的 legacy fallback。

### 判定证据

```powershell
Get-Content "testcase\_runtime_tauri_dev.log" -Encoding Unicode |
  Select-String "provider_registry_ready|empty_api_key|NoProviderConfigured|LocalProtocolError|Illegal header"
```

通过判据：

| 证据 | 通过条件 |
|---|---|
| 截图 | `TC02-04-complete.png` 有助手回复 |
| 日志 | `provider_registry_ready` 中 `enabled>=1` |
| 日志 | 无 `empty_api_key`、无 `LocalProtocolError`、无 `Illegal header` |

---

## TC03 路径跨会话不漂移

| 字段 | 内容 |
|---|---|
| 执行类型 | frozen 装机版必测 |
| 优先级 | P0 |
| 覆盖风险 | Rust/Python 双解析不一致、未注入 `DESKPET_USER_DATA_DIR`、登录期写 A 路径而重启读 B 路径 |

### 前置

1. 使用本次修复后的 frozen 安装包。
2. 全新安装到自定义目录，例如 `F:\deskpet`。
3. 确保 `F:\deskpet\userdata` 可写。
4. 不设置 `DESKPET_BACKEND_DIR` 等 dev 覆盖变量。

### 坐标声明

登录期：

```text
坐标=(LOGIN_BUTTON_X,LOGIN_BUTTON_Y)|动作=click|期望=进入登录流程
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=click|期望=焦点落在聊天输入框
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=type|期望=剪贴板粘贴“登录期路径绑定测试”
坐标=(SEND_BUTTON_X,SEND_BUTTON_Y)|动作=click|期望=发送成功
```

重启期：

```text
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=click|期望=焦点落在聊天输入框
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=type|期望=剪贴板粘贴“重启期路径绑定测试”
坐标=(SEND_BUTTON_X,SEND_BUTTON_Y)|动作=click|期望=不重新登录即可发送成功
```

### 步骤

| 步骤 | 人工动作 | 期望即时现象 | 截图 |
|---:|---|---|---|
| 1 | 安装到 `F:\deskpet` 并启动 | 主界面启动，创建 `F:\deskpet\userdata` | `TC03-01-installed.png` |
| 2 | 完成登录 | 登录成功，provider 写入 config | `TC03-02-login-ok.png` |
| 3 | 发送 `登录期路径绑定测试` | 聊天成功 | `TC03-03-login-chat.png` |
| 4 | 完全退出应用，包括托盘/后台进程 | 任务管理器无 deskpet/backend 进程 | `TC03-04-exited.png` |
| 5 | 重新启动 frozen 应用 | 不要求重新登录 | `TC03-05-rebooted.png` |
| 6 | 发送 `重启期路径绑定测试` | 聊天成功 | `TC03-06-restart-chat.png` |

### 预期

1. 登录期和重启期都使用同一个 `config.toml`。
2. 登录期与重启期 `config_loaded` 的 `path=` 完全一致。
3. 登录期与重启期 `config_loaded` 的 `user_data_dir=` 完全一致。
4. `env_pinned=True`。
5. `provider_registry_ready enabled>=1`。
6. 不出现重启后 provider 丢失、空 Bearer、要求重新登录但 key 实际存在的异常。

### 判定证据

```powershell
Select-String -Path "F:\deskpet\userdata\logs\backend.log" -Pattern "config_loaded|provider_registry_ready|empty_api_key|LocalProtocolError|Illegal header" |
  Select-Object -Last 40
```

通过判据：

| 证据 | 通过条件 |
|---|---|
| 截图 | `TC03-03-login-chat.png` 和 `TC03-06-restart-chat.png` 均聊天成功 |
| 日志 | 两个会话 `config_loaded path=` 完全一致 |
| 日志 | 两个会话 `user_data_dir=` 完全一致 |
| 日志 | `env_pinned=True` |
| 日志 | `provider_registry_ready enabled>=1` |
| 日志 | 无 `LocalProtocolError` / `Illegal header value b'Bearer '` |

---

## TC04 存量 orphan endpoint 自愈

| 字段 | 内容 |
|---|---|
| 执行类型 | frozen 或构造；推荐 frozen 真机 |
| 优先级 | P0 |
| 覆盖风险 | 旧版本把 relay-cloud endpoint 写到 AppData/盘邻居，canonical config 无 enabled endpoints，重启后无法聊天 |

### 前置

构造方式 A，frozen 推荐：

1. 安装到 `F:\deskpet`。
2. canonical：`F:\deskpet\userdata\config.toml` 存在但没有 `[[llm.endpoints]]`，或 endpoints 全 disabled。
3. orphan：`$env:APPDATA\deskpet\config.toml` 存在且包含 enabled `relay-cloud` endpoint。
4. keyring 中仍有对应 relay key，或测试账号可保持有效登录态。

构造方式 B，dev 兜底：

1. 设置 `DESKPET_USER_DATA_DIR` 指向测试目录。
2. 在 canonical config 中移除 enabled endpoints。
3. 在 AppData 候选 config 中放入 enabled endpoints。

### 坐标声明

```text
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=click|期望=焦点落在聊天输入框
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=type|期望=剪贴板粘贴“存量自愈测试”
坐标=(SEND_BUTTON_X,SEND_BUTTON_Y)|动作=click|期望=无需重登即可发送
```

### 步骤

| 步骤 | 人工动作 | 期望即时现象 | 截图 |
|---:|---|---|---|
| 1 | 按前置构造 canonical 无 endpoints、AppData 有 endpoints | 文件状态符合构造 | `TC04-01-files-before.png` |
| 2 | 启动应用 | 启动不崩溃，日志出现 recovery | `TC04-02-boot.png` |
| 3 | 不重新登录，发送 `存量自愈测试` | 聊天成功 | `TC04-03-chat-ok.png` |
| 4 | 检查 canonical 目录 | 出现 `.pre-recover-bak` 备份 | `TC04-04-backup.png` |
| 5 | 再次重启应用 | recovery 幂等，不重复破坏 config | `TC04-05-idempotent.png` |

### 预期

1. 启动日志出现 `endpoints_recovered_from src=... count=N`，`N>=1`。
2. 不重登即可聊天。
3. canonical config 生成 `.pre-recover-bak`。
4. 重启第二次不重复污染 config，不抛异常。

### 判定证据

```powershell
Select-String -Path "F:\deskpet\userdata\logs\backend.log" -Pattern "endpoints_recovered_from|provider_registry_ready|empty_api_key|LocalProtocolError|Illegal header" |
  Select-Object -Last 60

Get-ChildItem "F:\deskpet\userdata" -Force |
  Where-Object { $_.Name -like "*.pre-recover-bak" -or $_.Name -like "*pre-recover*" }
```

通过判据：

| 证据 | 通过条件 |
|---|---|
| 日志 | 有 `endpoints_recovered_from` 且 `count` 大于 0 |
| 文件 | 有 `.pre-recover-bak` |
| 日志 | recovery 后 `provider_registry_ready enabled>=1` |
| 截图 | 不重登聊天成功 |
| 日志 | 无 `LocalProtocolError` / `Illegal header` |

---

## TC05 boot 可观测日志

| 字段 | 内容 |
|---|---|
| 执行类型 | 任意模式 |
| 优先级 | P1 |
| 覆盖风险 | 修复后必须能从启动日志一眼看到 config 路径、portable/env pin 状态、provider registry 数量 |

### 前置

1. dev 或 frozen 均可。
2. 至少启动一次应用并让 backend 完成初始化。

### 坐标声明

本用例不要求聊天；如果需要唤醒主界面：

```text
坐标=(APP_WINDOW_X,APP_WINDOW_Y)|动作=click|期望=窗口获得焦点
```

### 步骤

| 步骤 | 人工动作 | 期望即时现象 | 截图 |
|---:|---|---|---|
| 1 | 启动应用 | 主窗口显示 | `TC05-01-boot.png` |
| 2 | 等待 10 秒 | backend 初始化完成 | `TC05-02-ready.png` |
| 3 | grep 日志 | 能看到 config/provider registry 摘要 | `TC05-03-log.png` |

### 预期

日志至少包含：

1. `config_loaded`
2. `portable=...`
3. `env_pinned=...`
4. `path=...`
5. `user_data_dir=...`
6. `provider_registry_ready`
7. `n=` 或 provider 总数
8. `enabled=` 或 enabled 数
9. `ids=` 或 provider id 列表

### 判定证据

```powershell
Get-Content "testcase\_runtime_tauri_dev.log" -Encoding Unicode |
  Select-String "config_loaded|provider_registry_ready"
```

frozen：

```powershell
Select-String -Path "F:\deskpet\userdata\logs\backend.log" -Pattern "config_loaded|provider_registry_ready" |
  Select-Object -Last 20
```

通过判据：上述字段齐全，且字段值能解释当前运行模式。

---

## TC06 本地 Ollama 放行

| 字段 | 内容 |
|---|---|
| 执行类型 | 真机优先，单测兜底 |
| 优先级 | P1 |
| 覆盖风险 | `key=ollama` 对 localhost 是合法约定，不能被空 key/占位符保护误拦 |

### 前置

1. 本机启动 Ollama 或 OpenAI-compatible 本地服务。
2. endpoint `base_url` 指向 `http://localhost:11434`、`http://127.0.0.1:11434` 或等价本地地址。
3. provider key 设置为 `ollama`。
4. provider enabled。

### 坐标声明

```text
坐标=(SETTINGS_BUTTON_X,SETTINGS_BUTTON_Y)|动作=click|期望=进入 provider 设置
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=click|期望=焦点落在聊天输入框
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=type|期望=剪贴板粘贴“本地 ollama 放行测试”
坐标=(SEND_BUTTON_X,SEND_BUTTON_Y)|动作=click|期望=发送到本地 provider
```

### 步骤

| 步骤 | 人工动作 | 期望即时现象 | 截图 |
|---:|---|---|---|
| 1 | 确认本地服务可用 | 本地模型接口响应正常 | `TC06-01-local-service.png` |
| 2 | 配置 provider 为 localhost + key `ollama` | provider 保存成功 | `TC06-02-provider.png` |
| 3 | 发送 `本地 ollama 放行测试` | 本地模型回复 | `TC06-03-chat-ok.png` |

### 预期

1. localhost/127.0.0.1 endpoint 不因 key=`ollama` 被拦截。
2. 如果本地模型自身失败，应显示本地服务错误，而不是 `empty_api_key`。

### 判定证据

```powershell
Get-Content "testcase\_runtime_tauri_dev.log" -Encoding Unicode |
  Select-String "provider_registry_ready|empty_api_key|ollama|localhost|127.0.0.1|ErrorEvent"
```

通过判据：

| 证据 | 通过条件 |
|---|---|
| 截图 | 本地模型正常回复 |
| 日志 | 无 `empty_api_key` |
| 日志 | provider 指向 localhost/127.0.0.1 |

单测兜底：覆盖 `_client` 或 key guard，断言 localhost + `ollama` 不抛 `LLMProviderError(error_class="empty_api_key")`。

---

## TC07 云端 + ollama 占位拦截

| 字段 | 内容 |
|---|---|
| 执行类型 | dev 可测，单测兜底 |
| 优先级 | P1 |
| 覆盖风险 | 非本地云端 endpoint 使用 `ollama` 或其他占位符时，不应发出伪 Bearer 请求 |

### 前置

1. dev 启动。
2. 配置一个非本地 OpenAI-compatible endpoint，例如 `https://example-cloud.invalid/v1` 或测试云端 endpoint。
3. key 设置为 `ollama`、空字符串、`your-key-here`、`from-keychain` 中任一占位符。
4. provider enabled。

### 坐标声明

```text
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=click|期望=焦点落在聊天输入框
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=type|期望=剪贴板粘贴“云端占位 key 拦截测试”
坐标=(SEND_BUTTON_X,SEND_BUTTON_Y)|动作=click|期望=触发友好 provider 配置错误
```

### 步骤

| 步骤 | 人工动作 | 期望即时现象 | 截图 |
|---:|---|---|---|
| 1 | 保存非本地 endpoint + 占位 key | 保存成功 | `TC07-01-provider.png` |
| 2 | 发送 `云端占位 key 拦截测试` | UI 显示请配置 provider/key 的友好错误 | `TC07-02-friendly-error.png` |
| 3 | 等待 10 秒后继续点击输入框 | 应用仍可交互 | `TC07-03-still-alive.png` |

### 预期

1. 不向云端发送 `Authorization: Bearer ollama` 或 `Bearer `。
2. 抛出/透传 `LLMProviderError(error_class="empty_api_key")`。
3. UI 有可读提示。

### 判定证据

```powershell
Get-Content "testcase\_runtime_tauri_dev.log" -Encoding Unicode |
  Select-String "empty_api_key|ErrorEvent|Bearer ollama|Bearer |LocalProtocolError|Illegal header"
```

通过判据：

| 证据 | 通过条件 |
|---|---|
| 日志 | 有 `empty_api_key` |
| 截图 | 有可读 provider/key 配置提示 |
| 日志 | 无 `LocalProtocolError` / `Illegal header` |

单测兜底：非 localhost endpoint + `ollama` 断言抛 `LLMProviderError(error_class="empty_api_key")`。

---

## TC08 sentinel 固化绑定

| 字段 | 内容 |
|---|---|
| 执行类型 | frozen 优先，单测兜底 |
| 优先级 | P1 |
| 覆盖风险 | `<install>/userdata/.deskpet-portable` 存在时，偶发 probe/write 抖动不应导致路径漂移到 AppData 或盘邻居 |

### 前置

1. frozen 安装到 `F:\deskpet`。
2. 已成功启动过一次，存在 `F:\deskpet\userdata\.deskpet-portable`。
3. 记录当前 `config_loaded path=` 与 `user_data_dir=`。
4. 模拟偶发不可写时要谨慎：只对测试安装目录操作，不碰真实用户数据。

### 坐标声明

```text
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=click|期望=焦点落在聊天输入框
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=type|期望=剪贴板粘贴“sentinel 固化绑定测试”
坐标=(SEND_BUTTON_X,SEND_BUTTON_Y)|动作=click|期望=仍使用 portable userdata 发送
```

### 步骤

| 步骤 | 人工动作 | 期望即时现象 | 截图 |
|---:|---|---|---|
| 1 | 确认 sentinel 文件存在 | `F:\deskpet\userdata\.deskpet-portable` 存在 | `TC08-01-sentinel.png` |
| 2 | 制造一次轻量不可写抖动，例如临时去掉当前用户对 `userdata` 的写权限后启动 | 应用仍识别 portable 绑定，不漂移到 `F:\userdata` | `TC08-02-boot.png` |
| 3 | 恢复写权限，发送测试消息 | 聊天成功或给出非路径漂移类错误 | `TC08-03-chat.png` |
| 4 | grep 启动日志 | `user_data_dir` 仍为 `F:\deskpet\userdata` | `TC08-04-log.png` |

### 预期

1. sentinel 存在时绑定 `<install>/userdata`。
2. 不出现盘邻居 `F:\userdata`。
3. 不因单次 probe 不可写而跨会话漂移。

### 判定证据

```powershell
Select-String -Path "F:\deskpet\userdata\logs\backend.log" -Pattern "config_loaded|portable_userdata_unwritable|AppData|F:\\userdata|user_data_dir" |
  Select-Object -Last 60
```

通过判据：

| 证据 | 通过条件 |
|---|---|
| 文件 | `.deskpet-portable` 存在 |
| 日志 | `user_data_dir=F:\deskpet\userdata` |
| 日志 | 不出现 `F:\userdata` 作为 userdata/config |

单测兜底：mock sentinel 存在、probe 写失败，断言解析结果仍固定到 `<install>/userdata`，且不尝试盘邻居回落。

---

## TC09 安装目录不可写回落 AppData

| 字段 | 内容 |
|---|---|
| 执行类型 | frozen 优先，单测兜底 |
| 优先级 | P1 |
| 覆盖风险 | `<install>/userdata` 不可写时必须 `logger.error` 明确记录并回落 AppData，而不是静默漂移或落到盘邻居 |

### 前置

1. 使用测试安装目录，例如 `C:\Program Files\Deskpet-Test` 或手工移除当前用户写权限的 `F:\deskpet`。
2. 确保没有 sentinel 固化场景干扰；本用例验证“首次不可写回落”。
3. 只在测试目录做权限操作。

### 坐标声明

```text
坐标=(APP_WINDOW_X,APP_WINDOW_Y)|动作=click|期望=应用窗口获得焦点
```

### 步骤

| 步骤 | 人工动作 | 期望即时现象 | 截图 |
|---:|---|---|---|
| 1 | 准备不可写安装目录并启动 frozen | 应用能启动，不崩溃 | `TC09-01-boot.png` |
| 2 | 等待 backend 初始化 | 日志出现 portable 不可写 error | `TC09-02-ready.png` |
| 3 | grep 日志 | 回落路径是 `%AppData%\deskpet`，不是盘邻居 | `TC09-03-log.png` |

### 预期

1. 日志出现 `portable_userdata_unwritable`，级别为 error 或等价错误日志。
2. 明确回落 AppData。
3. 不创建/使用 `F:\userdata` 这类盘邻居路径。

### 判定证据

```powershell
Select-String -Path "$env:APPDATA\deskpet\logs\backend.log" -Pattern "portable_userdata_unwritable|config_loaded|user_data_dir|F:\\userdata" |
  Select-Object -Last 60
```

通过判据：

| 证据 | 通过条件 |
|---|---|
| 日志 | 有 `portable_userdata_unwritable` |
| 日志 | `user_data_dir` 指向 `%AppData%\deskpet` |
| 日志 | 不出现盘邻居 userdata |

单测兜底：mock `<install>/userdata` mkdir/probe 写失败，断言返回 AppData 且记录 `portable_userdata_unwritable`。

---

## TC10 chain 全 provider 失败 ErrorEvent

| 字段 | 内容 |
|---|---|
| 执行类型 | dev 可测，单测兜底 |
| 优先级 | P1 |
| 覆盖风险 | provider chain 全失败时，最终前端错误事件必须保留底层 `error_class`，不能退化成不可读通用错误 |

### 前置

1. dev 启动。
2. 配置 2 个 enabled provider：
   - provider A：非本地 endpoint + 空/占位 key，预期 `empty_api_key`。
   - provider B：不可达 endpoint 或明确返回认证失败的测试 endpoint。
3. 确认 chain 会尝试多个 provider，最终全失败。

### 坐标声明

```text
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=click|期望=焦点落在聊天输入框
坐标=(CHAT_INPUT_X,CHAT_INPUT_Y)|动作=type|期望=剪贴板粘贴“chain 全失败错误透传测试”
坐标=(SEND_BUTTON_X,SEND_BUTTON_Y)|动作=click|期望=最终 ErrorEvent 带 error_class
```

### 步骤

| 步骤 | 人工动作 | 期望即时现象 | 截图 |
|---:|---|---|---|
| 1 | 配置两个必失败 provider 并启用 | registry enabled 数量至少 2 | `TC10-01-providers.png` |
| 2 | 发送 `chain 全失败错误透传测试` | UI 显示可读失败提示 | `TC10-02-error-ui.png` |
| 3 | grep 日志和前端事件 | ErrorEvent 带 `error_class` | `TC10-03-log.png` |

### 预期

1. chain 不会因第一个 provider 的空 key 崩溃。
2. 全 provider 失败后，最终 `ErrorEvent` 带 `error_class`。
3. 如果首要错误是空 key，`error_class=empty_api_key` 应可见；如果最终选择其他错误类，也必须保留具体 class。

### 判定证据

```powershell
Get-Content "testcase\_runtime_tauri_dev.log" -Encoding Unicode |
  Select-String "provider_registry_ready|chain|ErrorEvent|error_class|empty_api_key|LocalProtocolError|Illegal header"
```

通过判据：

| 证据 | 通过条件 |
|---|---|
| 日志 | 有 `ErrorEvent` 且包含 `error_class` |
| 日志 | 有具体错误类，例如 `empty_api_key` |
| 截图 | UI 有可读错误提示 |
| 日志 | 无 `LocalProtocolError` / `Illegal header` |

单测兜底：构造 chain 内全部 provider 抛错，断言最终事件 payload 保留 `error_class`。

---

## 单测兜底清单

这些边界可手工验证，但若真机环境难以稳定构造，必须至少有单测覆盖：

| 边界 | 推荐单测断言 |
|---|---|
| portable 只认 `<install>/userdata` | frozen + `sys.executable=<install>\backend\...exe` 时返回 `<install>\userdata`，不返回 `<drive>\userdata` |
| `DESKPET_USER_DATA_DIR` priority-1 | env 设置后 Python 直接使用 env 路径 |
| 记忆化 | 同进程首次解析后，即使 env/文件状态变化，也不漂移 |
| sentinel 固化 | `.deskpet-portable` 存在时，probe 抖动不改绑定 |
| 不可写回落 | probe 写失败记录 `portable_userdata_unwritable` 并回落 AppData |
| `_recover_orphaned_endpoints` | canonical 无 enabled endpoints、AppData 有 enabled endpoints 时迁移、备份、幂等 |
| localhost + `ollama` | 不抛 `empty_api_key` |
| 云端 + `ollama`/空/占位 key | 抛 `LLMProviderError(error_class="empty_api_key")` |
| chain 全失败 | ErrorEvent payload 保留 `error_class` |
| frozen keyring | PyInstaller spec 包含 keyring/Windows backend hiddenimports |

## 判定汇总表

执行人填写：

| TC | 执行类型 | 结果 | 关键截图 | 关键日志证据 | 备注 |
|---|---|---|---|---|---|
| TC01 空 key 友好错误 | dev | PASS / FAIL / BLOCKED |  | `empty_api_key`；无 `LocalProtocolError` |  |
| TC02 正常聊天不回归 | dev | PASS / FAIL / BLOCKED |  | `provider_registry_ready enabled>=1` |  |
| TC03 路径跨会话不漂移 | frozen | PASS / FAIL / BLOCKED |  | 登录期/重启期 `path` 与 `user_data_dir` 完全一致 |  |
| TC04 存量自愈 | frozen/构造 | PASS / FAIL / BLOCKED |  | `endpoints_recovered_from count=N`，`.pre-recover-bak` |  |
| TC05 boot 可观测 | 任意 | PASS / FAIL / BLOCKED |  | `config_loaded portable/env_pinned`，`provider_registry_ready` |  |
| TC06 本地 Ollama 放行 | 真机/单测 | PASS / FAIL / BLOCKED |  | localhost + no `empty_api_key` |  |
| TC07 云端占位拦截 | dev/单测 | PASS / FAIL / BLOCKED |  | `empty_api_key`，无非法 Bearer |  |
| TC08 sentinel 固化 | frozen/单测 | PASS / FAIL / BLOCKED |  | `.deskpet-portable`，不漂移 |  |
| TC09 不可写回落 AppData | frozen/单测 | PASS / FAIL / BLOCKED |  | `portable_userdata_unwritable`，AppData |  |
| TC10 chain 全失败 ErrorEvent | dev/单测 | PASS / FAIL / BLOCKED |  | `ErrorEvent error_class=...` |  |

## DECISION

结论占位：

```text
DECISION: PASS / FAIL / BLOCKED
版本/构建号:
执行日期:
执行人:
必须修复项:
可延期项:
环境受限说明:
```

发布门槛：

1. TC01、TC02、TC03、TC04 必须 PASS。
2. TC05 必须 PASS，否则后续线上定位能力不足。
3. TC06-TC10 至少真机或单测兜底 PASS；若标 BLOCKED，必须附 3 次 retry 和 3 种 workaround 记录。
4. 任一日志出现 `Illegal header value b'Bearer '` 或 `LocalProtocolError`，本轮直接 FAIL。
