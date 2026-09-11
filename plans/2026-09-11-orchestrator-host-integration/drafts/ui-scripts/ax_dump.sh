#!/bin/bash
# Dump AXStaticText / AXButton / AXCheckBox names of window 1 of the verify app.
BID="$1"
osascript - "$BID" <<'APPLESCRIPT'
on run argv
  set bid to item 1 of argv
  tell application "System Events"
    tell process "simple-harness"
      set out to ""
      set matches to entire contents of window 1
      repeat with el in matches
        try
          set r to role of el
          if r is "AXStaticText" or r is "AXButton" or r is "AXCheckBox" then
            set out to out & r & "|" & (name of el as text) & linefeed
          end if
        end try
      end repeat
      return out
    end tell
  end tell
end run
APPLESCRIPT
