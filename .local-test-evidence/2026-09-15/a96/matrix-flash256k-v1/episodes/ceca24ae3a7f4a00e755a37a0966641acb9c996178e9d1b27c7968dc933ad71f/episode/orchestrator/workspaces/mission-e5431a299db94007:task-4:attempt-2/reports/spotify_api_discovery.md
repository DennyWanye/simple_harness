# Spotify API Discovery (Task-1)

Scope: discover the AppWorld Spotify public APIs and the user account mapping required to
(a) read the user's saved album library, (b) read each album's tracks, (c) read per-song play
counts/history, and (d) create/modify playlists.

All observations below were produced by live calls in the shared AppWorld shell
(`apis.<app>.<api>(...)`). No hidden answers or evaluator were accessed.

## 1. Account / user mapping

Supervisor profile (`apis.supervisor.show_profile()`):
- first_name: Debra
- last_name: Ritter
- email: de_ritt@gmail.com
- phone_number: 3375602296
- birthday: 1994-11-30

Supervisor app passwords (`apis.supervisor.show_account_passwords()`) included:
- account_name: spotify, password: `7s7!cA8`

Spotify login (`apis.spotify.login(username='de_ritt@gmail.com', password='7s7!cA8')`):
- success -> `{"access_token": "<jwt>", "token_type": "Bearer"}`
- The access_token JWT subject (`sub`) contains `spotify+de_ritt@gmail.com`, confirming the
  Spotify user identity for this session.

Spotify account confirmation (`apis.spotify.show_account(access_token=...)`):
- first_name: Debra
- last_name: Ritter
- email: de_ritt@gmail.com
- registered_at: 2022-07-06T10:52:39
- verified: true
- is_premium: false

Public profile (`apis.spotify.show_profile(email='de_ritt@gmail.com')`):
- first_name: Debra, last_name: Ritter, registered_at: 2022-07-06T10:52:39

## 2. Exact API names and signatures

### Reading saved album library
- API: `spotify.show_album_library`
- Method/path: GET `/library/albums`
- Required params: `access_token` (string)
- Optional: `page_index` (int, default 0, >=0), `page_limit` (int, default 5, 1..20)
- Returns per album: `album_id`, `title`, `genre`, `artists` (list of {id,name}), `rating`,
  `like_count`, `review_count`, `release_date`, `song_ids` (list of int), `added_at`

### Reading an album's tracks
- API: `spotify.show_album`
- Method/path: GET `/albums/{album_id}`
- Required params: `album_id` (int)
- Returns: album fields plus `songs` = list of {`id`, `title`, `artist_ids`} and `shareable_link`

### Reading per-song info incl. play count
- API: `spotify.show_song`
- Method/path: GET `/songs/{song_id}`
- Required params: `song_id` (int)
- Returns: `song_id`, `title`, `album_id`, `album_title`, `duration`, `artists`, `release_date`,
  `genre`, `play_count` (float/int), `rating`, `like_count`, `review_count`, `shareable_link`
- This `play_count` field is the key signal for "most-played" per album.

### Per-song private info
- API: `spotify.show_song_privates` — GET `/songs/{song_id}/privates`
- Required: `song_id`, `access_token`
- Returns: `liked`, `reviewed`, `in_song_library`, `downloaded`
- Example (song 33): `{"liked": false, "reviewed": false, "in_song_library": false, "downloaded": true}`

### Reading playlist library (to check existing playlist name)
- API: `spotify.show_playlist_library` — GET `/library/playlists`
- Required: `access_token`
- Optional: `is_public` (bool), `page_index` (int, default 0), `page_limit` (int, default 5, 1..20)
- Returns per playlist: `playlist_id`, `title`, `is_public`, `rating`, `like_count`,
  `review_count`, `owner` {name,email}, `created_at`, `song_ids`

### Creating a playlist
- API: `spotify.create_playlist` — POST `/playlists`
- Required: `title` (string, length>=1), `access_token` (string)
- Optional: `is_public` (bool, default false)
- Returns: `message`, `playlist_id`

### Adding a song to a playlist
- API: `spotify.add_song_to_playlist` — POST `/playlists/{playlist_id}/songs/{song_id}`
- Required: `playlist_id` (int), `song_id` (int), `access_token` (string)
- Returns: `message`

### Reading a playlist's contents (for later verification)
- API: `spotify.show_playlist` — GET `/playlists/{playlist_id}`
- Required: `playlist_id` (int), `access_token` (string)
- Returns: `playlist_id`, `title`, `is_public`, `owner`, `created_at`, `shareable_link`,
  `songs` = list of {`id`, `title`, `artist_ids`}

## 3. Successful read evidence

### Album library (8 saved albums)
`show_album_library(access_token=..., page_index=0, page_limit=20)` returned 8 albums
(page_index=1 returned `[]`, so the library is exactly these 8):

| album_id | title | song_ids | added_at |
|---|---|---|---|
| 7  | Vibrant Visions | 33,34,35,36 | 2022-08-14T01:47:52 |
| 9  | Mystical Crescendo | 44,45,46 | 2022-11-13T01:13:07 |
| 10 | Dreamscape Delights | 47,48,49,50,51,52,53 | 2023-03-26T01:43:50 |
| 11 | Synaptic Serenity | 54,55,56,57 | 2023-01-28T03:45:46 |
| 13 | Starlight Serenades | 63,64,65,66 | 2023-04-20T19:05:42 |
| 15 | Whispers in the Wind | 70,71,72,73 | 2023-05-16T00:39:25 |
| 16 | Electric Dreamscape | 74,75,76 | 2023-01-17T09:02:24 |
| 18 | Echoes of Eternity | 80,81,82 | 2023-04-17T06:12:04 |

### Per-song play counts (from `show_song`, key for most-played selection)
| album_id | song_id | title | play_count |
|---|---|---|---|
| 7  | 33 | Unveiled | 809 |
| 7  | 34 | Under the Gaze of a Watchful Moon | 357 |
| 7  | 35 | Dancing in the Rain of Tears | 135 |
| 7  | 36 | A Whisper in the Midnight Air | 974 |
| 9  | 44 | The Illusion of Eternal Spring | 671 |
| 9  | 45 | Eclipsed | 713 |
| 9  | 46 | Symphony of the Twilight Forest | 134 |
| 10 | 47 | Time's Hold | 279 |
| 10 | 48 | Cherry Blossom Tears | 314 |
| 10 | 49 | Whispers of the Enchanted Forest | 841 |
| 10 | 50 | Lonely Skies | 316 |
| 10 | 51 | The Silence Between Us | 991 |
| 10 | 52 | Bleeding Sun | 648 |
| 10 | 53 | Unearthed Secrets | 648 |
| 11 | 54 | Heartstrings Symphony | 845 |
| 11 | 55 | Tangled Lies | 437 |
| 11 | 56 | Distant Love | 334 |
| 11 | 57 | Silver Lining | 421 |
| 13 | 63 | Journey Through the Unknown | 582 |
| 13 | 64 | Caught in a Web of Lies | 468 |
| 13 | 65 | Eternal Solitude | 818 |
| 13 | 66 | Phantom Pain | 975 |
| 15 | 70 | Serenade of the Forgotten Stars | 201 |
| 15 | 71 | Eternal Fade | 313 |
| 15 | 72 | Wandering Through Time's Embrace | 151 |
| 15 | 73 | When Silence Becomes Deafening | 482 |
| 16 | 74 | On the Border of Reality | 846 |
| 16 | 75 | Walking Through the Valley of Shadows | 806 |
| 16 | 76 | Whispers of the Heart | 330 |
| 18 | 80 | Wandering the Streets Alone | 863 |
| 18 | 81 | Echoes of the Whispering Wind | 645 |
| 18 | 82 | Lost in the Twilight of Hope | 750 |

### Playlist library (existing playlists owned by Debra Ritter)
`show_playlist_library(access_token=..., page_index=0, page_limit=20)` returned 5 playlists;
page_index=1 returned `[]`:
- 593 "October Feels: Autumn Aesthetics" (private)
- 594 "Heartbreak Hotel: Songs of Sorrow" (public)
- 595 "Velvet Voices: Best of R&B" (public)
- 596 "Countryside Chronicles: Folk Favorites" (private)
- 597 "Art & Soul: Masterful Melodies" (public)

No playlist named "My Most Played Album Songs" currently exists.

## 4. Notes, gotchas and limitations

- All transactional Spotify endpoints require an `access_token`; obtain it via
  `spotify.login(username=<email>, password=<password>)` and reuse the returned token.
- `show_album_library` and `show_playlist_library` paginate; default `page_limit` is 5 and max is 20.
  A page index past the end returns an empty list `[]` (used to establish the full set of 8 albums / 5 playlists).
- `show_album_library` does NOT include play counts; play counts come only from `show_song.play_count`.
- "Most-played" is unambiguously the max `play_count` within each album's `song_ids`. Empty songs
  exist for none of the 8 albums.
- `show_album`/`show_song` do not require an access token (public reads); the library and
  playlist mutation endpoints do.
- Observed album 10 has a 648/648 tie for non-max songs (52,53); the maximum (song 51 = 991) is unambiguous.
- Limitation: this task only performed discovery/read calls. No playlist was created and no songs
  were added (that is Task-3's scope); no mutation was attempted here.
