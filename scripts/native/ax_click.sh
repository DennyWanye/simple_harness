#!/bin/bash
# Click a UI element of the running verify app by AX role+name via System Events.
# Usage: ax_click.sh <bundle-id> <role e.g. AXButton> <exact name> [process-name]
set -e
BID="$1"; ROLE="$2"; NAME="$3"; PROC="${4:-simple-harness}"
osascript - "$BID" "$ROLE" "$NAME" "$PROC" <<'APPLESCRIPT'
on run argv
  set bid to item 1 of argv
  set theRole to item 2 of argv
  set theName to item 3 of argv
  set procName to item 4 of argv
  tell application id bid to activate
  delay 0.3
  tell application "System Events"
    tell process procName
      set frontmost to true
      set matches to entire contents of window 1
      repeat with el in matches
        try
          if (role of el is theRole) and (name of el is theName) then
            click el
            return "clicked " & theName
          end if
        end try
      end repeat
      return "not found: " & theName
    end tell
  end tell
end run
APPLESCRIPT
