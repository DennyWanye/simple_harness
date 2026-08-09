#!/usr/bin/env bash
# AX 探针：列出主窗内可交互元素的**实时绝对逻辑坐标**。
#
# 为什么需要：侧栏在「会话视图（会话列表展开）」和「其他视图（列表收起）」之间
# 会伸缩，导航项 y 坐标随之变化。用上一张截图算坐标连点必然错位（实测踩了 3 次）。
# 每次点击前重新跑本探针取当前坐标，才不会点空。
#
# 用法：
#   ax.sh nav          列出侧栏导航按钮（顺序：会话/技能中心/产物库/更多/设置）
#   ax.sh text <关键词>  列出匹配的静态文本及坐标
#   ax.sh all          全量 dump
set -uo pipefail
APP=simple-harness

dump() {
osascript << AS 2>&1
tell application "System Events" to tell (first process whose name is "$APP")
  set out to ""
  set els to entire contents of window 1
  repeat with e in els
    try
      set r to (role of e)
      if r is in {"AXButton", "AXLink", "AXStaticText", "AXTextField", "AXTextArea", "AXCheckBox", "AXPopUpButton"} then
        set p to position of e
        set s to size of e
        set t to ""
        try
          set t to (value of e) as text
        end try
        if t is "" then try
          set t to (description of e) as text
        end try
        set out to out & r & "\t" & t & "\t" & (item 1 of p) & "," & (item 2 of p) & "\t" & (item 1 of s) & "x" & (item 2 of s) & linefeed
      end if
    end try
  end repeat
  return out
end tell
AS
}

case "${1:-all}" in
  # 不加 nl —— 加了会多插一列把 awk 的字段序号顶掉（实测踩过）。行号即索引。
  nav)   dump | grep "^AXButton" | head -8 ;;
  text)  dump | grep -i "${2:-}" ;;
  *)     dump ;;
esac
