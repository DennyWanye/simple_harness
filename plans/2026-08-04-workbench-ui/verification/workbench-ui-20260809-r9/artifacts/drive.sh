#!/usr/bin/env bash
# 稳定的 GUI 驱动器：所有坐标相对窗口原点（逻辑点），每步强制前台校验。
# 用法：drive.sh guard | click <dx> <dy> | type <text> | paste <text> | key <keycode> | shot <name> | geom
# 硬闸：裸 cliclick / 裸 osascript keystroke 之前必须先 `drive.sh guard` 且退出码为 0；
#       任何坐标计算之前必须先 `drive.sh geom` 且退出码为 0（AX 会瞬时失灵）。
set -uo pipefail

# ── 硬闸 0：屏幕未锁 ────────────────────────────────────────────────
# 锁屏时 WindowServer 不合成用户窗口 → AX 查询返回 0 窗口、screencapture 只有
# 壁纸、WebView 被挂起导致前端 effect 不执行（表现为 backend 起不来）。
# 这套失败形态酷似"应用崩了/代码回归"，2026-08-08 实测据此误判过一次，
# 差点去二分一个不存在的回归。所有观测前先断言未锁。
# 两个坑（都实测踩过，别改回管道写法）：
#   ① `ioreg ... | grep -q` + `set -o pipefail`：ioreg 自身退出码非零 → 整条
#      管道判失败 → if 永远不成立。
#   ② 即使先把输出存进变量，`printf ... | grep -q` 仍然失败：grep -q 命中后
#      立即退出并关闭管道，printf 收到 SIGPIPE 返回非零，pipefail 再次把管道
#      判为失败。grep -c 因为读完全部输入反而"正常"，两者结论相反极具迷惑性。
# 结论：用纯 bash 的 case 做子串匹配，完全不经管道。
_ioreg_out="$(ioreg -n Root -d1 -a 2>/dev/null || true)"
case "$_ioreg_out" in *CGSSessionScreenIsLocked*)
  echo "SCREEN_LOCKED: 屏幕已锁定，AX/截图/窗口计数全部无效观测。请先解锁再测。" >&2
  exit 3 ;;
esac

APP=simple-harness
A=/Users/denny/projects/simple_harness/plans/2026-08-04-workbench-ui/verification/workbench-ui-20260809-r9/artifacts

front() {
  osascript -e "tell application \"System Events\" to set frontmost of (first process whose name is \"$APP\") to true" >/dev/null 2>&1
  for i in 1 2 3 4 5; do
    f=$(osascript -e 'tell application "System Events" to name of first process whose frontmost is true' 2>/dev/null)
    [ "$f" = "$APP" ] && return 0
    osascript -e "tell application \"System Events\" to set frontmost of (first process whose name is \"$APP\") to true" >/dev/null 2>&1
    sleep 0.4
  done
  echo "FRONT_FAIL(front=$f)" >&2; return 1
}

geom() {
  # AX 会瞬时失灵（报「window 1 … 无效的索引」或返回空）。这里**必须**在拿不到
  # 几何时以非零退出并打印 GEOM_FAIL —— 早先版本静默返回空串，调用方
  # `read -r wx wy ww wh` 拿到空值后算出 (0,0)，点击落到屏幕左上角或别的应用。
  local out
  out=$(osascript << AS 2>/dev/null
tell application "System Events" to tell (first process whose name is "$APP")
  set p to position of first window
  set s to size of first window
  return (item 1 of p as text) & " " & (item 2 of p as text) & " " & (item 1 of s as text) & " " & (item 2 of s as text)
end tell
AS
)
  # 必须是四个数字；空串 / 少字段一律视为失败。
  if [ -z "$(printf '%s' "$out" | tr -d ' ')" ] || [ "$(printf '%s\n' "$out" | wc -w | tr -d ' ')" != "4" ]; then
    echo "GEOM_FAIL(AX 未返回有效几何，勿据此计算坐标)" >&2
    return 1
  fi
  printf '%s\n' "$out"
}

# 前台硬闸：任何**裸 cliclick / 裸 osascript keystroke** 之前都要先过这一关。
# 本轮真实教训：在别处绕开 drive.sh 直接敲键，遇上 `set frontmost` 静默失败，
# 按键落到了另一个应用。输入类操作落错窗口是不可逆风险，必须 fail-closed。
guard() {
  front || { echo "GUARD_FAIL(无法把 $APP 置前台，禁止发送输入)" >&2; return 1; }
  local f
  f=$(osascript -e 'tell application "System Events" to name of first process whose frontmost is true' 2>/dev/null)
  if [ "$f" != "$APP" ]; then
    echo "GUARD_FAIL(前台是 '$f' 而非 $APP，禁止发送输入)" >&2
    return 1
  fi
  echo "GUARD_OK front=$f"
}

case "${1:-}" in
  geom) geom ;;
  guard) guard ;;
  click)
    front || exit 1
    # 必须先取到有效几何再算坐标。原先直接 `read <<< "$(geom)"` 不看退出码，
    # AX 瞬时失灵时 wx/wy 为空 → $((wx + dx)) 当 0 处理 → 点到 (dx,dy) 绝对位置，
    # 可能落在别的应用上。fail-closed。
    G=$(geom) || { echo "ABORT: 几何不可得，拒绝点击" >&2; exit 1; }
    read -r wx wy ww wh <<< "$G"
    X=$((wx + $2)); Y=$((wy + $3))
    # 用 cliclick 真 HID 点击。AppleScript `click at {x,y}` 实测不可靠
    # （关 Web Inspector、点会话行都静默不生效），不再使用。
    cliclick m:$X,$Y w:150 c:$X,$Y
    echo "clicked win($wx,$wy)+($2,$3) => ($X,$Y)"
    ;;
  type)
    front || exit 1
    osascript << AS >/dev/null 2>&1
tell application "System Events" to tell (first process whose name is "$APP")
  set frontmost to true
  delay 0.2
  keystroke "$2"
end tell
AS
    echo "typed: $2"
    ;;
  paste)
    # UTF-8 locale is required or pbcopy silently drops non-ASCII (实测:
    # 无 LC_ALL 时中文进剪贴板变空串)。
    printf '%s' "$2" | LC_ALL=en_US.UTF-8 pbcopy
    front || exit 1
    osascript << AS >/dev/null 2>&1
tell application "System Events" to tell (first process whose name is "$APP")
  set frontmost to true
  delay 0.3
  keystroke "v" using command down
end tell
AS
    echo "pasted: $2"
    ;;
  key)
    front || exit 1
    osascript << AS >/dev/null 2>&1
tell application "System Events" to tell (first process whose name is "$APP")
  set frontmost to true
  delay 0.2
  key code $2
end tell
AS
    echo "key: $2"
    ;;
  shot)
    front || exit 1
    sleep 0.5
    screencapture -x "$A/$2.png"
    G=$(geom) || { echo "shot: $2 (全屏已存，几何不可得故未裁窗口区域)"; exit 0; }
    read -r wx wy ww wh <<< "$G"
    # 裁窗口区域（物理像素=逻辑×2）
    sips -c $((wh*2)) $((ww*2)) --cropOffset $((wy*2)) $((wx*2)) "$A/$2.png" --out "$A/$2-win.png" >/dev/null 2>&1
    echo "shot: $2 (win crop $ww x $wh @ $wx,$wy)"
    ;;
  *) echo "usage: drive.sh {guard|geom|click dx dy|type text|paste text|key code|shot name}" >&2; exit 2 ;;
esac
