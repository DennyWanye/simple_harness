#!/usr/bin/env bash
# 稳定的 GUI 驱动器：所有坐标相对窗口原点（逻辑点），每步强制前台校验。
# 用法：drive.sh click <dx> <dy> | type <text> | paste <text> | key <keycode> | shot <name> | geom
set -uo pipefail
APP=simple-harness
A=/Users/denny/projects/simple_harness/plans/2026-08-04-workbench-ui/verification/workbench-ui-20260807-r7/artifacts

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
  osascript << AS
tell application "System Events" to tell (first process whose name is "$APP")
  set p to position of first window
  set s to size of first window
  return (item 1 of p as text) & " " & (item 2 of p as text) & " " & (item 1 of s as text) & " " & (item 2 of s as text)
end tell
AS
}

case "${1:-}" in
  geom) geom ;;
  click)
    front || exit 1
    read -r wx wy ww wh <<< "$(geom)"
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
    read -r wx wy ww wh <<< "$(geom)"
    # 裁窗口区域（物理像素=逻辑×2）
    sips -c $((wh*2)) $((ww*2)) --cropOffset $((wy*2)) $((wx*2)) "$A/$2.png" --out "$A/$2-win.png" >/dev/null 2>&1
    echo "shot: $2 (win crop $ww x $wh @ $wx,$wy)"
    ;;
  *) echo "usage: drive.sh {geom|click dx dy|type text|paste text|key code|shot name}" >&2; exit 2 ;;
esac
