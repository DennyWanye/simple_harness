# Simple Note Export — Reconnaissance Inventory

**Task:** mission-a449d575e8358d31:task-1 (reconnaissance)
**Root goal:** Export all Simple Note notes to `~/backups/simple_note/`, files named after the
note title with white space replaced by `_`, extension `.md`.
**Method:** `appworld_execute` Python shell (persistent), public app APIs only. All notes were
retrieved live from the `simple_note` app; all file-system facts were read live from `file_system`.

## 1. Environment / method summary

- Shared AppWorld shell; application state persists across calls.
- Discovered API catalog via `apis.api_docs.show_app_descriptions()`.
- Per-app API lists via `apis.api_docs.show_api_descriptions(app_name=...)`.
- Full signatures via `apis.api_docs.show_api_doc(app_name=..., api_name=...)`.
- Simulated user + credentials via `apis.supervisor.show_profile()` and
  `apis.supervisor.show_account_passwords()`.
- Notes listed via `apis.simple_note.search_notes(...)` and contents via
  `apis.simple_note.show_note(...)`.
- Baseline home directory read via `apis.file_system.show_directory(...)`.

## 2. Discovered API signatures

### api_docs
- `apis.api_docs.show_app_descriptions()` -> list of `{name, description}`.

### supervisor
- `apis.supervisor.show_active_task()` -> `{instruction, status, answer}`.
- `apis.supervisor.show_profile()` -> `{first_name, last_name, email, phone_number, birthday, sex}`.
- `apis.supervisor.show_addresses()`
- `apis.supervisor.show_payment_cards()`
- `apis.supervisor.show_account_passwords()` -> `[{account_name, password}]`.
- `apis.supervisor.complete_task()` (final step only).

### simple_note
- `login(username, password)` -> `{access_token, token_type}`.  username = account email.
- `search_notes(access_token, query='', tags=None, pinned=None, dont_reorder_pinned=None, page_index=0, page_limit=5, sort_by=None)`
  -> list of `{note_id, title, tags, created_at, updated_at, pinned}` (NO content).
  `page_limit` max 20; `sort_by` in `{+created_at, -created_at, +updated_at, -updated_at}`.
- `show_note(note_id, access_token)` -> `{note_id, title, content, tags, created_at, updated_at, pinned}`.
- Other write APIs (not used in this task, relevant to Task 2/3):
  `create_note`, `update_note`, `delete_note`, `add_content_to_note`.

### file_system
- `login(username, password)` -> `{access_token, token_type}`.
- `show_directory(access_token, ...)` -> list of absolute paths (recursive).
- `directory_exists(access_token, directory_path)` -> `{exists: bool}`.
- `create_directory`, `file_exists`, `create_file`, `show_file`, `update_file`, `delete_file`,
  `copy_file`, `move_file`, `copy_directory`, `move_directory`, `compress_directory`, `decompress_file`.

## 3. Accounts / identities (from the supervisor, live)

- Supervisor profile: **Anita Burch**, email `anita.burch@gmail.com`, phone `3643463570`,
  birthday `1997-03-10`, sex `female`.
- Active task instruction (verbatim): "Export all my Simple Note notes to \"~/backups/simple_note/\" directory in my file system. The files should be named according to the note title, replacing white space with \"_\", and the extension should be \".md\"."
- The simulated user IS the `simple_note` account (same email). Login succeeded with the
  supervisor-provided `simple_note` password `]ic5XP5` and returned a bearer access token.
- `file_system` is a separate account with its own password `tXQIUXl`; login also succeeded
  (email `anita.burch@gmail.com`).
- `file_system` home is `/home/anita/` (equivalently `~/`). `~/backups/` **exists**
  (contains `laptop.zip`, `phone.zip`); `~/backups/simple_note/` does **not** exist yet.

## 4. Note inventory (28 notes — title captured VERBATIM)

All 28 notes retrieved by full pagination of `search_notes` (no query filter). Confirmed identical
set (28 ids, 1056..1083) under two orderings. Titles below are exact strings (see quoting).

| # | note_id | title (verbatim) | tags | pinned | created | updated | items | content summary |
|---|---------|------------------|------|--------|---------|---------|-------|-----------------|
| 1 | 1056 | `Book Reading Lists` | leisure, list | no | 2023-05-11 | 2023-05-11 | 17 books | "# Book Reading Lists" — 17 books, each with `authors` and `genre`. |
| 2 | 1057 | `Movie Recommendations` | leisure, list | no | 2023-04-05 | 2023-04-05 | 25 movies | 25 films, each with `director` and `genre`. |
| 3 | 1058 | `Grocery List` | household, list | no | 2023-04-27 | 2023-04-27 | 24 items | 24 grocery items with quantities. |
| 4 | 1059 | `Gift Ideas for Various Occasions` | shopping, list | no | 2022-09-11 | 2022-09-11 | 10 occasions / 100 ideas | 10 occasions, 10 ideas each. |
| 5 | 1060 | `Weekly Workout Plan` | health | no | 2023-03-20 | 2023-03-20 | 7 days | Day-by-day plan (monday..sunday) with exercises + `duration_mins`. |
| 6 | 1061 | `Food Recipes` | cooking | no | 2023-01-17 | 2023-01-17 | 5 recipes | 5 recipes with ingredients/instructions/favorite. |
| 7 | 1062 | `Inspirational Quotes Collection` | quotes | yes | 2022-10-07 | 2022-10-07 | 7 quotes | 7 quotes, each with author line. |
| 8 | 1063 | `Funny Quotes Collection` | quotes | no | 2023-02-01 | 2023-02-01 | 4 quotes | 4 quotes with attribution. |
| 9 | 1064 | `Movie Quotes Collection` | quotes | no | 2022-06-18 | 2022-06-18 | 9 quotes | 9 movie quotes with source film/year. |
| 10 | 1065 | `My Bucket List ([x] = done, [ ] = not done))` | life | yes | 2022-12-12 | 2022-12-12 | 7 items | 7 checkbox bucket-list items. |
| 11 | 1066 | `Habit Tracking Log for 2023-05-17` | habit-tracker | yes | 2023-05-17 | 2023-05-17 | 10 habits | Daily yes/no habit tracker. |
| 12 | 1067 | `Habit Tracking Log for 2023-05-16` | habit-tracker | yes | 2023-05-16 | 2023-05-16 | 10 habits | Daily yes/no habit tracker. |
| 13 | 1068 | `Habit Tracking Log for 2023-05-15` | habit-tracker | yes | 2023-05-15 | 2023-05-15 | 10 habits | Daily yes/no habit tracker. |
| 14 | 1069 | `Habit Tracking Log for 2023-05-14` | habit-tracker | yes | 2023-05-14 | 2023-05-14 | 10 habits | Daily yes/no habit tracker. |
| 15 | 1070 | `Habit Tracking Log for 2023-05-13` | habit-tracker | yes | 2023-05-13 | 2023-05-13 | 10 habits | Daily yes/no habit tracker. |
| 16 | 1071 | `Habit Tracking Log for 2023-05-12` | habit-tracker | yes | 2023-05-12 | 2023-05-12 | 10 habits | Daily yes/no habit tracker. |
| 17 | 1072 | `Habit Tracking Log for 2023-05-11` | habit-tracker | yes | 2023-05-11 | 2023-05-11 | 10 habits | Daily yes/no habit tracker. |
| 18 | 1073 | `Habit Tracking Log for 2023-05-10` | habit-tracker | yes | 2023-05-10 | 2023-05-10 | 10 habits | Daily yes/no habit tracker. |
| 19 | 1074 | `Habit Tracking Log for 2023-05-09` | habit-tracker | yes | 2023-05-09 | 2023-05-09 | 10 habits | Daily yes/no habit tracker. |
| 20 | 1075 | `Habit Tracking Log for 2023-05-08` | habit-tracker | yes | 2023-05-08 | 2023-05-08 | 10 habits | Daily yes/no habit tracker. |
| 21 | 1076 | `Habit Tracking Log for 2023-05-07` | habit-tracker | yes | 2023-05-07 | 2023-05-07 | 10 habits | Daily yes/no habit tracker. |
| 22 | 1077 | `Habit Tracking Log for 2023-05-06` | habit-tracker | yes | 2023-05-06 | 2023-05-06 | 10 habits | Daily yes/no habit tracker (content also includes habits; see note). |
| 23 | 1078 | `Habit Tracking Log for 2023-05-05` | habit-tracker | yes | 2023-05-05 | 2023-05-05 | 10 habits | Daily yes/no habit tracker. |
| 24 | 1079 | `Habit Tracking Log for 2023-05-04` | habit-tracker | yes | 2023-05-04 | 2023-05-04 | 10 habits | Daily yes/no habit tracker. |
| 25 | 1080 | `Habit Tracking Log for 2023-05-03` | habit-tracker | yes | 2023-05-03 | 2023-05-03 | 10 habits | Daily yes/no habit tracker. |
| 26 | 1081 | `Habit Tracking Log for 2023-05-02` | habit-tracker | yes | 2023-05-02 | 2023-05-02 | 10 habits | Daily yes/no habit tracker. |
| 27 | 1082 | `Habit Tracking Log for 2023-05-01` | habit-tracker | yes | 2023-05-01 | 2023-05-01 | 10 habits | Daily yes/no habit tracker. |
| 28 | 1083 | `Habit Tracking Log for 2023-04-30` | habit-tracker | yes | 2023-04-30 | 2023-04-30 | 10 habits | Daily yes/no habit tracker. |

**Total: 28 notes.** The 18 `Habit Tracking Log ...` notes share the same template:
header "# Daily Habit Tracker (yes/no questions to answer daily)" followed by 10
`habit: yes|no` lines.

## 5. Derived target filenames (whitespace -> `_`, + `.md`)

Produced by `re.sub(r"\s+", "_", title) + ".md"`. Titles are preserved otherwise (brackets,
`=`, commas and periods are NOT altered).

1. `Book_Reading_Lists.md`
2. `Movie_Recommendations.md`
3. `Grocery_List.md`
4. `Gift_Ideas_for_Various_Occasions.md`
5. `Weekly_Workout_Plan.md`
6. `Food_Recipes.md`
7. `Inspirational_Quotes_Collection.md`
8. `Funny_Quotes_Collection.md`
9. `Movie_Quotes_Collection.md`
10. `My_Bucket_List_([x]_=_done,_[_]_=_not_done)).md`
11. `Habit_Tracking_Log_for_2023-05-17.md`
12. `Habit_Tracking_Log_for_2023-05-16.md`
13. `Habit_Tracking_Log_for_2023-05-15.md`
14. `Habit_Tracking_Log_for_2023-05-14.md`
15. `Habit_Tracking_Log_for_2023-05-13.md`
16. `Habit_Tracking_Log_for_2023-05-12.md`
17. `Habit_Tracking_Log_for_2023-05-11.md`
18. `Habit_Tracking_Log_for_2023-05-10.md`
19. `Habit_Tracking_Log_for_2023-05-09.md`
20. `Habit_Tracking_Log_for_2023-05-08.md`
21. `Habit_Tracking_Log_for_2023-05-07.md`
22. `Habit_Tracking_Log_for_2023-05-06.md`
23. `Habit_Tracking_Log_for_2023-05-05.md`
24. `Habit_Tracking_Log_for_2023-05-04.md`
25. `Habit_Tracking_Log_for_2023-05-03.md`
26. `Habit_Tracking_Log_for_2023-05-02.md`
27. `Habit_Tracking_Log_for_2023-05-01.md`
28. `Habit_Tracking_Log_for_2023-04-30.md`

All 28 filenames are distinct — no collisions after whitespace replacement.

## 6. Errors, limitations, and notes for downstream tasks

- First `apis.file_system.show_account()` call returned HTTP 401
  (`"You are either not authorized ... or your access token is missing, invalid or expired."`).
  Fix: call `apis.file_system.login(...)` first; subsequent calls succeeded.
- `simple_note.search_notes` never returns note content; `show_note(note_id, access_token)`
  is required per note.
- `page_limit` is capped at 20, so pagination is mandatory (28 notes -> pages 0 and 1).
- Access tokens are JWT-like and time-limited; Task 2/3 should re-login if a call returns 401.
- `~/backups/simple_note/` does NOT exist yet; `~/backups/` does. Task 2 must create it
  (e.g. `create_directory(directory_path='~/backups/simple_note')`).
- The note titles contain characters such as `(`, `)`, `[`, `]`, `=`, `,`, `.` which are kept
  verbatim in filenames; only whitespace becomes `_`.
- No note was empty; content lengths range ~317–4452 chars.
- This report is a workspace deliverable, not the application database; the authoritative
  source remains the `simple_note` app queried live above.
