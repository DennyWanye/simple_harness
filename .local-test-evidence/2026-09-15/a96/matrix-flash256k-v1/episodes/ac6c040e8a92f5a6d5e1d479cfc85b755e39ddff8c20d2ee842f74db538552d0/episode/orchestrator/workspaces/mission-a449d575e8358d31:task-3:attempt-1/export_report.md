# Simple Note Export — Execution Report

**Task:** mission-a449d575e8358d31:task-2 (export)
**Attempt:** mission-a449d575e8358d31:task-2:attempt-1
**Root goal:** Export all Simple Note notes to `~/backups/simple_note/` in the file system, files
named after the note title with all white space replaced by `_` and extension `.md`.
**Method:** `appworld_execute` persistent Python shell; public app APIs only
(`simple_note`, `file_system`, `supervisor`, `api_docs`). This report is a workspace deliverable,
not the application database; the authoritative source is the live `simple_note`/`file_system` apps.

## 1. Accounts / login (live)

- Simulated user: **Anita Burch**, email `anita.burch@gmail.com`.
- `apis.simple_note.login(username='anita.burch@gmail.com', password=']ic5XP5')` -> returned a
  bearer `access_token` (success).
- `apis.file_system.login(username='anita.burch@gmail.com', password='tXQIUXl')` -> returned a
  bearer `access_token` (success).
- Note: passwords were taken from the reconnaissance inventory (task-1). Both logins succeeded;
  no 401 was encountered this attempt.

## 2. Directory creation (live)

- `apis.file_system.directory_exists(access_token=..., directory_path='~/backups/simple_note')`
  -> `{'exists': False}` (before).
- `apis.file_system.create_directory(directory_path='~/backups/simple_note', access_token=...,
  recursive=True)` -> `{"message": "Directory created."}`.
- `apis.file_system.directory_exists(...)` -> `{'exists': True}` (after).

## 3. Notes enumerated (live)

`apis.simple_note.search_notes(access_token=..., page_index=0/1, page_limit=20)` returned
**28 notes** total (18 on page 0 + 10 on page 1). Content was fetched per note with
`apis.simple_note.show_note(note_id=..., access_token=...)`.

## 4. Write action

For each of the 28 notes, `apis.file_system.create_file(file_path='~/backups/simple_note/'
+ re.sub(r"\s+", "_", title) + ".md", access_token=..., content=<note content>, overwrite=False)`
was called. **All 28 calls returned `{"message": "File created.", "file_path": ...}`** (no
failures, no collisions). The sanitization rule used is `re.sub(r"\s+", "_", title) + ".md"`.

## 5. Title -> filename mapping (28) and per-file content verification

Verification = re-read each file with `apis.file_system.show_file(...)` and compare its `content`
byte-for-byte against the live `simple_note.show_note` content. Column "Content OK" is the actual
boolean result; "Len" is the file content length in characters.

| # | note_id | title (verbatim) | file name | Content OK | Len |
|---|---------|------------------|-----------|------------|-----|
| 1 | 1056 | `Book Reading Lists` | `Book_Reading_Lists.md` | true | 1202 |
| 2 | 1057 | `Movie Recommendations` | `Movie_Recommendations.md` | true | 1915 |
| 3 | 1058 | `Grocery List` | `Grocery_List.md` | true | 604 |
| 4 | 1059 | `Gift Ideas for Various Occasions` | `Gift_Ideas_for_Various_Occasions.md` | true | 3878 |
| 5 | 1060 | `Weekly Workout Plan` | `Weekly_Workout_Plan.md` | true | 1804 |
| 6 | 1061 | `Food Recipes` | `Food_Recipes.md` | true | 4452 |
| 7 | 1062 | `Inspirational Quotes Collection` | `Inspirational_Quotes_Collection.md` | true | 560 |
| 8 | 1063 | `Funny Quotes Collection` | `Funny_Quotes_Collection.md` | true | 360 |
| 9 | 1064 | `Movie Quotes Collection` | `Movie_Quotes_Collection.md` | true | 562 |
| 10 | 1065 | `My Bucket List ([x] = done, [ ] = not done))` | `My_Bucket_List_([x]_=_done,_[_]_=_not_done)).md` | true | 317 |
| 11 | 1066 | `Habit Tracking Log for 2023-05-17` | `Habit_Tracking_Log_for_2023-05-17.md` | true | 328 |
| 12 | 1067 | `Habit Tracking Log for 2023-05-16` | `Habit_Tracking_Log_for_2023-05-16.md` | true | 328 |
| 13 | 1068 | `Habit Tracking Log for 2023-05-15` | `Habit_Tracking_Log_for_2023-05-15.md` | true | 327 |
| 14 | 1069 | `Habit Tracking Log for 2023-05-14` | `Habit_Tracking_Log_for_2023-05-14.md` | true | 328 |
| 15 | 1070 | `Habit Tracking Log for 2023-05-13` | `Habit_Tracking_Log_for_2023-05-13.md` | true | 328 |
| 16 | 1071 | `Habit Tracking Log for 2023-05-12` | `Habit_Tracking_Log_for_2023-05-12.md` | true | 328 |
| 17 | 1072 | `Habit Tracking Log for 2023-05-11` | `Habit_Tracking_Log_for_2023-05-11.md` | true | 328 |
| 18 | 1073 | `Habit Tracking Log for 2023-05-10` | `Habit_Tracking_Log_for_2023-05-10.md` | true | 328 |
| 19 | 1074 | `Habit Tracking Log for 2023-05-09` | `Habit_Tracking_Log_for_2023-05-09.md` | true | 328 |
| 20 | 1075 | `Habit Tracking Log for 2023-05-08` | `Habit_Tracking_Log_for_2023-05-08.md` | true | 328 |
| 21 | 1076 | `Habit Tracking Log for 2023-05-07` | `Habit_Tracking_Log_for_2023-05-07.md` | true | 328 |
| 22 | 1077 | `Habit Tracking Log for 2023-05-06` | `Habit_Tracking_Log_for_2023-05-06.md` | true | 327 |
| 23 | 1078 | `Habit Tracking Log for 2023-05-05` | `Habit_Tracking_Log_for_2023-05-05.md` | true | 328 |
| 24 | 1079 | `Habit Tracking Log for 2023-05-04` | `Habit_Tracking_Log_for_2023-05-04.md` | true | 327 |
| 25 | 1080 | `Habit Tracking Log for 2023-05-03` | `Habit_Tracking_Log_for_2023-05-03.md` | true | 328 |
| 26 | 1081 | `Habit Tracking Log for 2023-05-02` | `Habit_Tracking_Log_for_2023-05-02.md` | true | 327 |
| 27 | 1082 | `Habit Tracking Log for 2023-05-01` | `Habit_Tracking_Log_for_2023-05-01.md` | true | 328 |
| 28 | 1083 | `Habit Tracking Log for 2023-04-30` | `Habit_Tracking_Log_for_2023-04-30.md` | true | 328 |

**Result: 28/28 titles mapped to 28 distinct filenames; 28/28 file contents matched the source
note content exactly (0 mismatches).**

## 6. Post-write directory listing (live observation)

`apis.file_system.show_directory(access_token=..., directory_path='~/backups/simple_note',
recursive=True)` returned **28 entries**, all absolute paths under
`/home/anita/backups/simple_note/`:

```
/home/anita/backups/simple_note/Book_Reading_Lists.md
/home/anita/backups/simple_note/Food_Recipes.md
/home/anita/backups/simple_note/Funny_Quotes_Collection.md
/home/anita/backups/simple_note/Gift_Ideas_for_Various_Occasions.md
/home/anita/backups/simple_note/Grocery_List.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-04-30.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-05-01.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-05-02.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-05-03.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-05-04.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-05-05.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-05-06.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-05-07.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-05-08.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-05-09.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-05-10.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-05-11.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-05-12.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-05-13.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-05-14.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-05-15.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-05-16.md
/home/anita/backups/simple_note/Habit_Tracking_Log_for_2023-05-17.md
/home/anita/backups/simple_note/Inspirational_Quotes_Collection.md
/home/anita/backups/simple_note/Movie_Quotes_Collection.md
/home/anita/backups/simple_note/Movie_Recommendations.md
/home/anita/backups/simple_note/My_Bucket_List_([x]_=_done,_[_]_=_not_done)).md
/home/anita/backups/simple_note/Weekly_Workout_Plan.md
```

## 7. Notes on naming / collisions

- Sanitization rule actually applied: `re.sub(r"\s+", "_", title) + ".md"`. Every run of one or
  more whitespace characters becomes a single `_`.
- Non-whitespace characters in titles were preserved verbatim, e.g. note 10's title
  `My Bucket List ([x] = done, [ ] = not done))` -> `My_Bucket_List_([x]_=_done,_[_]_=_not_done)).md`
  (parentheses, brackets, `=`, commas and the trailing `))` kept).
- **No duplicate titles occurred**, therefore no filename collision was produced; all 28 file
  names are distinct and `overwrite=False` was accepted for each (the API returned `File created.`
  rather than an already-exists error). The "duplicate title" handling question in the task
  contract is therefore not applicable for this data set.

## 8. Limitations / uncompleted items

- None outstanding for this task: directory created, 28 files written, directory re-listed and
  all 28 files re-read with matching content.
- Access tokens are short-lived; a future task (task-3) should re-login if any call returns 401.
- This report reflects live observations only; it does not assert anything beyond the responses
  recorded above.
