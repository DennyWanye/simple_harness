# DISCOVERY.md — AppWorld 文件系统发现记录 (Task-1)

> 本文件只做发现与计划，不执行任何重命名/移动等变更。执行由 Task-2 负责。

## 1. 操作账户与访问凭证

- owner: Nancy Ritter (nan_ritt@gmail.com)
- file_system 登录: POST /auth/token, username=`nan_ritt@gmail.com`, password=`UJa-ovY`
- 登录返回 access_token (JWT, token_type=Bearer)。

## 2. 当前模拟日期 / 年份

- access_token 的 JWT `exp` 解码为 `2023-05-18 12:29:50` (UTC)。
- `~/downloads/` 中最新的文件创建时间为 `2023-05-18T02:42:58` (moms_new_dress.jpg)。
- gmail 收件箱最新线程创建时间为 `2023-05-17T16:18:37`。
- 综合判断: 当前模拟日期为 **2023-05-18**，当前年份 = **2023**。
- 因此 "this year" = 2023；创建年份 != 2023 的文件应移入 `~/trash/`。

## 3. file_system 公开 API 清单 (apis.file_system.*)

- `show_account`
- `signup`
- `delete_account`
- `update_account_name`
- `login`
- `logout`
- `send_verification_code`
- `verify_account`
- `send_password_reset_code`
- `reset_password`
- `show_profile`
- `show_directory`
- `create_directory`
- `delete_directory`
- `directory_exists`
- `show_file`
- `create_file`
- `delete_file`
- `update_file`
- `file_exists`
- `copy_file`
- `move_file`
- `copy_directory`
- `move_directory`
- `compress_directory`
- `decompress_file`

## 4. 关键 API 用法 (已实测)

- `apis.file_system.show_directory(access_token=..., directory_path='~/downloads/', recursive=False, entry_type='all'|'files'|'directories', substring=None)` -> 返回 list[str]，元素为绝对路径。
- `apis.file_system.show_file(access_token=..., file_path=...)` -> 返回 dict: `{file_id, path, content, created_at, updated_at}`；`created_at` 格式 `YYYY-MM-DDTHH:MM:SS`。
- `apis.file_system.move_file(access_token=..., source_file_path=..., destination_file_path=..., overwrite=False, retain_dates=False)` -> 返回 `{message, destination_file_path}`。(供 Task-2 使用)
- `apis.file_system.file_exists(access_token=..., file_path=...)` / `directory_exists(...)` 可用于校验。

## 5. 目录状态 (发现时快照)

- `~/downloads/`: 共 100 个文件 (无子目录)。
- `~/trash/`: 共 9 个文件 (无子目录):
  - `/home/nancy/trash/DIY_home_improvement_guide.docx`
  - `/home/nancy/trash/cooking_masterclass_videos.zip`
  - `/home/nancy/trash/exotic_recipe_adventures.pdf`
  - `/home/nancy/trash/foreign_language_podcasts.zip`
  - `/home/nancy/trash/health_and_wellness_podcasts.mp3`
  - `/home/nancy/trash/historical_fiction_novel.epub`
  - `/home/nancy/trash/investment_strategies_ebook.epub`
  - `/home/nancy/trash/nature_documentary_series.mp4`
  - `/home/nancy/trash/travel_destination_photo_gallery.rar`

## 6. 逐文件计划表

规则: 新名称 = `创建日期(YYYY-MM-DD)` + `_` + 原始名称；若创建年份 != 2023 则同时移入 `~/trash/`。

| # | 原始名称 | created_at | 预期新名称 | 移入 ~/trash/ | 创建年份 |
|---|----------|------------|------------|---------------|----------|
| 1 | `DIY_home_repair_guide.docx` | 2022-12-04T10:24:57 | `2022-12-04_DIY_home_repair_guide.docx` | YES | 2022 |
| 2 | `art_inspiration_sketches.zip` | 2022-07-07T09:27:11 | `2022-07-07_art_inspiration_sketches.zip` | YES | 2022 |
| 3 | `bike_lights.jpg` | 2023-03-30T03:02:29 | `2023-03-30_bike_lights.jpg` | NO | 2023 |
| 4 | `bike_pump.jpg` | 2023-03-10T02:19:06 | `2023-03-10_bike_pump.jpg` | NO | 2023 |
| 5 | `board_games.jpg` | 2023-03-12T12:01:46 | `2023-03-12_board_games.jpg` | NO | 2023 |
| 6 | `candy.pdf` | 2023-03-15T06:23:34 | `2023-03-15_candy.pdf` | NO | 2023 |
| 7 | `car_repair_sibling.pdf` | 2022-12-18T06:24:12 | `2022-12-18_car_repair_sibling.pdf` | YES | 2022 |
| 8 | `chairs.jpg` | 2023-05-12T23:27:10 | `2023-05-12_chairs.jpg` | NO | 2023 |
| 9 | `chalk_bag.pdf` | 2023-05-15T20:48:56 | `2023-05-15_chalk_bag.pdf` | NO | 2023 |
| 10 | `childhood_memories.zip` | 2023-05-09T08:50:04 | `2023-05-09_childhood_memories.zip` | NO | 2023 |
| 11 | `chocolate.jpg` | 2022-12-28T05:01:07 | `2022-12-28_chocolate.jpg` | YES | 2022 |
| 12 | `clean_up_supplies.jpg` | 2023-02-20T21:14:23 | `2023-02-20_clean_up_supplies.jpg` | NO | 2023 |
| 13 | `cleaning_supplies.jpg` | 2023-05-03T01:01:26 | `2023-05-03_cleaning_supplies.jpg` | NO | 2023 |
| 14 | `climbing_shoes.jpg` | 2023-05-11T02:08:25 | `2023-05-11_climbing_shoes.jpg` | NO | 2023 |
| 15 | `concert_programs.jpg` | 2023-04-04T07:36:45 | `2023-04-04_concert_programs.jpg` | NO | 2023 |
| 16 | `concert_t_shirt.jpg` | 2023-05-15T08:40:47 | `2023-05-15_concert_t_shirt.jpg` | NO | 2023 |
| 17 | `cooking_tips_and_tricks_videos.zip` | 2021-06-22T10:18:35 | `2021-06-22_cooking_tips_and_tricks_videos.zip` | YES | 2021 |
| 18 | `cute_cat_gifs_collection.gif` | 2022-07-25T08:43:06 | `2022-07-25_cute_cat_gifs_collection.gif` | YES | 2022 |
| 19 | `cycling_shorts.jpg` | 2023-05-05T07:17:53 | `2023-05-05_cycling_shorts.jpg` | NO | 2023 |
| 20 | `dads_new_phone.pdf` | 2023-03-26T04:14:04 | `2023-03-26_dads_new_phone.pdf` | NO | 2023 |
| 21 | `data_visualization_examples.ppt` | 2021-12-06T09:21:51 | `2021-12-06_data_visualization_examples.ppt` | YES | 2021 |
| 22 | `delicious_recipe_videos.zip` | 2022-07-07T08:48:52 | `2022-07-07_delicious_recipe_videos.zip` | YES | 2022 |
| 23 | `dinner_date.jpg` | 2022-10-06T15:46:52 | `2022-10-06_dinner_date.jpg` | YES | 2022 |
| 24 | `dinner_party.jpg` | 2023-04-10T06:41:14 | `2023-04-10_dinner_party.jpg` | NO | 2023 |
| 25 | `dinner_party_cousins.jpg` | 2023-05-10T03:40:05 | `2023-05-10_dinner_party_cousins.jpg` | NO | 2023 |
| 26 | `dog_food.jpg` | 2022-09-15T12:48:45 | `2022-09-15_dog_food.jpg` | YES | 2022 |
| 27 | `dumbbells.jpg` | 2023-01-27T20:35:38 | `2023-01-27_dumbbells.jpg` | NO | 2023 |
| 28 | `earplugs.pdf` | 2023-03-18T08:44:43 | `2023-03-18_earplugs.pdf` | NO | 2023 |
| 29 | `emergency_fund.jpg` | 2023-03-06T21:22:31 | `2023-03-06_emergency_fund.jpg` | NO | 2023 |
| 30 | `escape_room_merchandise.pdf` | 2023-05-10T05:37:04 | `2023-05-10_escape_room_merchandise.pdf` | NO | 2023 |
| 31 | `family_photoshoot.jpg` | 2023-04-12T15:59:44 | `2023-04-12_family_photoshoot.jpg` | NO | 2023 |
| 32 | `fashion_design_inspiration_gallery.zip` | 2022-03-06T08:45:09 | `2022-03-06_fashion_design_inspiration_gallery.zip` | YES | 2022 |
| 33 | `fashion_design_sketches.rar` | 2022-02-18T10:39:58 | `2022-02-18_fashion_design_sketches.rar` | YES | 2022 |
| 34 | `financial_growth_analysis.xlsx` | 2021-06-14T11:35:25 | `2021-06-14_financial_growth_analysis.xlsx` | YES | 2021 |
| 35 | `first_aid_kit.jpg` | 2023-05-03T02:49:01 | `2023-05-03_first_aid_kit.jpg` | NO | 2023 |
| 36 | `fitness_tracker.jpg` | 2023-04-19T00:30:10 | `2023-04-19_fitness_tracker.jpg` | NO | 2023 |
| 37 | `foam_roller.pdf` | 2023-03-14T11:41:27 | `2023-03-14_foam_roller.pdf` | NO | 2023 |
| 38 | `food_processor.pdf` | 2022-11-21T17:51:34 | `2022-11-21_food_processor.pdf` | YES | 2022 |
| 39 | `game_expansion_pack.jpg` | 2023-05-13T20:07:01 | `2023-05-13_game_expansion_pack.jpg` | NO | 2023 |
| 40 | `game_night.jpg` | 2023-05-16T17:00:39 | `2023-05-16_game_night.jpg` | NO | 2023 |
| 41 | `garden_decor.pdf` | 2023-04-30T21:37:13 | `2023-04-30_garden_decor.pdf` | NO | 2023 |
| 42 | `gardening_book.jpg` | 2023-04-08T14:38:01 | `2023-04-08_gardening_book.jpg` | NO | 2023 |
| 43 | `gas_bill.pdf` | 2023-05-15T14:18:37 | `2023-05-15_gas_bill.pdf` | NO | 2023 |
| 44 | `groceries_receipt.jpg` | 2023-05-16T04:52:13 | `2023-05-16_groceries_receipt.jpg` | NO | 2023 |
| 45 | `guide_service.jpg` | 2023-04-18T17:31:42 | `2023-04-18_guide_service.jpg` | NO | 2023 |
| 46 | `health_checkup.jpg` | 2022-11-26T15:01:10 | `2022-11-26_health_checkup.jpg` | YES | 2022 |
| 47 | `hobby_supplies.jpg` | 2023-05-10T00:35:32 | `2023-05-10_hobby_supplies.jpg` | NO | 2023 |
| 48 | `holiday_baking_supplies.pdf` | 2023-04-01T03:58:02 | `2023-04-01_holiday_baking_supplies.pdf` | NO | 2023 |
| 49 | `houseplants.jpg` | 2023-03-19T17:47:39 | `2023-03-19_houseplants.jpg` | NO | 2023 |
| 50 | `ice_bucket.pdf` | 2023-01-20T00:07:50 | `2023-01-20_ice_bucket.pdf` | NO | 2023 |
| 51 | `ice_cream.jpg` | 2023-05-17T01:16:06 | `2023-05-17_ice_cream.jpg` | NO | 2023 |
| 52 | `kitchen_utensils.jpg` | 2023-05-14T15:17:22 | `2023-05-14_kitchen_utensils.jpg` | NO | 2023 |
| 53 | `language_learning_podcasts.mp3` | 2021-06-26T09:02:20 | `2021-06-26_language_learning_podcasts.mp3` | YES | 2021 |
| 54 | `marketing_materials.jpg` | 2023-02-24T11:35:28 | `2023-02-24_marketing_materials.jpg` | NO | 2023 |
| 55 | `marketing_materials.pdf` | 2023-03-27T07:01:57 | `2023-03-27_marketing_materials.pdf` | NO | 2023 |
| 56 | `medical_insurance.pdf` | 2023-05-17T01:14:28 | `2023-05-17_medical_insurance.pdf` | NO | 2023 |
| 57 | `meeting_room_rental.jpg` | 2023-02-24T19:38:05 | `2023-02-24_meeting_room_rental.jpg` | NO | 2023 |
| 58 | `merchandise.jpg` | 2023-04-16T22:27:00 | `2023-04-16_merchandise.jpg` | NO | 2023 |
| 59 | `mindfulness_meditation_audio_sessions.mp3` | 2023-01-03T11:23:34 | `2023-01-03_mindfulness_meditation_audio_sessions.mp3` | NO | 2023 |
| 60 | `moms_new_dress.jpg` | 2023-05-18T02:42:58 | `2023-05-18_moms_new_dress.jpg` | NO | 2023 |
| 61 | `monthly_groceries.jpg` | 2023-03-08T16:17:45 | `2023-03-08_monthly_groceries.jpg` | NO | 2023 |
| 62 | `mulch.pdf` | 2023-04-15T23:39:08 | `2023-04-15_mulch.pdf` | NO | 2023 |
| 63 | `new_dress.jpg` | 2023-03-18T07:38:35 | `2023-03-18_new_dress.jpg` | NO | 2023 |
| 64 | `new_sofa.jpg` | 2023-04-24T00:31:19 | `2023-04-24_new_sofa.jpg` | NO | 2023 |
| 65 | `new_years_eve_party.jpg` | 2023-03-03T19:23:55 | `2023-03-03_new_years_eve_party.jpg` | NO | 2023 |
| 66 | `ocean_wave_relaxation_audio.mp3` | 2022-08-05T08:51:30 | `2022-08-05_ocean_wave_relaxation_audio.mp3` | YES | 2022 |
| 67 | `office_decorations.jpg` | 2023-04-11T04:12:15 | `2023-04-11_office_decorations.jpg` | NO | 2023 |
| 68 | `office_lighting.pdf` | 2023-04-26T23:22:49 | `2023-04-26_office_lighting.pdf` | NO | 2023 |
| 69 | `office_stationery.jpg` | 2023-05-01T11:38:55 | `2023-05-01_office_stationery.jpg` | NO | 2023 |
| 70 | `office_utilities.pdf` | 2023-04-11T08:43:51 | `2023-04-11_office_utilities.pdf` | NO | 2023 |
| 71 | `online_course.pdf` | 2023-04-25T01:44:27 | `2023-04-25_online_course.pdf` | NO | 2023 |
| 72 | `photography_competition_entries.rar` | 2022-03-21T09:18:34 | `2022-03-21_photography_competition_entries.rar` | YES | 2022 |
| 73 | `portable_charger.pdf` | 2023-04-01T14:53:19 | `2023-04-01_portable_charger.pdf` | NO | 2023 |
| 74 | `punch_bowl.jpg` | 2023-03-02T02:54:02 | `2023-03-02_punch_bowl.jpg` | NO | 2023 |
| 75 | `puzzle_solvers_guide.pdf` | 2023-02-21T14:35:05 | `2023-02-21_puzzle_solvers_guide.pdf` | NO | 2023 |
| 76 | `recipe_collection.pdf` | 2022-01-24T10:51:39 | `2022-01-24_recipe_collection.pdf` | YES | 2022 |
| 77 | `saddle_bag.pdf` | 2023-05-09T00:03:13 | `2023-05-09_saddle_bag.pdf` | NO | 2023 |
| 78 | `scientific_research_paper.pdf` | 2023-01-11T08:42:57 | `2023-01-11_scientific_research_paper.pdf` | NO | 2023 |
| 79 | `shower_curtain.pdf` | 2023-05-14T09:15:12 | `2023-05-14_shower_curtain.pdf` | NO | 2023 |
| 80 | `ski_insurance.pdf` | 2023-05-16T04:55:57 | `2023-05-16_ski_insurance.pdf` | NO | 2023 |
| 81 | `space_exploration_videos.zip` | 2022-09-11T08:22:01 | `2022-09-11_space_exploration_videos.zip` | YES | 2022 |
| 82 | `subscription_service.jpg` | 2022-10-22T12:32:45 | `2022-10-22_subscription_service.jpg` | YES | 2022 |
| 83 | `supplements.pdf` | 2023-04-10T20:45:26 | `2023-04-10_supplements.pdf` | NO | 2023 |
| 84 | `table_rentals.pdf` | 2023-03-03T20:38:35 | `2023-03-03_table_rentals.pdf` | NO | 2023 |
| 85 | `team_lunch.jpg` | 2023-04-24T22:22:57 | `2023-04-24_team_lunch.jpg` | NO | 2023 |
| 86 | `timers.pdf` | 2023-05-11T03:00:29 | `2023-05-11_timers.pdf` | NO | 2023 |
| 87 | `toiletries.jpg` | 2023-03-06T23:59:23 | `2023-03-06_toiletries.jpg` | NO | 2023 |
| 88 | `training_course.jpg` | 2023-02-26T02:05:09 | `2023-02-26_training_course.jpg` | NO | 2023 |
| 89 | `transportation.pdf` | 2022-12-16T16:33:49 | `2022-12-16_transportation.pdf` | YES | 2022 |
| 90 | `travel_adventure_diary.docx` | 2023-01-19T09:48:30 | `2023-01-19_travel_adventure_diary.docx` | NO | 2023 |
| 91 | `travel_adventures_journal.doc` | 2022-04-06T11:27:53 | `2022-04-06_travel_adventures_journal.doc` | YES | 2022 |
| 92 | `trellis.pdf` | 2023-04-14T23:29:51 | `2023-04-14_trellis.pdf` | NO | 2023 |
| 93 | `vacuum_cleaner.pdf` | 2023-05-09T07:06:30 | `2023-05-09_vacuum_cleaner.pdf` | NO | 2023 |
| 94 | `virtual_reality_gaming_experience.zip` | 2022-05-14T11:53:10 | `2022-05-14_virtual_reality_gaming_experience.zip` | YES | 2022 |
| 95 | `volunteer_t_shirts.jpg` | 2023-05-16T15:12:03 | `2023-05-16_volunteer_t_shirts.jpg` | NO | 2023 |
| 96 | `wine_charms.pdf` | 2023-02-23T07:03:01 | `2023-02-23_wine_charms.pdf` | NO | 2023 |
| 97 | `wine_opener.jpg` | 2023-04-05T21:39:29 | `2023-04-05_wine_opener.jpg` | NO | 2023 |
| 98 | `workout_progress_tracker.doc` | 2021-08-26T08:34:37 | `2021-08-26_workout_progress_tracker.doc` | YES | 2021 |
| 99 | `world_landmarks_photo_album.zip` | 2021-11-11T10:43:46 | `2021-11-11_world_landmarks_photo_album.zip` | YES | 2021 |
| 100 | `world_travel_itinerary.docx` | 2021-09-09T08:09:19 | `2021-09-09_world_travel_itinerary.docx` | YES | 2021 |

## 7. 汇总

- `~/downloads/` 文件总数: 100
- 创建于 2023 (仅加前缀，保留在 ~/downloads/): 73
- 创建年份 != 2023 (加前缀后移入 ~/trash/): 27

## 8. 执行注意 (供 Task-2)

- "add prefix ... based on their creation dates": 前缀日期取文件 `created_at` 的日期部分，按原文件逐个使用，而不是统一用今天日期。
- 重命名需要 `move_file(source=原路径, destination=同目录带前缀新路径, retain_dates=True)`。
- 移入 trash 时必须使用**已加前缀**的新文件名（即先把文件重命名，再把该重命名后的文件移到 ~/trash/）。
- ~/trash/ 现有 9 个文件与下载目录中的文件名不冲突，但仍建议覆盖保护/校验。
