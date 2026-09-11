#!/bin/bash
# usage: send.sh <bundle-id> <message>  — sets the chat textarea value via System Events and clicks 发送
BID="$1"; MSG="$2"
osascript - "$BID" "$MSG" <<'APPLESCRIPT'
on run argv
  set bid to item 1 of argv
  set msg to item 2 of argv
  tell application id bid to activate
  delay 0.3
  tell application "System Events"
    tell process "simple-harness"
      set frontmost to true
      set matches to entire contents of window 1
      repeat with el in matches
        try
          if (role of el is "AXTextArea") then
            set focused of el to true
            delay 0.2
            set value of el to msg
            delay 0.2
            return "ok"
          end if
        end try
      end repeat
    end tell
  end tell
  return "no textarea"
end run
APPLESCRIPT
sleep 0.5; bash /Users/taiwan/PROJECTS/SimplaHarness/simple_harness/scripts/native/ax_click.sh "$BID" AXButton 发送 | tail -1
