# Manual E2E Results: DeepResearch Source Packs

Date: 2026-07-08

## Scope

Real DeskPet UI test for a Chinese Ukraine/Russia research prompt. The goal was to verify:

- the message panel creates a UUID session;
- the user can trigger `deepresearch` from the UI;
- source-pack directed searches are used inside deepresearch;
- the outer agent does not call a separate `web_search` after the report is saved.

## Environment

- App: Tauri dev, `target\debug\deskpet.exe`
- Backend launch evidence: `.tmp\dev-tauri.err.log` contains `[backend_launch] Dev python=F:\projects\deskpet\backend\.venv\Scripts\python.exe backend_dir=F:\projects\deskpet\backend`
- Ports: Vite `5173`, backend `8100`
- Config evidence: `.tmp\dev-tauri.err.log` contains `feature_flag_merge_applied ... research.source_packs`

## UI Steps

1. Opened DeskPet message panel via the main window's `消息` button.
2. Clicked `+ 新话题`.
3. Confirmed the new session id rendered as UUID: `a8204ba6-18df-4856-ae05-de3a8bdb5f9e`.
4. Pasted Chinese prompt with clipboard + Ctrl+V:
   `请调研一下俄乌最近的局势，给我带来源的简明结论`
5. Clicked `发送`.
6. Observed a `deepresearch` tool card, a saved markdown report card, and a final assistant summary.

## Evidence

Screenshots:

- `01-new-session.jpg`
- `02-sent.jpg`
- `03-deepresearch-tool-call.jpg`
- `04-report-and-final-reply.jpg`

Log evidence from `.tmp\dev-tauri.err.log`:

- `name='deepresearch'`
- `deepresearch_finalize_queued sid=a8204ba6-18df-4856-ae05-de3a8bdb5f9e`
- `https://www.google.com/search?q=Russia%20Ukraine%20latest%20situation%20ISW%20site%3Aunderstandingwar.org&hl=en`
- `https://www.google.com/search?q=Russia%20Ukraine%20civilian%20casualties%20humanitarian%20impact%20latest%20site%3Aun.org`
- `https://www.google.com/search?q=Russia%20Ukraine%20war%20latest%20Reuters%20AP%20BBC%20Al%20Jazeera%20%28site%3Areuters`

No `p5s2_tool_call_args_dump ... name='web_search'` appeared after the `deepresearch` call in this run. The only `web_search` matches were startup tool-registration lines.

## Result

PASS with a quality note: the UI and routing behavior now match the expected flow, and the source-pack queries are standalone authority-directed templates. The live search provider still returned limited usable source material in this environment, so the final assistant answer correctly warned that the report's cited material was thin.

## Follow-Up From Manual Test

The first manual run showed that source-pack queries were being generated as `Chinese sub-question + site:...`, which made live SERP quality weaker. This was corrected in `backend/deskpet/tools/research_tools.py` so source packs now add standalone authority-directed queries.
