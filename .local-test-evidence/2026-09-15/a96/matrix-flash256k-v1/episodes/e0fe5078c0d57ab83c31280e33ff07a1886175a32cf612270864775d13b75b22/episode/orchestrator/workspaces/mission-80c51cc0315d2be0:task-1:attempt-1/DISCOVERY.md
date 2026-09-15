# DISCOVERY.md

Task: mission-80c51cc0315d2be0:task-1 — Discover AppWorld accounts and the relevant Spotify/music APIs needed to identify the user's Spotify player queue and artist/song/play-count data.

All observations below come from live `apis.*` calls executed in the shared AppWorld shell during this task. Nothing here is copied from hidden answers or the evaluator.

## 1. Supervisor (user) identity and accounts

`apis.supervisor.show_profile()`:
- first_name: Susan
- last_name: Burton
- email: susanmiller@gmail.com
- phone_number: 3296062648
- birthday: 1994-04-30
- sex: female

`apis.supervisor.show_account_passwords()` returned account_name/password pairs:
- amazon: Gt$!_*W
- file_system: 8nNw!jZ
- gmail: qu4Y7}s
- phone: C4n&I40
- simple_note: e+QwbmV
- splitwise: mSqG}QU
- spotify: %CCvl8v
- todoist: jHZ#RPM
- venmo: Wq8!RAU

Relevant account for this mission: **Spotify** — username/email `susanmiller@gmail.com`, password `%CCvl8v`.

## 2. Spotify login and observed account state

- API: `apis.spotify.login(username, password)` → path `/auth/token` (POST). Parameters: `username` (string, required, "Your account email"), `password` (string, required). Returns `access_token`, `token_type`.
- Executed: `apis.spotify.login(username='susanmiller@gmail.com', password='%CCvl8v')` → succeeded; returned access_token (JWT) and token_type "Bearer". Most Spotify APIs require this `access_token`.
- API: `apis.spotify.show_account(access_token)` → path `/account` (GET). Observed:
  - first_name: Susan, last_name: Burton, email: susanmiller@gmail.com
  - registered_at: 2023-01-20T10:07:19, last_logged_in: 2023-01-20T10:07:19
  - verified: True
  - is_premium: False

## 3. Relevant Spotify APIs (names + parameter requirements)

Discovery/read:
- `search_artists(query, genre, min_follower_count, max_follower_count, page_index, page_limit, sort_by)` — GET `/artists`. `query` string (optional). Returns artist_id, name, genre, follower_count, created_at.
- `show_artist(artist_id)` — GET `/artists/{artist_id}` (no access_token needed). Returns artist details.
- `show_artist_following(artist_id, access_token)` — GET `/artists/{artist_id}/following`.
- `search_songs(query, artist_id, album_id, genre, min_release_date, max_release_date, min_duration, max_duration, min_rating, max_rating, min_like_count, max_like_count, min_play_count, max_play_count, page_index, page_limit, sort_by)` — GET `/songs`. Key filters for this mission: `artist_id` (int) and `min_play_count` (int). `sort_by` supports rating, like_count, play_count (prefix +/-). Each item returns song_id, title, album_id, album_title, duration, artists, release_date, genre, **play_count**, rating, like_count, review_count, shareable_link.
- `show_song(song_id)` — GET `/songs/{song_id}` (no access_token). Same fields incl. play_count.
- `show_song_privates(song_id, access_token)` — GET `/songs/{song_id}/privates`. Returns liked, reviewed, in_song_library, downloaded.
- `show_song_queue(access_token)` — GET `/music_player/song_queue`. Returns list of queue entries (song_id, title, album_id, album_title, duration, artists, position, is_playing, is_current).
- `show_current_song(access_token)` — GET `/music_player/current_song`. Returns current song incl. played_seconds, is_playing.
- `show_volume(access_token)` — GET volume level.

Queue mutation (for downstream tasks):
- `add_to_queue(access_token, song_id, album_id, playlist_id)` — POST `/music_player/song_queue`. Add a song/album/playlist to the queue.
- `play_music(access_token, song_id, album_id, playlist_id, queue_position)` — POST `/music_player/play`. At most one of song_id/album_id/playlist_id/queue_position.
- `clear_song_queue(access_token)`, `remove_song_from_queue(...)`, `move_song_in_queue(...)`, `shuffle_song_queue(...)`, `pause_music`, `next_song`, `previous_song`, `seek_song`, `loop_song`.

Auxiliary Spotify APIs that exist but are not required for this goal: songs/albums/playlists library, likes, reviews, downloads, recommendations, premium plans/subscriptions, payment cards.

## 4. Observed Spotify player queue state (as of this task)

`apis.spotify.show_song_queue(access_token)` returned 9 entries (position 0–8), none by Lily Moon:
| position | song_id | title | artist | is_playing | is_current |
|---|---|---|---|---|---|
| 0 | 102 | Autumn's Lament | Marigold Muse (id 4) | False | False |
| 1 | 108 | Cold Embrace | Ava Morgan (id 5) | False | False |
| 2 | 214 | The Sweet Pain of Reminiscence | Oceanic Odyssey (id 21) | False | False |
| 3 | 202 | Summer's End | Ethan Wallace (id 19) | False | False |
| 4 | 114 | When All Hope Seems Lost | Seraphina Dawn (id 6) | True | True |
| 5 | 10 | The Curse of Loving You | Lucas Grey (id 32) | False | False |
| 6 | 287 | Painted Skies | Hazel Winter (id 31) | False | False |
| 7 | 285 | Wading Through the Ashes of Love | Marcus Lane (id 30) | False | False |
| 8 | 38 | Destiny's Game | Aria Sterling (id 8) | False | False |

`apis.spotify.show_current_song(access_token)` → song_id 114 "When All Hope Seems Lost" (Seraphina Dawn), played_seconds 0, is_playing True.
`apis.spotify.show_volume(access_token)` → volume 5.

## 5. Artist / song / play-count data for Lily Moon

`apis.spotify.search_artists(query='Lily Moon')` → exact artist: **artist_id 34, name "Lily Moon", genre "rock", follower_count 25, created_at 2018-12-04T01:37:41** (top result; other results were unrelated).

`apis.spotify.show_artist_following(artist_id=34, access_token)` → following: True.

All 11 songs by artist_id 34, sorted by play_count (via `search_songs(artist_id=34, page_limit=20, sort_by='-play_count')`, paged):
| song_id | title | play_count |
|---|---|---|
| 311 | Infinite Dreams | 990 |
| 67 | The Echoes of a Silent Heart | 916 |
| 313 | Mystical Dreamscape | 864 |
| 74 | On the Border of Reality | 846 |
| 75 | Walking Through the Valley of Shadows | 806 |
| 310 | Harmony of the Distant Stars | 715 |
| 69 | Whispers of a Forgotten Love | 645 |
| 312 | Eternal Tears | 562 |
| 309 | Final Act | 520 |
| 76 | Whispers of the Heart | 330 |
| 68 | Lost in the Wilderness of Love | 156 |

Filter check: `search_songs(artist_id=34, min_play_count=980, page_limit=20)` returned exactly one song: **song_id 311, "Infinite Dreams", play_count 990**. This is the only Lily Moon song with play_count > 980 (the other ten are ≤ 916).

## 6. Key identifiers for downstream tasks

- Supervisor user: Susan Burton, susanmiller@gmail.com.
- Spotify login: susanmiller@gmail.com / %CCvl8v (access_token obtained at login; required by queue/account APIs).
- Lily Moon artist_id: 34.
- Candidate song(s) with play_count > 980: song_id 311 ("Infinite Dreams", 990).
- Current queue: 9 songs (see table); none by Lily Moon yet.
- Current song: 114; volume: 5.

## Limitations / notes

- Access tokens were observed to carry an expiry (`exp` claim in the JWT); if a later task's call fails on auth, re-login with the Spotify credentials above.
- Play-count figures are point-in-time values returned by the search/show APIs during this task.
- This file records discovery only; no queue mutation was performed in this task.
