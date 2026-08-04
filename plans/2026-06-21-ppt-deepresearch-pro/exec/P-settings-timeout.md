在 DeskPet 前端给设置面板加一个「对话超时(分钟)」设置项。只改 `G:\projects\deskpet\tauri-app\src\components\SettingsPanel.tsx`(必要时其类型/store),别动后端。

## 背景:后端已就绪(我刚加的)
- WS control 消息(走 SettingsPanel 已有的 `ch`/control channel,跟 `permission_auto_mode_set` 同一条):
  - **写**:`ch.send({ type: "chat_turn_timeout_set", payload: { minutes: <int 1-60> } })`
  - **读**:`ch.send({ type: "chat_turn_timeout_get", payload: {} })`
  - 后端两者都回:`{ type: "chat_turn_timeout_response", payload: { minutes: <int> } }`
- 语义:对话回合硬超时(默认 15 分钟)。relay/网络持续不可用时,超过该时长 agent 优雅停止并提示用户,避免桌宠"努力工作中"死转。范围 1~60 分钟。

## 要做
参照 `SettingsPanel.tsx` 里现有 `permission_auto_mode_set`/`permission_auto_mode_response` 的写法(约 452/463 行发送、以及它如何监听 control channel 的 response 来回填 UI 状态):
1. 加一个 state,如 `const [turnTimeoutMin, setTurnTimeoutMin] = useState<number>(15)`。
2. 组件挂载 / channel 就绪时,`ch.send({type:"chat_turn_timeout_get",payload:{}})` 拉当前值;在监听 control channel 消息的地方(找现有处理 `permission_auto_mode_response` 的 onMessage 分支)加一个 `case/if msg.type==="chat_turn_timeout_response"` → `setTurnTimeoutMin(msg.payload.minutes)`。
3. 在设置面板 UI 合适分区(可放在「权限/高级」或模型相关区附近,跟现有项风格一致)加一个**数字输入或带 +/- 步进的控件**:
   - 标签:`对话超时(分钟)`,副说明小字:`网络/中转站持续不响应时,超过此时长自动停止并提示。默认 15 分钟,范围 1–60。`
   - 值绑定 `turnTimeoutMin`;change 时 clamp 到 1~60,`setTurnTimeoutMin(v)` 并 `ch.send({type:"chat_turn_timeout_set",payload:{minutes:v}})`。
   - 风格、布局、深浅色跟该面板现有控件一致(复用现有 className/组件)。
4. 防御:`ch` 可能为 null(参考现有 `try { ch.send(...) }` 的判空写法)。

## 约束/验收
- TS 严格:`cd /g/projects/deskpet/tauri-app && npx tsc --noEmit` 必须 0 error。
- 不破坏现有设置项(permission/model/compaction 等)。
- 若有 SettingsPanel 的测试,跑 `npx vitest run SettingsPanel` 不破;能加一条测试更好(渲染控件 + change 发对 envelope)。
- 不改后端、不改其它无关组件。

完成后简述:改了哪些行/加了什么控件 + tsc 结果。