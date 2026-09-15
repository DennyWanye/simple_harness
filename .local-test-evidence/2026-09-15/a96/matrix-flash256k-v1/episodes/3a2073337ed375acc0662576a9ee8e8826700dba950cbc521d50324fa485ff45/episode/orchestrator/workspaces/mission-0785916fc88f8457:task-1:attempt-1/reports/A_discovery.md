# Task A — Simple Note Discovery Report

Task: mission-0785916fc88f8457:task-1 — "Discover the AppWorld Simple Note environment and user
accounts, enumerate all Simple Note notes with their exact titles and contents, and publish a
discovery report that establishes the export contract."

This report is a *discovery* artifact produced from live API calls in the AppWorld shell. It records
what was actually observed; nothing here is inferred from hidden answers.

## 1. Environment discovery

`apis.api_docs.show_app_descriptions()` lists the available apps:
`api_docs, supervisor, amazon, phone, file_system, spotify, venmo, gmail, splitwise, simple_note, todoist`.

The two apps relevant to the mission are `simple_note` (source of notes) and `file_system` (export
destination).

`apis.supervisor.show_active_task()` returned the instruction:
> Export all my Simple Note notes to "~/backups/simple_note/" directory in my file system. The files
> should be named according to the note title, replacing white space with "_", and the extension
> should be ".md".

## 2. Account discovery

Supervisor profile (`apis.supervisor.show_profile()`):
- first_name: Anita, last_name: Burch, email: anita.burch@gmail.com,
  phone_number: 3643463570, birthday: 1997-03-10, sex: female.
- Addresses: Home (247 Salinas Pines Suite 668, Seattle, Washington, US 11799),
  Work (7844 Joshua Shore Suite 460, Seattle, Washington, US 46946).

Credentials (`apis.supervisor.show_account_passwords()`) for the two apps used here:
- `simple_note` password: `]ic5XP5`
- `file_system` password: `tXQIUXl`

Both apps authenticate with the supervisor's email `anita.burch@gmail.com` plus the app password
(see `apis.<app>.login(username=<email>, password=<password>)`).

Authenticated account info:
- simple_note `show_account`: anita.burch@gmail.com, registered 2022-04-06T15:17:09,
  last_logged_in 2022-04-06T15:17:09, verified = True.
- file_system `show_account`: anita.burch@gmail.com, registered 2022-10-21T10:41:53,
  last_logged_in 2022-10-21T10:41:53, verified = True.

## 3. API contract (verified by calling docs and the APIs)

### simple_note
- `login(username, password)` → `{access_token, token_type}` (username = account email).
- `search_notes(access_token, query="", tags=None, pinned=None, dont_reorder_pinned=None,
  page_index=0, page_limit=5..20, sort_by=None)` → list of
  `{note_id, title, tags, created_at, updated_at, pinned}`. It does NOT return content. Pinned notes
  are reordered to the top by default. Pagination is real: page 0/1 returned 20 + 8 and page 2
  returned `[]`.
- `show_note(note_id, access_token)` → `{note_id, title, content, tags, created_at, updated_at,
  pinned}` — this is the source of the exact `content`.
- (Also available: create_note, update_note, add_content_to_note, delete_note, logout, etc.)

### file_system
- `login(username, password)` → `{access_token, token_type}`.
- `show_directory(access_token, directory_path=..., substring=None, entry_type='all',
  recursive=True)` → list of absolute paths. `directory_path` accepts absolute (`/...`) or
  home-relative (`~/...`). Note: passing bare `~` fails with 422 ("Directory with path /~/ is not
  available"); use `~/` or an absolute path.
- `create_directory(directory_path, access_token, recursive=False)` → creates dir (recursively if
  asked).
- `create_file(file_path, access_token, content="", overwrite=False)` → `{message, file_path}`.
- Also available: show_file, file_exists, directory_exists, update_file, delete_file, copy/move, etc.

## 4. File-system pre-state (before any export)

Home directory of the file_system app is `/home/anita/` (login user "anita").

`show_directory(directory_path='~/')` shows `~/backups/` already exists but contains only:
- `/home/anita/backups/laptop.zip`
- `/home/anita/backups/phone.zip`

`~/backups/simple_note/` does **not** exist yet. The export step must therefore create the
directory `~/backups/simple_note/` (equivalently `/home/anita/backups/simple_note/`) and then create
one `.md` file per note inside it.

## 5. Note inventory

`search_notes` enumerated **28** notes total (18 pinned habit-tracking notes first, then 10
unpinned notes). All 28 note_ids are distinct. Full contents were fetched with `show_note`.

| note_id | exact title | target filename (whitespace → `_`, + `.md`) | pinned | tags | content length |
|--------:|-------------|---------------------------------------------|:------:|------|---------------:|
| 1066 | Habit Tracking Log for 2023-05-17 | Habit_Tracking_Log_for_2023-05-17.md | true | habit-tracker | 328 |
| 1067 | Habit Tracking Log for 2023-05-16 | Habit_Tracking_Log_for_2023-05-16.md | true | habit-tracker | 328 |
| 1068 | Habit Tracking Log for 2023-05-15 | Habit_Tracking_Log_for_2023-05-15.md | true | habit-tracker | 327 |
| 1069 | Habit Tracking Log for 2023-05-14 | Habit_Tracking_Log_for_2023-05-14.md | true | habit-tracker | 328 |
| 1070 | Habit Tracking Log for 2023-05-13 | Habit_Tracking_Log_for_2023-05-13.md | true | habit-tracker | 328 |
| 1071 | Habit Tracking Log for 2023-05-12 | Habit_Tracking_Log_for_2023-05-12.md | true | habit-tracker | 328 |
| 1072 | Habit Tracking Log for 2023-05-11 | Habit_Tracking_Log_for_2023-05-11.md | true | habit-tracker | 328 |
| 1073 | Habit Tracking Log for 2023-05-10 | Habit_Tracking_Log_for_2023-05-10.md | true | habit-tracker | 328 |
| 1074 | Habit Tracking Log for 2023-05-09 | Habit_Tracking_Log_for_2023-05-09.md | true | habit-tracker | 328 |
| 1075 | Habit Tracking Log for 2023-05-08 | Habit_Tracking_Log_for_2023-05-08.md | true | habit-tracker | 328 |
| 1076 | Habit Tracking Log for 2023-05-07 | Habit_Tracking_Log_for_2023-05-07.md | true | habit-tracker | 328 |
| 1077 | Habit Tracking Log for 2023-05-06 | Habit_Tracking_Log_for_2023-05-06.md | true | habit-tracker | 327 |
| 1078 | Habit Tracking Log for 2023-05-05 | Habit_Tracking_Log_for_2023-05-05.md | true | habit-tracker | 328 |
| 1079 | Habit Tracking Log for 2023-05-04 | Habit_Tracking_Log_for_2023-05-04.md | true | habit-tracker | 327 |
| 1080 | Habit Tracking Log for 2023-05-03 | Habit_Tracking_Log_for_2023-05-03.md | true | habit-tracker | 328 |
| 1081 | Habit Tracking Log for 2023-05-02 | Habit_Tracking_Log_for_2023-05-02.md | true | habit-tracker | 327 |
| 1082 | Habit Tracking Log for 2023-05-01 | Habit_Tracking_Log_for_2023-05-01.md | true | habit-tracker | 328 |
| 1083 | Habit Tracking Log for 2023-04-30 | Habit_Tracking_Log_for_2023-04-30.md | true | habit-tracker | 328 |
| 1056 | Book Reading Lists | Book_Reading_Lists.md | false | leisure, list | 1202 |
| 1057 | Movie Recommendations | Movie_Recommendations.md | false | leisure, list | 1915 |
| 1058 | Grocery List | Grocery_List.md | false | household, list | 604 |
| 1059 | Gift Ideas for Various Occasions | Gift_Ideas_for_Various_Occasions.md | false | shopping, list | 3878 |
| 1060 | Weekly Workout Plan | Weekly_Workout_Plan.md | false | health | 1804 |
| 1061 | Food Recipes | Food_Recipes.md | false | cooking | 4452 |
| 1062 | Inspirational Quotes Collection | Inspirational_Quotes_Collection.md | true | quotes | 560 |
| 1063 | Funny Quotes Collection | Funny_Quotes_Collection.md | false | quotes | 360 |
| 1064 | Movie Quotes Collection | Movie_Quotes_Collection.md | false | quotes | 562 |
| 1065 | My Bucket List ([x] = done, [ ] = not done)) | My_Bucket_List_([x]_=_done,_[_]_=_not_done)).md | true | life | 317 |

The 28 derived filenames are all distinct (no collisions) even though two notes share very similar
titles ("Movie Quotes Collection" vs "Movie Recommendations", and the three "Quotes Collection"
notes).

### Exact title / content observations

The titles include embedded dates (habit logs) and one title with punctuation and brackets:
`My Bucket List ([x] = done, [ ] = not done))`. Applying "replace white space with `_`" to that
title yields `My_Bucket_List_([x]_=_done,_[_]_=_not_done))` — only the whitespace characters are
changed; parentheses, brackets, commas, equals signs and the trailing `))` are preserved as-is.
Likewise the habit titles keep their hyphens (`2023-05-17`).

Full contents (verbatim, as returned by `show_note`), grouped below.

#### note_id 1066 — "Habit Tracking Log for 2023-05-17"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: yes
ate_homemade_meals: yes
practiced_meditation: no
read_atleast_30_mins: yes
drank_adequate_water: yes
slept_over_7_hrs: yes
wrote_gratitude_journal: yes
limited_screen_time_to_1_hr: yes
practiced_good_posture: no
connected_with_friends: yes
```

#### note_id 1067 — "Habit Tracking Log for 2023-05-16"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: yes
ate_homemade_meals: yes
practiced_meditation: yes
read_atleast_30_mins: yes
drank_adequate_water: yes
slept_over_7_hrs: no
wrote_gratitude_journal: yes
limited_screen_time_to_1_hr: yes
practiced_good_posture: yes
connected_with_friends: no
```

#### note_id 1068 — "Habit Tracking Log for 2023-05-15"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: yes
ate_homemade_meals: no
practiced_meditation: yes
read_atleast_30_mins: no
drank_adequate_water: yes
slept_over_7_hrs: yes
wrote_gratitude_journal: yes
limited_screen_time_to_1_hr: no
practiced_good_posture: yes
connected_with_friends: yes
```

#### note_id 1069 — "Habit Tracking Log for 2023-05-14"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: yes
ate_homemade_meals: yes
practiced_meditation: yes
read_atleast_30_mins: yes
drank_adequate_water: yes
slept_over_7_hrs: no
wrote_gratitude_journal: no
limited_screen_time_to_1_hr: yes
practiced_good_posture: yes
connected_with_friends: yes
```

#### note_id 1070 — "Habit Tracking Log for 2023-05-13"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: yes
ate_homemade_meals: yes
practiced_meditation: yes
read_atleast_30_mins: yes
drank_adequate_water: no
slept_over_7_hrs: yes
wrote_gratitude_journal: yes
limited_screen_time_to_1_hr: yes
practiced_good_posture: no
connected_with_friends: yes
```

#### note_id 1071 — "Habit Tracking Log for 2023-05-12"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: yes
ate_homemade_meals: yes
practiced_meditation: no
read_atleast_30_mins: yes
drank_adequate_water: yes
slept_over_7_hrs: yes
wrote_gratitude_journal: yes
limited_screen_time_to_1_hr: yes
practiced_good_posture: no
connected_with_friends: yes
```

#### note_id 1072 — "Habit Tracking Log for 2023-05-11"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: yes
ate_homemade_meals: no
practiced_meditation: yes
read_atleast_30_mins: yes
drank_adequate_water: yes
slept_over_7_hrs: no
wrote_gratitude_journal: yes
limited_screen_time_to_1_hr: yes
practiced_good_posture: yes
connected_with_friends: yes
```

#### note_id 1073 — "Habit Tracking Log for 2023-05-10"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: yes
ate_homemade_meals: yes
practiced_meditation: yes
read_atleast_30_mins: yes
drank_adequate_water: yes
slept_over_7_hrs: no
wrote_gratitude_journal: yes
limited_screen_time_to_1_hr: yes
practiced_good_posture: yes
connected_with_friends: no
```

#### note_id 1074 — "Habit Tracking Log for 2023-05-09"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: yes
ate_homemade_meals: no
practiced_meditation: yes
read_atleast_30_mins: yes
drank_adequate_water: yes
slept_over_7_hrs: yes
wrote_gratitude_journal: yes
limited_screen_time_to_1_hr: no
practiced_good_posture: yes
connected_with_friends: yes
```

#### note_id 1075 — "Habit Tracking Log for 2023-05-08"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: yes
ate_homemade_meals: yes
practiced_meditation: yes
read_atleast_30_mins: yes
drank_adequate_water: yes
slept_over_7_hrs: no
wrote_gratitude_journal: no
limited_screen_time_to_1_hr: yes
practiced_good_posture: yes
connected_with_friends: yes
```

#### note_id 1076 — "Habit Tracking Log for 2023-05-07"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: yes
ate_homemade_meals: yes
practiced_meditation: yes
read_atleast_30_mins: yes
drank_adequate_water: yes
slept_over_7_hrs: no
wrote_gratitude_journal: no
limited_screen_time_to_1_hr: yes
practiced_good_posture: yes
connected_with_friends: yes
```

#### note_id 1077 — "Habit Tracking Log for 2023-05-06"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: yes
ate_homemade_meals: no
practiced_meditation: yes
read_atleast_30_mins: no
drank_adequate_water: yes
slept_over_7_hrs: yes
wrote_gratitude_journal: yes
limited_screen_time_to_1_hr: no
practiced_good_posture: yes
connected_with_friends: yes
```

#### note_id 1078 — "Habit Tracking Log for 2023-05-05"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: yes
ate_homemade_meals: yes
practiced_meditation: yes
read_atleast_30_mins: yes
drank_adequate_water: yes
slept_over_7_hrs: no
wrote_gratitude_journal: yes
limited_screen_time_to_1_hr: yes
practiced_good_posture: yes
connected_with_friends: no
```

#### note_id 1079 — "Habit Tracking Log for 2023-05-04"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: no
ate_homemade_meals: yes
practiced_meditation: yes
read_atleast_30_mins: yes
drank_adequate_water: yes
slept_over_7_hrs: yes
wrote_gratitude_journal: no
limited_screen_time_to_1_hr: no
practiced_good_posture: yes
connected_with_friends: yes
```

#### note_id 1080 — "Habit Tracking Log for 2023-05-03"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: yes
ate_homemade_meals: yes
practiced_meditation: no
read_atleast_30_mins: yes
drank_adequate_water: yes
slept_over_7_hrs: yes
wrote_gratitude_journal: yes
limited_screen_time_to_1_hr: yes
practiced_good_posture: yes
connected_with_friends: no
```

#### note_id 1081 — "Habit Tracking Log for 2023-05-02"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: yes
ate_homemade_meals: no
practiced_meditation: no
read_atleast_30_mins: yes
drank_adequate_water: yes
slept_over_7_hrs: yes
wrote_gratitude_journal: no
limited_screen_time_to_1_hr: yes
practiced_good_posture: yes
connected_with_friends: yes
```

#### note_id 1082 — "Habit Tracking Log for 2023-05-01"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: yes
ate_homemade_meals: yes
practiced_meditation: yes
read_atleast_30_mins: no
drank_adequate_water: yes
slept_over_7_hrs: yes
wrote_gratitude_journal: no
limited_screen_time_to_1_hr: yes
practiced_good_posture: yes
connected_with_friends: yes
```

#### note_id 1083 — "Habit Tracking Log for 2023-04-30"
```
# Daily Habit Tracker (yes/no questions to answer daily)

exercised_atleast_30_mins: yes
ate_homemade_meals: yes
practiced_meditation: yes
read_atleast_30_mins: yes
drank_adequate_water: yes
slept_over_7_hrs: no
wrote_gratitude_journal: yes
limited_screen_time_to_1_hr: yes
practiced_good_posture: yes
connected_with_friends: no
```

#### note_id 1056 — "Book Reading Lists"
```
# Book Reading Lists

The Catcher in the Rye
 - authors: J.D. Salinger
 - genre: Coming-of-Age

You Are a Badass
 - authors: Jen Sincero
 - genre: Self-Help

The Alchemist
 - authors: Paulo Coelho
 - genre: Self-Help

Atomic Habits
 - authors: James Clear
 - genre: Self-Help

The Power of Now
 - authors: Eckhart Tolle
 - genre: Spirituality

The Guns of August
 - authors: Barbara W. Tuchman
 - genre: History

Thinking, Fast and Slow
 - authors: Daniel Kahneman
 - genre: Psychology

Gone Girl
 - authors: Gillian Flynn
 - genre: Mystery

The Girl on the Train
 - authors: Paula Hawkins
 - genre: Psychological Thriller

Educated
 - authors: Tara Westover
 - genre: Memoir

Grit: The Power of Passion and Perseverance
 - authors: Angela Duckworth
 - genre: Psychology

Harry Potter and the Sorcerer's Stone
 - authors: J.K. Rowling
 - genre: Young Adult

The Hunger Games
 - authors: Suzanne Collins
 - genre: Science Fiction

1984
 - authors: George Orwell
 - genre: Dystopian

Becoming
 - authors: Michelle Obama
 - genre: Memoir

The Lord of the Rings: The Fellowship of the Ring
 - authors: J.R.R. Tolkien
 - genre: Epic Fantasy

Fahrenheit 451
 - authors: Ray Bradbury
 - genre: Science Fiction
```

#### note_id 1057 — "Movie Recommendations"
```
# Movie Recommendations

Inception
 - director: Christopher Nolan
 - genre: Sci-Fi, Action

The Dark Knight Rises
 - director: Christopher Nolan
 - genre: Action, Crime

The Social Network
 - director: David Fincher
 - genre: Biography, Drama

Interstellar
 - director: Christopher Nolan
 - genre: Sci-Fi, Drama

Schindler's List
 - director: Steven Spielberg
 - genre: Biography, Drama, History

Pulp Fiction
 - director: Quentin Tarantino
 - genre: Crime, Drama

The Social Network
 - director: David Fincher
 - genre: Biography, Drama

Gladiator
 - director: Ridley Scott
 - genre: Action, Drama

The Big Lebowski
 - director: Joel and Ethan Coen
 - genre: Comedy, Crime

The Grand Budapest Hotel
 - director: Wes Anderson
 - genre: Adventure, Comedy, Crime

Eternal Sunshine of the Spotless Mind
 - director: Michel Gondry
 - genre: Drama, Romance, Sci-Fi

No Country for Old Men
 - director: Joel and Ethan Coen
 - genre: Crime, Drama, Thriller

Pan's Labyrinth
 - director: Guillermo del Toro
 - genre: Drama, Fantasy, War

Fight Club
 - director: David Fincher
 - genre: Drama

The Matrix
 - director: The Wachowskis
 - genre: Sci-Fi, Action

A Beautiful Mind
 - director: Ron Howard
 - genre: Biography, Drama

Whiplash
 - director: Damien Chazelle
 - genre: Drama, Music

Inglourious Basterds
 - director: Quentin Tarantino
 - genre: Adventure, Drama, War

Blade Runner 2049
 - director: Denis Villeneuve
 - genre: Drama, Sci-Fi, Thriller

The Lord of the Rings: The Fellowship of the Ring
 - director: Peter Jackson
 - genre: Adventure, Fantasy

The Shawshank Redemption
 - director: Frank Darabont
 - genre: Drama

The Pianist
 - director: Roman Polanski
 - genre: Biography, Drama, Music

Spirited Away
 - director: Hayao Miyazaki
 - genre: Animation, Adventure, Family

Amélie
 - director: Jean-Pierre Jeunet
 - genre: Comedy, Romance

Pulp Fiction
 - director: Quentin Tarantino
 - genre: Crime, Drama
```

#### note_id 1058 — "Grocery List"
```
# Grocery List

 - avocado (2.0 pieces)
 - bananas (6.0 pieces)
 - peanut butter (1.0 jar)
 - orange juice (1.0 carton)
 - cucumber (2.0 pieces)
 - olive oil (1.0 bottle)
 - carrots (1.0 bag)
 - chicken thighs (2.0 pounds)
 - lettuce (1.0 head)
 - spinach (1.0 bag)
 - bell peppers (3.0 pieces)
 - potatoes (3.0 pounds)
 - bread (2.0 loaves)
 - cheese (0.5 pound)
 - salmon fillets (2.0 pieces)
 - rice (1.0 pound)
 - onions (2.0 pieces)
 - yogurt (2.0 pints)
 - almonds (1.0 bag)
 - frozen peas (1.0 bag)
 - yogurt (4.0 cups)
 - fruit juice (1.0 bottle)
 - apples (3.0 pieces)
 - strawberries (1.0 pint)
```

#### note_id 1059 — "Gift Ideas for Various Occasions"
```
# Gift Ideas for Various Occasions

occasion: Graduation
ideas:
- Gift cards for online retailers
- Laptop or tablet accessories
- Online courses or workshops subscription
- Professional resume template
- Amazon Prime membership
- E-book reader
- Virtual reality headset
- Language learning app subscription
- Online fitness class membership
- Digital notetaking device

occasion: Wedding
ideas:
- Kitchen appliances or cookware
- Home decor items
- Personalized cutting board or wine glasses
- Contribution to their honeymoon fund
- Artwork or wall hangings
- Fine dining experience gift card
- Bedding or linens
- Outdoor furniture or accessories
- Wine or champagne set
- Streaming device

occasion: Father's Day
ideas:
- Tech gadgets or tools from online electronics stores
- Tickets to a virtual sports game or concert
- Whiskey or beer tasting kits from online sellers
- Outdoor adventure gear from online retailers
- Personalized wallet or keychain from online artisans
- Online grilling or cooking classes
- Virtual golf or fishing experience
- E-book of his favorite genre
- Digital subscription to a sports news website
- Online DIY project kits

occasion: Friendship Day
ideas:
- Customized friendship bracelet or necklace from online shops
- Virtual spa day experience
- Memory scrapbook or photo album created online
- Cooking a special meal together through a virtual class
- Online concert or movie streaming subscription
- Subscription to a fun activity or hobby box
- Virtual museum or art gallery tour
- Outdoor picnic essentials available online
- Digital games or puzzle subscriptions
- Online crafting or DIY workshop

occasion: Birthday
ideas:
- Gift card to their favorite online store
- Tech gadgets or accessories
- Books by their favorite author
- Cooking or baking equipment
- Subscription to a streaming service
- Customized jewelry
- Personalized phone case
- Wireless earbuds
- Fitness tracker
- Outdoor adventure gear

occasion: Valentine's Day
ideas:
- Gift cards to online gourmet food stores
- Romantic e-cards or digital love notes
- Jewelry or accessories from online boutiques
- Virtual cooking class for couples
- Online wine tasting experience
- Digital music subscription
- Virtual reality date experience
- Online personalized gifts
- Digital movie rental
- E-book of romantic poetry

occasion: Mother's Day
ideas:
- Spa or pampering gift basket from online retailers
- Online cooking or baking class
- Handmade or personalized jewelry from online artisans
- Books or a subscription to an e-book service
- Gift card for a favorite online store
- Plant or garden accessories from online nurseries
- Online art classes
- Subscription to online magazines or blogs
- Virtual escape room experience
- Online wellness retreat

occasion: Housewarming
ideas:
- Indoor plants or succulents from an online nursery
- Candles or essential oil diffusers
- Decorative throw pillows or blankets
- Personalized doormat
- Wine or cocktail set
- Home organization items
- Art prints or wall decor from online galleries
- Online interior design consultation
- Subscription to a meal kit delivery service
- Smart home devices

occasion: Anniversary
ideas:
- Romantic getaway weekend voucher
- Customized anniversary photo book
- Tickets to a virtual concert or show
- Cooking or mixology class for couples (online)
- Spa or wellness retreat gift certificate
- Personalized star map of their wedding date
- Engraved watches
- Online escape room experience
- Digital photo frame
- Subscription to a movie streaming service

occasion: Baby Shower
ideas:
- Gift cards to baby stores
- Baby books and educational toys
- Nursery decor items
- Online parenting course
- Stroller or car seat
- Online shopping for baby essentials
- Baby monitor or breastfeeding accessories
- Children's e-books subscription
- Virtual baby shower games
- Diaper subscription service
```

#### note_id 1060 — "Weekly Workout Plan"
```
# Weekly Workout Plan

day: monday
exercises:
- 'Morning meditation: 10 minutes of mindfulness'
- Full-body dynamic stretches - 5 minutes
- Kettlebell swings - 3 sets of 15 reps
- Renegade rows - 3 sets of 10 reps per arm
- Plyometric box jumps - 4 sets of 8 reps
- Cool-down - 5 minutes of deep breathing
duration_mins: 30

day: tuesday
exercises:
- Warm-up - 5 minutes of light jogging
- Rock climbing - 1 hour at a local indoor climbing gym
- TRX suspension training - 3 sets of 12 reps
- Handstand practice - 10 minutes against a wall
- Cool-down - 5 minutes of gentle stretches
duration_mins: 85

day: wednesday
exercises:
- Morning yoga session - 30 minutes of vinyasa flow
- Biking - 45 minutes of cycling in a hilly terrain
- Medicine ball slams - 4 sets of 20 seconds
- Bosu ball squats - 3 sets of 12 reps
- Cool-down - 10 minutes of deep stretches
duration_mins: 90

day: thursday
exercises:
- Warm-up - 5 minutes of jump rope
- CrossFit-style AMRAP (As Many Rounds As Possible) - 20 minutes
- Barbell deadlifts - 4 sets of 6 reps
- Hanging leg raises - 3 sets of 12 reps
- Cool-down - 5 minutes of foam rolling
duration_mins: 60

day: friday
exercises:
- Warm-up - 10 minutes of brisk walking
- Swimming drills - 30 minutes focusing on different strokes
- Resistance band pull-aparts - 3 sets of 15 reps
- Sprints - 10 sets of 100 meters
- Cool-down - 5 minutes of gentle stretches
duration_mins: 70

day: saturday
exercises:
- Morning Tai Chi session - 40 minutes of fluid movements
- Parkour practice - 30 minutes of jumping, climbing, and balancing
- Cool-down - 10 minutes of deep breathing and stretching
duration_mins: 80

day: sunday
exercises:
- Active recovery - 1-hour leisurely bike ride or stroll
- Yoga for relaxation - 30 minutes of gentle poses and meditation
duration_mins: 90
```

#### note_id 1061 — "Food Recipes"
```
# Food Recipes

name: Mediterranean Quinoa Salad
ingredients:
- 1 cup quinoa, cooked and cooled
- 1 cup cucumber, diced
- 1 cup cherry tomatoes, halved
- 1/2 cup red onion, finely chopped
- 1/2 cup Kalamata olives, pitted and sliced
- 1/2 cup feta cheese, crumbled
- 1/4 cup fresh parsley, chopped
- 1/4 cup fresh mint, chopped
- 3 tablespoons extra virgin olive oil
- 2 tablespoons lemon juice
- Salt and black pepper to taste
instructions:
- In a large bowl, combine cooked quinoa, cucumber, cherry tomatoes, red onion, olives, feta cheese, parsley, and mint.
- In a small bowl, whisk together olive oil, lemon juice, salt, and black pepper.
- Pour the dressing over the quinoa mixture and toss to combine.
- Chill in the refrigerator for about 30 minutes before serving.
favorite: false

name: Vegetable Stir-Fry with Tofu
ingredients:
- 200g firm tofu, cubed
- 2 cups mixed vegetables (bell peppers, broccoli, carrots, snap peas, etc.), sliced
- 3 tablespoons soy sauce
- 1 tablespoon hoisin sauce
- 1 tablespoon sesame oil
- 2 cloves garlic, minced
- 1 teaspoon ginger, minced
- 2 tablespoons vegetable oil
- Cooked rice, for serving
instructions:
- In a bowl, mix together soy sauce, hoisin sauce, and sesame oil. Marinate the tofu cubes in this mixture for about 15 minutes.
- Heat vegetable oil in a wok or skillet over high heat. Add minced garlic and ginger, and stir-fry for a minute.
- Add the mixed vegetables and stir-fry for a few minutes until they are tender yet crisp.
- Push the vegetables to the side of the wok and add the marinated tofu. Cook until the tofu is golden and heated through.
- Combine the tofu and vegetables, and stir in the remaining marinade.
- Serve the stir-fry over cooked rice.
favorite: true

name: Chocolate Raspberry Parfait
ingredients:
- 1 cup chocolate cookies, crushed
- 2 cups vanilla Greek yogurt
- 1 cup fresh raspberries
- 1/2 cup dark chocolate chips
- 2 tablespoons honey
- Fresh mint leaves, for garnish
instructions:
- In serving glasses or bowls, layer crushed chocolate cookies at the bottom.
- Spoon a layer of vanilla Greek yogurt on top of the cookies.
- Add a layer of fresh raspberries.
- Sprinkle dark chocolate chips over the raspberries.
- Repeat the layers until the glasses are filled, finishing with a layer of yogurt on top.
- Drizzle honey over the top layer and garnish with fresh mint leaves.
- Refrigerate for at least 30 minutes before serving.
favorite: false

name: Spinach and Mushroom Stuffed Chicken
ingredients:
- 4 boneless, skinless chicken breasts
- 1 cup baby spinach, chopped
- 1 cup mushrooms, finely chopped
- 1/2 cup mozzarella cheese, shredded
- 2 cloves garlic, minced
- 1 tablespoon olive oil
- 1 teaspoon dried oregano
- Salt and black pepper to taste
- Toothpicks
instructions:
- "Preheat the oven to 375°F (190°C)."
- "In a skillet, heat olive oil over medium heat. Add minced garlic and sauté until fragrant."
- Add chopped mushrooms and cook until they release their moisture and become tender.
- Stir in chopped spinach and cook until wilted. Season with dried oregano, salt, and black pepper.
- Remove the skillet from heat and mix in shredded mozzarella cheese.
- Make a pocket in each chicken breast by cutting a slit horizontally. Stuff the pockets with the spinach and mushroom mixture.
- Secure the openings with toothpicks.
- Place the stuffed chicken breasts in a baking dish. Bake for about 25-30 minutes, or until the chicken is cooked through.
- Remove the toothpicks before serving.
favorite: true

name: Homestyle Chicken Noodle Soup
ingredients:
- 2 boneless, skinless chicken breasts
- 8 cups chicken broth
- 2 carrots, sliced
- 2 celery stalks, sliced
- 1 onion, diced
- 2 cloves garlic, minced
- 1 teaspoon dried thyme
- 1 teaspoon dried rosemary
- 100g egg noodles
- Salt and black pepper to taste
- Fresh parsley, chopped, for garnish
instructions:
- In a large pot, bring the chicken broth to a simmer.
- Add the chicken breasts, carrots, celery, onion, minced garlic, dried thyme, and dried rosemary.
- Simmer for about 20-25 minutes, or until the chicken is cooked through and the vegetables are tender.
- Remove the chicken breasts from the pot and shred them using two forks. Return the shredded chicken to the pot.
- Add the egg noodles and cook until tender, following the package instructions.
- Season the soup with salt and black pepper to taste.
- Garnish with chopped fresh parsley before serving.
favorite: false
```

#### note_id 1062 — "Inspirational Quotes Collection"
```
# Inspirational Quotes Collection

 - Success is not about being the best, it's about being better than you were yesterday.
   by Unknown
 - You are never too old to set another goal or to dream a new dream.
   by C.S. Lewis
 - Embrace the uncertainty, and you'll find the adventure.
   by Unknown
 - Your attitude determines your direction.
   by Unknown
 - The future starts today, not tomorrow.
   by Unknown
 - Every adversity carries with it the seed of an equivalent advantage.
   by Napoleon Hill
 - Every day is a new opportunity to grow.
   by Unknown
```

#### note_id 1063 — "Funny Quotes Collection"
```
# Funny Quotes Collection

 - I'm on a diet, but it's not going well. It's a Wi-Fi diet, and I'm trying to lose some data.
   by Unknown
 - Behind every great man, there is a woman rolling her eyes.
   by Jim Carrey
 - I'm on the whiskey diet. I've lost three days already.
   by Tommy Cooper
 - I'm on the seafood diet. I see food, and I eat it.
   by Unknown
```

#### note_id 1064 — "Movie Quotes Collection"
```
# Movie Quotes Collection

 - There's no crying in baseball!
   from A League of Their Own (1992)
 - E.T. phone home.
   from E.T. the Extra-Terrestrial (1982)
 - I see dead people.
   from The Sixth Sense (1999)
 - I'm king of the world!
   from Titanic (1997)
 - My precious.
   from The Lord of the Rings: The Two Towers (2002)
 - I feel the need... the need for speed!
   from Top Gun (1986)
 - There's no place like home.
   from The Wizard of Oz (1939)
 - You talking to me?
   from Taxi Driver (1976)
 - Go ahead, make my day.
   from Sudden Impact (1983)
```

#### note_id 1065 — "My Bucket List ([x] = done, [ ] = not done))"
```
# My Bucket List ([x] = done, [ ] = not done))

[ ] Swimming with dolphins
[x] Cruising on the Nile River
[x] Participating in a cultural exchange program
[x] Taking a cooking class in a foreign country
[x] Taking a cruise around the world
[ ] Hiking the Inca Trail to Machu Picchu
[x] Taking a photography expedition
```

## 6. Export contract (for the downstream export Task)

1. Source: all 28 notes of simple_note account `anita.burch@gmail.com` — nothing may be omitted.
   Enumerate with `search_notes` pagination (page_limit up to 20) and fetch content with
   `show_note`.
2. Destination directory: `~/backups/simple_note/` (absolute: `/home/anita/backups/simple_note/`).
   It must be created first (e.g. `create_directory(directory_path='~/backups/simple_note/',
   recursive=True)`).
3. Filename rule: take the note's exact `title`, replace every whitespace character with `_`, and
   append `.md`. No other characters are transformed. Example edge case:
   `My Bucket List ([x] = done, [ ] = not done))`
   → `My_Bucket_List_([x]_=_done,_[_]_=_not_done)).md`.
4. File content: the note's exact `content` string as returned by `show_note`.
5. Use `create_file(file_path='~/backups/simple_note/<name>.md', content=<content>,
   overwrite=False)`.
6. Expected result: 28 files inside `~/backups/simple_note/`, one per note, each matching the
   convention above.

## 7. Limitations / open items

- This task is discovery only; it did not create the export files. The actual export
  (directory creation + 28 file creations) is the downstream Task B, and independent verified
  listing is Task C.
- Values recorded here are point-in-time observations from the live app APIs. If notes are later
  added/edited, the inventory and filenames would change.
- Note content was captured verbatim; non-ASCII characters (e.g. `°`, `é` in "Food Recipes" and
  "Amélie") and single-quote/double-quote characters are preserved exactly.
- No knowledge items were available/verified for this mission (`verified_knowledge` empty), so no
  knowledge IDs are cited.
