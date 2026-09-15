# REPORT — Final integrated verification & delivery (task-3)

**Task:** mission-a449d575e8358d31:task-3 (attempt-1)
**Root goal:** Export all my Simple Note notes to `~/backups/simple_note/` in my file system, with
each file named after the note title (white space replaced by `_`, extension `.md`).
**Method:** `appworld_execute` persistent Python shell; public app APIs only
(`supervisor`, `simple_note`, `file_system`), plus the dependency artifacts
`notes_inventory.md` (task-1) and `export_report.md` (task-2), which were read and cross-checked
against the live world. This report is a workspace deliverable, not the application database.

## Outcome: COMPLETE — independently re-verified against the live shared world

All checks below were performed LIVE in this attempt (fresh logins; no reliance on prior reports
for the facts asserted).

### 1. Identity / access (live)
- `apis.supervisor.show_profile()` -> Anita Burch, `anita.burch@gmail.com` (matches inventory).
- `apis.simple_note.login(username='anita.burch@gmail.com', password=']ic5XP5')` -> `Bearer` token (success).
- `apis.file_system.login(username='anita.burch@gmail.com', password='tXQIUXl')` -> `Bearer` token (success).
- No HTTP 401 this attempt.

### 2. Target directory (live)
- `apis.file_system.directory_exists(directory_path='~/backups/simple_note')` -> `{'exists': True}`.
- `apis.file_system.show_directory(..., recursive=True)` -> **28 entries**, all under
  `/home/anita/backups/simple_note/`.

### 3. Note enumeration (live)
- `apis.simple_note.search_notes(...)` paginated (page_limit 20) -> **28 notes**, ids 1056–1083.
  This exactly matches the 28-note set recorded in `notes_inventory.md` (no additions, no removals).

### 4. Name cross-check (live) — whitespace -> `_`, extension `.md`
- Expected filenames computed live as `re.sub(r"\s+", "_", title) + ".md"` for all 28 titles.
- Compared the expected set against the 28 on-disk filenames:
  - **MISSING (expected but absent): none**
  - **EXTRA (present but not expected): none**
  - 28 expected == 28 actual; all distinct (no collisions).

### 5. Content completeness (live)
For every one of the 28 notes, the on-disk file was re-read with
`apis.file_system.show_file(...)` and compared to the live `apis.simple_note.show_note(...)`
content:
- **28 / 28 files content-identical (0 mismatches).** Character lengths matched per file
  (317–4452 chars; e.g. `Food_Recipes.md` & note 1061 = 4452; `Grocery_List.md` & note 1058 = 604).

### 6. Full title -> filename mapping (live-verified)

| # | note_id | title (verbatim) | file name | names OK | content OK |
|---|---------|------------------|-----------|----------|------------|
| 1 | 1056 | `Book Reading Lists` | `Book_Reading_Lists.md` | yes | yes (1202) |
| 2 | 1057 | `Movie Recommendations` | `Movie_Recommendations.md` | yes | yes (1915) |
| 3 | 1058 | `Grocery List` | `Grocery_List.md` | yes | yes (604) |
| 4 | 1059 | `Gift Ideas for Various Occasions` | `Gift_Ideas_for_Various_Occasions.md` | yes | yes (3878) |
| 5 | 1060 | `Weekly Workout Plan` | `Weekly_Workout_Plan.md` | yes | yes (1804) |
| 6 | 1061 | `Food Recipes` | `Food_Recipes.md` | yes | yes (4452) |
| 7 | 1062 | `Inspirational Quotes Collection` | `Inspirational_Quotes_Collection.md` | yes | yes (560) |
| 8 | 1063 | `Funny Quotes Collection` | `Funny_Quotes_Collection.md` | yes | yes (360) |
| 9 | 1064 | `Movie Quotes Collection` | `Movie_Quotes_Collection.md` | yes | yes (562) |
| 10 | 1065 | `My Bucket List ([x] = done, [ ] = not done))` | `My_Bucket_List_([x]_=_done,_[_]_=_not_done)).md` | yes | yes (317) |
| 11 | 1066 | `Habit Tracking Log for 2023-05-17` | `Habit_Tracking_Log_for_2023-05-17.md` | yes | yes (328) |
| 12 | 1067 | `Habit Tracking Log for 2023-05-16` | `Habit_Tracking_Log_for_2023-05-16.md` | yes | yes (328) |
| 13 | 1068 | `Habit Tracking Log for 2023-05-15` | `Habit_Tracking_Log_for_2023-05-15.md` | yes | yes (327) |
| 14 | 1069 | `Habit Tracking Log for 2023-05-14` | `Habit_Tracking_Log_for_2023-05-14.md` | yes | yes (328) |
| 15 | 1070 | `Habit Tracking Log for 2023-05-13` | `Habit_Tracking_Log_for_2023-05-13.md` | yes | yes (328) |
| 16 | 1071 | `Habit Tracking Log for 2023-05-12` | `Habit_Tracking_Log_for_2023-05-12.md` | yes | yes (328) |
| 17 | 1072 | `Habit Tracking Log for 2023-05-11` | `Habit_Tracking_Log_for_2023-05-11.md` | yes | yes (328) |
| 18 | 1073 | `Habit Tracking Log for 2023-05-10` | `Habit_Tracking_Log_for_2023-05-10.md` | yes | yes (328) |
| 19 | 1074 | `Habit Tracking Log for 2023-05-09` | `Habit_Tracking_Log_for_2023-05-09.md` | yes | yes (328) |
| 20 | 1075 | `Habit Tracking Log for 2023-05-08` | `Habit_Tracking_Log_for_2023-05-08.md` | yes | yes (328) |
| 21 | 1076 | `Habit Tracking Log for 2023-05-07` | `Habit_Tracking_Log_for_2023-05-07.md` | yes | yes (328) |
| 22 | 1077 | `Habit Tracking Log for 2023-05-06` | `Habit_Tracking_Log_for_2023-05-06.md` | yes | yes (327) |
| 23 | 1078 | `Habit Tracking Log for 2023-05-05` | `Habit_Tracking_Log_for_2023-05-05.md` | yes | yes (328) |
| 24 | 1079 | `Habit Tracking Log for 2023-05-04` | `Habit_Tracking_Log_for_2023-05-04.md` | yes | yes (327) |
| 25 | 1080 | `Habit Tracking Log for 2023-05-03` | `Habit_Tracking_Log_for_2023-05-03.md` | yes | yes (328) |
| 26 | 1081 | `Habit Tracking Log for 2023-05-02` | `Habit_Tracking_Log_for_2023-05-02.md` | yes | yes (327) |
| 27 | 1082 | `Habit Tracking Log for 2023-05-01` | `Habit_Tracking_Log_for_2023-05-01.md` | yes | yes (328) |
| 28 | 1083 | `Habit Tracking Log for 2023-04-30` | `Habit_Tracking_Log_for_2023-04-30.md` | yes | yes (328) |

## Conclusion

The user's full goal is achieved in the shared world: **28 notes exported to 28 files in
`~/backups/simple_note/`, names follow the title-with-`_`-and-`.md` rule, and every file's content
equals the corresponding live note content exactly (0 missing, 0 extra, 0 misnamed, 0 content
mismatch).**

## Uncompleted items / limitations

- None. No missing, misnamed, or content-divergent files were found.
- Caveat on scope: access tokens are short-lived; a later interaction may need a fresh
  `login(...)` if an API returns HTTP 401. All observations above are bounded to the live API
  responses recorded in this attempt.
- `notes_inventory.md` and `export_report.md` are dependency workspace reports; the authoritative
  sources are the live `simple_note` and `file_system` apps, which were queried directly here.
