# DISCOVERY.md — AppWorld account & Spotify API discovery (Task 1)

Scope: Discover the user's AppWorld accounts and the Spotify/music APIs needed to
identify the Spotify player queue and artist/song/play-count data. This file records
only observations produced by real API calls in the shared AppWorld shell.

## 1. User / supervisor accounts

From `apis.supervisor.show_profile()`:

- first_name: Susan
- last_name: Burton
- email: susanmiller@gmail.com
- phone_number: 3296062648
- birthday: 1994-04-30
- sex: female

From `apis.supervisor.show_account_passwords()` (AppWorld app account passwords):

- amazon: Gt$!_*W
- file_system: 8nNw!jZ
- gmail: qu4Y7}s
- phone: C4n&I40
- simple_note: e+QwbmV
- splitwise: mSqG}QU
- spotify: %CCvl8v
- todoist: jHZ#RPM
- venmo: Wq8!RAU

Current assigned task, from `apis.supervisor.show_active_task()`:

- instruction: "Add all the songs from Lily Moon that have been played over 980 times to my Spotify player queue."
- status: null
- answer: `<<NOT_GIVEN>>`

## 2. Spotify login / session

- API: `apis.spotify.login(username, password)`
  - `username` (string, required) = account email
  - `password` (string, required)
  - returns: `{access_token, token_type}` (token_type "Bearer")
- Working credentials:
  - username = `susanmiller@gmail.com`
  - password = `%CCvl8v`
- Result: login succeeded and returned an `access_token`. The token is required as the
  `access_token` parameter by all authenticated Spotify endpoints (account, queue, privates).

From `apis.spotify.show_account(access_token=...)`:

- first_name: Susan
- last_name: Burton
- email: susanmiller@gmail.com
- registered_at: 2023-01-20T10:07:19
- last_logged_in: 2023-01-20T10:07:19
- verified: true
- is_premium: false

## 3. Spotify player queue — observed state

API: `apis.spotify.show_song_queue(access_token)` -> list of queue entries, each with
`song_id, title, album_id, duration, artists[{id,name}], position, is_playing, is_current`.

Observed queue (length 9, positions 0..8) at discovery time:

| position | song_id | title | album_id | artist |
|---|---|---|---|---|
| 0 | 102 | Autumn's Lament | null | Marigold Muse (id 4) |
| 1 | 108 | Cold Embrace | null | Ava Morgan (id 5) |
| 2 | 214 | The Sweet Pain of Reminiscence | null | Oceanic Odyssey (id 21) |
| 3 | 202 | Summer's End | null | Ethan Wallace (id 19) |
| 4 | 114 | When All Hope Seems Lost (is_playing=true, is_current=true) | null | Seraphina Dawn (id 6) |
| 5 | 10 | The Curse of Loving You | 2 | Lucas Grey (id 32) |
| 6 | 287 | Painted Skies | null | Hazel Winter (id 31) |
| 7 | 285 | Wading Through the Ashes of Love | null | Marcus Lane (id 30) |
| 8 | 38 | Destiny's Game | 8 | Aria Sterling (id 8) |

No Lily Moon song was present in the queue at discovery time.

## 4. Artist lookup

API: `apis.spotify.search_artists(query)` -> list of `{artist_id, name, genre, follower_count, created_at}`.

`apis.spotify.search_artists(query='Lily Moon')` returned (top results):

- artist_id 34, name "Lily Moon", genre "rock", follower_count 25, created_at 2018-12-04T01:37:41
- artist_id 1, name "Olivia Roberts", genre "R&B", follower_count 20, ...
- artist_id 2, name "Phoenix Rivers", genre "R&B", follower_count 18, ...
- artist_id 3, name "Jasper Skye", genre "EDM", follower_count 23, ...
- artist_id 4, name "Marigold Muse", genre "R&B", follower_count 17, ...

=> The target artist is **Lily Moon, artist_id = 34**.

`apis.spotify.show_artist(artist_id)` also exists and takes `artist_id` (integer, required),
returning `{artist_id, name, genre, follower_count, created_at}`.

## 5. Song lookup and play-count data

API: `apis.spotify.search_songs(...)` — key parameters:

- `query` (string, optional, default "")
- `artist_id` (integer, optional) — filter by artist
- `album_id` (integer, optional)
- `genre` (string, optional)
- `min_release_date` / `max_release_date` (YYYY-MM-DD, optional)
- `min_duration` / `max_duration` (integer seconds, optional)
- `min_rating` / `max_rating` (number, optional)
- `min_like_count` / `max_like_count` (integer, optional)
- `min_play_count` / `max_play_count` (integer, optional) — play-count filter
- `page_index` (integer, default 0)
- `page_limit` (integer, default 5, range 1..20)
- `sort_by` (string, optional; valid attributes: `rating`, `like_count`, `play_count`;
  prefix `+`/`-` for ascending/descending)

Each result item includes: `song_id, title, album_id, album_title, duration, artists[{id,name}],
release_date, genre, play_count, rating, like_count, review_count, shareable_link`.

API: `apis.spotify.show_song(song_id)` — takes `song_id` (integer, required). Returns the
same fields including `play_count`. Example `show_song(song_id=311)` returned
title "Infinite Dreams", artist Lily Moon (id 34), genre rock, play_count 990.

### All Lily Moon songs observed

`apis.spotify.search_songs(artist_id=34, page_limit=20)` returned 11 songs (the full set;
11 < page_limit, so no further pages). Play counts observed:

| song_id | title | play_count |
|---|---|---|
| 67 | The Echoes of a Silent Heart | 916 |
| 68 | Lost in the Wilderness of Love | 156 |
| 69 | Whispers of a Forgotten Love | 645 |
| 74 | On the Border of Reality | 846 |
| 75 | Walking Through the Valley of Shadows | 806 |
| 76 | Whispers of the Heart | 330 |
| 309 | Final Act | 520 |
| 310 | Harmony of the Distant Stars | 715 |
| 311 | Infinite Dreams | 990 |
| 312 | Eternal Tears | 562 |
| 313 | Mystical Dreamscape | 864 |

Threshold given by the goal is "played over 980 times" (strictly > 980). Among the observed
Lily Moon songs, only **song_id 311 "Infinite Dreams" (play_count 990)** exceeds 980.
(This selection is the deliverable of Task 2 / SELECTION.md; recorded here only as observed data.)

## 6. Queue mutation API (for downstream tasks)

API: `apis.spotify.add_to_queue(access_token, song_id?, album_id?, playlist_id?)`
- `access_token` (string, required)
- At most one of `song_id`, `album_id`, `playlist_id` (integer each, optional) is used.
- Returns `{message}` on success.

Related queue APIs observed in docs: `show_song_queue`, `clear_song_queue`,
`remove_song_from_queue`, `play_music`, `show_current_song`.

## 7. Summary of exact identifiers & parameter requirements

- Supervisor email: susanmiller@gmail.com
- Spotify login: username `susanmiller@gmail.com`, password `%CCvl8v` -> access_token (required for authenticated calls)
- Target artist: Lily Moon, artist_id 34
- Queue inspection: `apis.spotify.show_song_queue(access_token)` — observed 9 entries
- Song search: `apis.spotify.search_songs(artist_id=34, page_limit=20)` — observed 11 Lily Moon songs
- Play count lives on `show_song`/`search_songs` as the `play_count` field
- Queue append: `apis.spotify.add_to_queue(access_token, song_id=...)`

## 8. Notes / limitations

- Only the artist_id=34 filter query was used; results returned 11 items (< page_limit 20),
  so the Lily Moon song set is considered complete for this data.
- `search_songs` with the default page_limit (5) truncated results; a larger `page_limit`
  was required to see all songs.
- No queue mutation was performed in this task.
