# Task B — Simple Note Export Log

Task id: `mission-8146a39116397bdb:task-2`
Goal: Export every Simple Note note to `~/backups/simple_note/<filename>` where `<filename>` = note title with all whitespace runs replaced by `_` plus `.md`; create the directory if missing; handle empty/duplicate/colliding titles; verify each write by reading it back; record per-note evidence.

Inputs from Task A (`reports/A_recon.md`): app `simple_note` (login `anita.burch@gmail.com` / password `]ic5XP5`), app `file_system` (login `anita.burch@gmail.com` / password `tXQIUXl`), `~` = `/home/anita/`, target `~/backups/simple_note/` (did not exist).

## 1. Actions performed (all via `appworld_execute`, real observations)

1. `apis.file_system.login(username="anita.burch@gmail.com", password="tXQIUXl")` → token OK.
2. `apis.simple_note.login(username="anita.burch@gmail.com", password="]ic5XP5")` → token OK.
3. `apis.file_system.directory_exists(directory_path="~/backups/simple_note", ...)` → `{'exists': False}`; `directory_exists("~/backups")` → `{'exists': True}`.
4. `apis.simple_note.search_notes(access_token=sn_tok, page_index=0..4, page_limit=20)` → page0=20, page1=8, page2=0 → **28 notes total**.
5. `apis.file_system.create_directory(directory_path="~/backups/simple_note/", access_token=fs_tok, recursive=True)` → `{'message': 'Directory created.'}`; `directory_exists` then → `{'exists': True}`.
6. `apis.file_system.show_directory(directory_path="~/backups/simple_note/", recursive=True)` before writing → `[]` (empty, no pre-existing files).
7. For each of the 28 notes: `apis.simple_note.show_note(note_id=<id>, access_token=sn_tok)` to fetch full content, then `apis.file_system.create_file(file_path="~/backups/simple_note/<filename>", access_token=fs_tok, content=<content>, overwrite=True)`. All 28 returned `{'message': 'File created.', 'file_path': ...}` — no errors.
8. Verification: for each file, `apis.file_system.show_file(file_path="~/backups/simple_note/<filename>", access_token=fs_tok)` and compared `content` byte-for-byte with `show_note` content. **All 28 matched.**
9. Final listing: `apis.file_system.show_directory(directory_path="~/backups/simple_note/", recursive=True)` → **28 entries**, all `.md`, none extra.

## 2. Filename sanitization and edge cases

- Rule applied: `re.sub(r"\s+", "_", title.strip()) + ".md"` (all whitespace runs → single `_`).
- Empty titles: `[]` (none). Duplicate resulting filenames: `[]` (none). No collisions.
- No title contained consecutive whitespace, so `str.replace(" ","_")` and the regex produce identical results; the regex was used for robustness.
- Note `1058` "Grocery List" → `Grocery_List.md` matches the user's stated convention. (Note: `1057` title is "Movie Recommendations" per the Simple Note API.)
- Title with punctuation `1065` "My Bucket List ([x] = done, [ ] = not done))" → `My_Bucket_List_([x]_=_done,_[_]_=_not_done)).md` (punctuation preserved; only whitespace replaced, per the stated rule).

## 3. Per-note evidence

write_message = message returned by `create_file`; verify_match = (show_file.content == show_note.content).

| note_id | title | resulting filename | write_message | verify_match |
|---|---|---|---|---|
| 1066 | Habit Tracking Log for 2023-05-17 | `Habit_Tracking_Log_for_2023-05-17.md` | File created. | True |
| 1067 | Habit Tracking Log for 2023-05-16 | `Habit_Tracking_Log_for_2023-05-16.md` | File created. | True |
| 1068 | Habit Tracking Log for 2023-05-15 | `Habit_Tracking_Log_for_2023-05-15.md` | File created. | True |
| 1069 | Habit Tracking Log for 2023-05-14 | `Habit_Tracking_Log_for_2023-05-14.md` | File created. | True |
| 1070 | Habit Tracking Log for 2023-05-13 | `Habit_Tracking_Log_for_2023-05-13.md` | File created. | True |
| 1071 | Habit Tracking Log for 2023-05-12 | `Habit_Tracking_Log_for_2023-05-12.md` | File created. | True |
| 1072 | Habit Tracking Log for 2023-05-11 | `Habit_Tracking_Log_for_2023-05-11.md` | File created. | True |
| 1073 | Habit Tracking Log for 2023-05-10 | `Habit_Tracking_Log_for_2023-05-10.md` | File created. | True |
| 1074 | Habit Tracking Log for 2023-05-09 | `Habit_Tracking_Log_for_2023-05-09.md` | File created. | True |
| 1075 | Habit Tracking Log for 2023-05-08 | `Habit_Tracking_Log_for_2023-05-08.md` | File created. | True |
| 1076 | Habit Tracking Log for 2023-05-07 | `Habit_Tracking_Log_for_2023-05-07.md` | File created. | True |
| 1077 | Habit Tracking Log for 2023-05-06 | `Habit_Tracking_Log_for_2023-05-06.md` | File created. | True |
| 1078 | Habit Tracking Log for 2023-05-05 | `Habit_Tracking_Log_for_2023-05-05.md` | File created. | True |
| 1079 | Habit Tracking Log for 2023-05-04 | `Habit_Tracking_Log_for_2023-05-04.md` | File created. | True |
| 1080 | Habit Tracking Log for 2023-05-03 | `Habit_Tracking_Log_for_2023-05-03.md` | File created. | True |
| 1081 | Habit Tracking Log for 2023-05-02 | `Habit_Tracking_Log_for_2023-05-02.md` | File created. | True |
| 1082 | Habit Tracking Log for 2023-05-01 | `Habit_Tracking_Log_for_2023-05-01.md` | File created. | True |
| 1083 | Habit Tracking Log for 2023-04-30 | `Habit_Tracking_Log_for_2023-04-30.md` | File created. | True |
| 1056 | Book Reading Lists | `Book_Reading_Lists.md` | File created. | True |
| 1057 | Movie Recommendations | `Movie_Recommendations.md` | File created. | True |
| 1058 | Grocery List | `Grocery_List.md` | File created. | True |
| 1059 | Gift Ideas for Various Occasions | `Gift_Ideas_for_Various_Occasions.md` | File created. | True |
| 1060 | Weekly Workout Plan | `Weekly_Workout_Plan.md` | File created. | True |
| 1061 | Food Recipes | `Food_Recipes.md` | File created. | True |
| 1062 | Inspirational Quotes Collection | `Inspirational_Quotes_Collection.md` | File created. | True |
| 1063 | Funny Quotes Collection | `Funny_Quotes_Collection.md` | File created. | True |
| 1064 | Movie Quotes Collection | `Movie_Quotes_Collection.md` | File created. | True |
| 1065 | My Bucket List ([x] = done, [ ] = not done)) | `My_Bucket_List_([x]_=_done,_[_]_=_not_done)).md` | File created. | True |

## 4. Final directory listing (`~/backups/simple_note/`, 28 entries)

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

## 5. Notes that could not be exported

**None.** All 28 of 28 notes were written and read-back-verified.

## 6. Limitations / honest scope

- The target filesystem is the AppWorld `file_system` app of the simulated user Anita Burch; this workspace file is a report, not the app database.
- Verification = byte-for-byte equality between `show_note` content and the read-back `show_file` content, plus a directory listing showing exactly 28 `.md` entries and no extras.
- No knowledge IDs were used as current-effective facts for this step; the one VERIFIED knowledge entry (`appworld-api:e73afa...`) only restates the active task instruction.
- Final whole-goal integration/verification is Task 3.
