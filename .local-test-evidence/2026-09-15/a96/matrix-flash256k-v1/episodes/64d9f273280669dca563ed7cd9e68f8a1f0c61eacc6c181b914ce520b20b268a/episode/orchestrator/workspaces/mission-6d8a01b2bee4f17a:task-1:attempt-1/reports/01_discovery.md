# 01 — Discovery Report (Mission task-1)

No mutations were performed in this task. Everything below is read-only observation.

## 1. Current date / year reference

There is no public API that returns "today" directly. The reference was pinned from independent evidence:

- Newest file creation timestamp in the shared file-system state: `2023-05-18T02:42:58` (`moms_new_dress.jpg`).
- Newest Gmail inbox thread: `2023-05-17T16:18:37` ("New Employee Onboarding").
- The `file_system` login JWT expires at `2023-05-18T12:14:58 UTC` (claims `exp=1684412098`), and the `gmail` login JWT expires at `2023-05-18T12:25:41 UTC` (`exp=1684412741`).

Conclusion used for the plan: **the simulated "now" is on 2023-05-18, so the current year = 2023.** Therefore "files not from this year" = every file whose creation year is 2021 or 2022 (not 2023).

## 2. Accounts discovered (supervisor)

- `supervisor.show_profile()` → owner: **Nancy Ritter**, email `nan_ritt@gmail.com`, phone `2307354647`, birthday `1990-08-14`.
- `supervisor.show_account_passwords()` returned credentials for 9 apps (amazon, file_system, gmail, phone, simple_note, splitwise, spotify, todoist, venmo).
- file-system account (the one used here):
  - `file_system` username/email: **`nan_ritt@gmail.com`**
  - home directory: **`/home/nancy/`**
  - `file_system.show_account()` → first_name `Nancy`, last_name `Ritter`, email `nan_ritt@gmail.com`, registered_at `2022-05-19T10:04:07`, last_logged_in `2022-05-19T10:04:07`, verified `true`.
  - Note: no separate numeric account id is exposed by the app; the identifying account value is the email `nan_ritt@gmail.com`.

## 3. File-system API surface (exact names + key parameters)

App name: **`file_system`**.

Account/session:
- `file_system.login(username, password)` → `{access_token, token_type}`. Logged in as `nan_ritt@gmail.com`.
- `file_system.show_account(access_token)`.

Directory/file inspection:
- `file_system.show_directory(access_token, directory_path='/', substring=None, entry_type='all', recursive=True)` → list of path strings. `directory_path` may be absolute (`/...`) or home-relative (`~/...`).
- `file_system.show_file(file_path, access_token)` → `{file_id, path, content, created_at, updated_at}`. `created_at` is the creation date used for the rename prefix.
- `file_system.directory_exists(...)`, `file_system.file_exists(...)`.

Mutations available for the later tasks:
- `file_system.move_file(source_file_path, destination_file_path, access_token, overwrite=False, retain_dates=False)` → `{message, destination_file_path}`. **`retain_dates=True` should be used for the moves so the (renamed) files keep their created/updated dates, and so re-verification by creation date stays consistent.**
- `file_system.copy_file`, `file_system.move_directory`, `file_system.copy_directory`, `file_system.create_directory`, `file_system.delete_file`, `file_system.update_file`, `file_system.compress_directory`, `file_system.decompress_file`.

Note: there is NO dedicated "rename" API. A rename = `move_file` from the old path to a new path in the same directory.

Home directory listing (`~/`, non-recursive):
`/home/nancy/backups/`, `/home/nancy/bills/`, `/home/nancy/documents/`, `/home/nancy/downloads/`, `/home/nancy/photographs/`, `/home/nancy/trash/`.

Existing trash contents (pre-change), 9 files:
`DIY_home_improvement_guide.docx`, `cooking_masterclass_videos.zip`, `exotic_recipe_adventures.pdf`, `foreign_language_podcasts.zip`, `health_and_wellness_podcasts.mp3`, `historical_fiction_novel.epub`, `investment_strategies_ebook.epub`, `nature_documentary_series.mp4`, `travel_destination_photo_gallery.rar`.

## 4. Full pre-change listing of `~/downloads/` (name + creation date)

100 files, all directly in `~/downloads/` (no sub-directories). Sorted by creation date.

| # | File name | created_at |
|---|-----------|------------|
| 1 | financial_growth_analysis.xlsx | 2021-06-14T11:35:25 |
| 2 | cooking_tips_and_tricks_videos.zip | 2021-06-22T10:18:35 |
| 3 | language_learning_podcasts.mp3 | 2021-06-26T09:02:20 |
| 4 | workout_progress_tracker.doc | 2021-08-26T08:34:37 |
| 5 | world_travel_itinerary.docx | 2021-09-09T08:09:19 |
| 6 | world_landmarks_photo_album.zip | 2021-11-11T10:43:46 |
| 7 | data_visualization_examples.ppt | 2021-12-06T09:21:51 |
| 8 | recipe_collection.pdf | 2022-01-24T10:51:39 |
| 9 | fashion_design_sketches.rar | 2022-02-18T10:39:58 |
| 10 | fashion_design_inspiration_gallery.zip | 2022-03-06T08:45:09 |
| 11 | photography_competition_entries.rar | 2022-03-21T09:18:34 |
| 12 | travel_adventures_journal.doc | 2022-04-06T11:27:53 |
| 13 | virtual_reality_gaming_experience.zip | 2022-05-14T11:53:10 |
| 14 | delicious_recipe_videos.zip | 2022-07-07T08:48:52 |
| 15 | art_inspiration_sketches.zip | 2022-07-07T09:27:11 |
| 16 | cute_cat_gifs_collection.gif | 2022-07-25T08:43:06 |
| 17 | ocean_wave_relaxation_audio.mp3 | 2022-08-05T08:51:30 |
| 18 | space_exploration_videos.zip | 2022-09-11T08:22:01 |
| 19 | dog_food.jpg | 2022-09-15T12:48:45 |
| 20 | dinner_date.jpg | 2022-10-06T15:46:52 |
| 21 | subscription_service.jpg | 2022-10-22T12:32:45 |
| 22 | food_processor.pdf | 2022-11-21T17:51:34 |
| 23 | health_checkup.jpg | 2022-11-26T15:01:10 |
| 24 | DIY_home_repair_guide.docx | 2022-12-04T10:24:57 |
| 25 | transportation.pdf | 2022-12-16T16:33:49 |
| 26 | car_repair_sibling.pdf | 2022-12-18T06:24:12 |
| 27 | chocolate.jpg | 2022-12-28T05:01:07 |
| 28 | mindfulness_meditation_audio_sessions.mp3 | 2023-01-03T11:23:34 |
| 29 | scientific_research_paper.pdf | 2023-01-11T08:42:57 |
| 30 | travel_adventure_diary.docx | 2023-01-19T09:48:30 |
| 31 | ice_bucket.pdf | 2023-01-20T00:07:50 |
| 32 | dumbbells.jpg | 2023-01-27T20:35:38 |
| 33 | clean_up_supplies.jpg | 2023-02-20T21:14:23 |
| 34 | puzzle_solvers_guide.pdf | 2023-02-21T14:35:05 |
| 35 | wine_charms.pdf | 2023-02-23T07:03:01 |
| 36 | marketing_materials.jpg | 2023-02-24T11:35:28 |
| 37 | meeting_room_rental.jpg | 2023-02-24T19:38:05 |
| 38 | training_course.jpg | 2023-02-26T02:05:09 |
| 39 | punch_bowl.jpg | 2023-03-02T02:54:02 |
| 40 | new_years_eve_party.jpg | 2023-03-03T19:23:55 |
| 41 | table_rentals.pdf | 2023-03-03T20:38:35 |
| 42 | emergency_fund.jpg | 2023-03-06T21:22:31 |
| 43 | toiletries.jpg | 2023-03-06T23:59:23 |
| 44 | monthly_groceries.jpg | 2023-03-08T16:17:45 |
| 45 | bike_pump.jpg | 2023-03-10T02:19:06 |
| 46 | board_games.jpg | 2023-03-12T12:01:46 |
| 47 | foam_roller.pdf | 2023-03-14T11:41:27 |
| 48 | candy.pdf | 2023-03-15T06:23:34 |
| 49 | new_dress.jpg | 2023-03-18T07:38:35 |
| 50 | earplugs.pdf | 2023-03-18T08:44:43 |
| 51 | houseplants.jpg | 2023-03-19T17:47:39 |
| 52 | dads_new_phone.pdf | 2023-03-26T04:14:04 |
| 53 | marketing_materials.pdf | 2023-03-27T07:01:57 |
| 54 | bike_lights.jpg | 2023-03-30T03:02:29 |
| 55 | holiday_baking_supplies.pdf | 2023-04-01T03:58:02 |
| 56 | portable_charger.pdf | 2023-04-01T14:53:19 |
| 57 | concert_programs.jpg | 2023-04-04T07:36:45 |
| 58 | wine_opener.jpg | 2023-04-05T21:39:29 |
| 59 | gardening_book.jpg | 2023-04-08T14:38:01 |
| 60 | dinner_party.jpg | 2023-04-10T06:41:14 |
| 61 | supplements.pdf | 2023-04-10T20:45:26 |
| 62 | office_decorations.jpg | 2023-04-11T04:12:15 |
| 63 | office_utilities.pdf | 2023-04-11T08:43:51 |
| 64 | family_photoshoot.jpg | 2023-04-12T15:59:44 |
| 65 | trellis.pdf | 2023-04-14T23:29:51 |
| 66 | mulch.pdf | 2023-04-15T23:39:08 |
| 67 | merchandise.jpg | 2023-04-16T22:27:00 |
| 68 | guide_service.jpg | 2023-04-18T17:31:42 |
| 69 | fitness_tracker.jpg | 2023-04-19T00:30:10 |
| 70 | new_sofa.jpg | 2023-04-24T00:31:19 |
| 71 | team_lunch.jpg | 2023-04-24T22:22:57 |
| 72 | online_course.pdf | 2023-04-25T01:44:27 |
| 73 | office_lighting.pdf | 2023-04-26T23:22:49 |
| 74 | garden_decor.pdf | 2023-04-30T21:37:13 |
| 75 | office_stationery.jpg | 2023-05-01T11:38:55 |
| 76 | cleaning_supplies.jpg | 2023-05-03T01:01:26 |
| 77 | first_aid_kit.jpg | 2023-05-03T02:49:01 |
| 78 | cycling_shorts.jpg | 2023-05-05T07:17:53 |
| 79 | saddle_bag.pdf | 2023-05-09T00:03:13 |
| 80 | vacuum_cleaner.pdf | 2023-05-09T07:06:30 |
| 81 | childhood_memories.zip | 2023-05-09T08:50:04 |
| 82 | hobby_supplies.jpg | 2023-05-10T00:35:32 |
| 83 | dinner_party_cousins.jpg | 2023-05-10T03:40:05 |
| 84 | escape_room_merchandise.pdf | 2023-05-10T05:37:04 |
| 85 | climbing_shoes.jpg | 2023-05-11T02:08:25 |
| 86 | timers.pdf | 2023-05-11T03:00:29 |
| 87 | chairs.jpg | 2023-05-12T23:27:10 |
| 88 | game_expansion_pack.jpg | 2023-05-13T20:07:01 |
| 89 | shower_curtain.pdf | 2023-05-14T09:15:12 |
| 90 | kitchen_utensils.jpg | 2023-05-14T15:17:22 |
| 91 | concert_t_shirt.jpg | 2023-05-15T08:40:47 |
| 92 | gas_bill.pdf | 2023-05-15T14:18:37 |
| 93 | chalk_bag.pdf | 2023-05-15T20:48:56 |
| 94 | groceries_receipt.jpg | 2023-05-16T04:52:13 |
| 95 | ski_insurance.pdf | 2023-05-16T04:55:57 |
| 96 | volunteer_t_shirts.jpg | 2023-05-16T15:12:03 |
| 97 | game_night.jpg | 2023-05-16T17:00:39 |
| 98 | medical_insurance.pdf | 2023-05-17T01:14:28 |
| 99 | ice_cream.jpg | 2023-05-17T01:16:06 |
| 100 | moms_new_dress.jpg | 2023-05-18T02:42:58 |

Year distribution: 2021 → 7 files, 2022 → 20 files, 2023 → 73 files. Total 100.

## 5. Concrete plan

### 5a. Rename pattern (task-2)
For **every** file in `~/downloads/`, rename it by prefixing its own creation date in `YYYY-MM-DD` form, using the file's `created_at` (first 10 characters). New name = `'<YYYY-MM-DD>_<original name>'`, e.g. `saddle_bag.pdf` (created 2023-05-09T00:03:13) → `2023-05-09_saddle_bag.pdf`.
Because there is no rename API, each rename = `file_system.move_file(source=~/downloads/<old>, destination=~/downloads/<new>, retain_dates=True)` in place. All 100 files get a prefix.

### 5b. Move to `~/trash/` (task-3)
After renaming, move every file whose creation year ≠ 2023 (i.e. 2021 or 2022) from `~/downloads/` to `~/trash/`, using the renamed name. `retain_dates=True`.

Exact set of 27 files to move (post-rename names):

| # | Renamed name (to move to ~/trash/) | Original name | created_at |
|---|-------------------------------------|---------------|------------|
| 1 | 2021-06-14_financial_growth_analysis.xlsx | financial_growth_analysis.xlsx | 2021-06-14T11:35:25 |
| 2 | 2021-06-22_cooking_tips_and_tricks_videos.zip | cooking_tips_and_tricks_videos.zip | 2021-06-22T10:18:35 |
| 3 | 2021-06-26_language_learning_podcasts.mp3 | language_learning_podcasts.mp3 | 2021-06-26T09:02:20 |
| 4 | 2021-08-26_workout_progress_tracker.doc | workout_progress_tracker.doc | 2021-08-26T08:34:37 |
| 5 | 2021-09-09_world_travel_itinerary.docx | world_travel_itinerary.docx | 2021-09-09T08:09:19 |
| 6 | 2021-11-11_world_landmarks_photo_album.zip | world_landmarks_photo_album.zip | 2021-11-11T10:43:46 |
| 7 | 2021-12-06_data_visualization_examples.ppt | data_visualization_examples.ppt | 2021-12-06T09:21:51 |
| 8 | 2022-01-24_recipe_collection.pdf | recipe_collection.pdf | 2022-01-24T10:51:39 |
| 9 | 2022-02-18_fashion_design_sketches.rar | fashion_design_sketches.rar | 2022-02-18T10:39:58 |
| 10 | 2022-03-06_fashion_design_inspiration_gallery.zip | fashion_design_inspiration_gallery.zip | 2022-03-06T08:45:09 |
| 11 | 2022-03-21_photography_competition_entries.rar | photography_competition_entries.rar | 2022-03-21T09:18:34 |
| 12 | 2022-04-06_travel_adventures_journal.doc | travel_adventures_journal.doc | 2022-04-06T11:27:53 |
| 13 | 2022-05-14_virtual_reality_gaming_experience.zip | virtual_reality_gaming_experience.zip | 2022-05-14T11:53:10 |
| 14 | 2022-07-07_delicious_recipe_videos.zip | delicious_recipe_videos.zip | 2022-07-07T08:48:52 |
| 15 | 2022-07-07_art_inspiration_sketches.zip | art_inspiration_sketches.zip | 2022-07-07T09:27:11 |
| 16 | 2022-07-25_cute_cat_gifs_collection.gif | cute_cat_gifs_collection.gif | 2022-07-25T08:43:06 |
| 17 | 2022-08-05_ocean_wave_relaxation_audio.mp3 | ocean_wave_relaxation_audio.mp3 | 2022-08-05T08:51:30 |
| 18 | 2022-09-11_space_exploration_videos.zip | space_exploration_videos.zip | 2022-09-11T08:22:01 |
| 19 | 2022-09-15_dog_food.jpg | dog_food.jpg | 2022-09-15T12:48:45 |
| 20 | 2022-10-06_dinner_date.jpg | dinner_date.jpg | 2022-10-06T15:46:52 |
| 21 | 2022-10-22_subscription_service.jpg | subscription_service.jpg | 2022-10-22T12:32:45 |
| 22 | 2022-11-21_food_processor.pdf | food_processor.pdf | 2022-11-21T17:51:34 |
| 23 | 2022-11-26_health_checkup.jpg | health_checkup.jpg | 2022-11-26T15:01:10 |
| 24 | 2022-12-04_DIY_home_repair_guide.docx | DIY_home_repair_guide.docx | 2022-12-04T10:24:57 |
| 25 | 2022-12-16_transportation.pdf | transportation.pdf | 2022-12-16T16:33:49 |
| 26 | 2022-12-18_car_repair_sibling.pdf | car_repair_sibling.pdf | 2022-12-18T06:24:12 |
| 27 | 2022-12-28_chocolate.jpg | chocolate.jpg | 2022-12-28T05:01:07 |

The remaining **73 files (year 2023)** stay in `~/downloads/`, renamed only.

## 6. Post-change expectations (for task-4 verification)

- `~/downloads/` should end with **73 files**, all named `2023-*_<original>`.
- `~/trash/` should end with **36 files** = 9 pre-existing + 27 moved.
- No file content is changed; only names + locations.
