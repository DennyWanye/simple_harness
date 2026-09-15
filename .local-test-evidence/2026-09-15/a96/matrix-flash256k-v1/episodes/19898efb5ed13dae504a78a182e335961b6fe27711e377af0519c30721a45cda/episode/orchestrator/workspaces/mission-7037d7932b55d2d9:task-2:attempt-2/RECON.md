# RECON — AppWorld API, Account & Spotify Library Reconnaissance

Task: mission-7037d7932b55d2d9:task-1
Goal: Discover and document AppWorld public APIs, supervisor account information, and
Spotify library access for the user (concrete API names, required parameters, account
handles, and library data locations).

All observations below were obtained by actually executing `appworld_execute` in the
shared AppWorld shell. `apis` is preloaded in the shell namespace; `import apis` is
**not allowed** (raises "Usage of the following module is not allowed: apis").

## 1. API discovery mechanism
- `apis.api_docs.show_app_descriptions()` → list of all apps {name, description}.
- `apis.api_docs.show_api_descriptions(app_name='<app>')` → list of APIs {name, description}.
- `apis.api_docs.show_api_doc(app_name='<app>', api_name='<api>')` → full doc: path, method,
  parameter list (name/type/required/default/constraints) and response schemas.

Apps present in this AppWorld: `api_docs`, `supervisor`, `amazon`, `phone`, `file_system`,
`spotify`, `venmo`, `gmail`, `splitwise`, `simple_note`, `todoist`.

## 2. Supervisor account information
- `apis.supervisor.show_profile()` →
  `{'first_name': 'Glenn', 'last_name': 'Burton', 'email': 'glenn.burton@gmail.com',
   'phone_number': '8638518861', 'birthday': '1993-08-13', 'sex': 'male'}`
- `apis.supervisor.show_account_passwords()` → app account credentials (name, password):
  - amazon: `kz0d(by`
  - file_system: `ym=Rysn`
  - gmail: `H9c5f9I`
  - phone: `F#_2d^6`
  - simple_note: `}@}a=$h`
  - splitwise: `FMLx3Hu`
  - **spotify: `EbhXe%D`**
  - todoist: `JBfGOar`
  - venmo: `zp$44pd`
- `apis.supervisor.show_addresses()` → Home: 816 Brittney Overpass Suite 48, Seattle,
  Washington 44756, United States; Work: 8875 Amy Extensions Suite 797, Seattle, Washington
  49596, United States.
- `apis.supervisor.show_active_task()` → instruction: "Give me a comma-separated list of top
  4 most played r&b song titles from across my Spotify song, album and playlist libraries."
- Account handle used for Spotify login: **`glenn.burton@gmail.com`** (password `EbhXe%D`).

## 3. Spotify library access
### 3.1 Authentication
- `apis.spotify.login(username='glenn.burton@gmail.com', password='EbhXe%D')` → returns
  `{'access_token': <jwt>, 'token_type': 'Bearer'}`. Every library/detail call below requires
  `access_token`.

Required parameter note: `login` needs `username` (account email) and `password`.
All `show_*` library APIs require `access_token`.

### 3.2 Concrete data locations (concrete API names + required params)
- **Song library:** `apis.spotify.show_song_library(access_token, page_index=0, page_limit=5)`
  - path `GET /library/songs`; paginated. Returns per song: `song_id, title, album_id,
    album_title, duration, artists[{id,name}], added_at`. **No `genre`/`play_count` in this
    listing.**
- **Album library:** `apis.spotify.show_album_library(access_token, page_index=0, page_limit=5)`
  - path `GET /library/albums`; paginated. Returns `album_id, title, genre, artists,
    rating, like_count, review_count, release_date, song_ids, added_at`.
- **Playlist library:** `apis.spotify.show_playlist_library(access_token, is_public=None,
  page_index=0, page_limit=5)` - path `GET /library/playlists`; paginated. Returns
  `playlist_id, title, is_public, rating, like_count, review_count, owner{name,email},
  created_at, song_ids`.
- **Song detail (genre + play_count):** `apis.spotify.show_song(song_id)` (no token needed) →
  `song_id, title, album_id, album_title, duration, artists, release_date, genre,
  play_count, rating, like_count, review_count, shareable_link`. **This is the canonical
  location for `genre` and `play_count` used for the "most played R&B" ranking.**
- **Genres:** `apis.spotify.show_genres()` (no params) → `['EDM', 'R&B', 'indie', 'hip-hop',
  'jazz', 'rock', 'pop', 'classical', 'reggae', 'country']`. Confirms `'R&B'` is the exact
  genre string.
- Alternative/filtered lookup: `apis.spotify.search_songs(query, genre, min_play_count,
  max_play_count, sort_by, page_index, page_limit, ...)`; `sort_by` accepts
  `+/-rating`, `+/-like_count`, `+/-play_count`. Could be used to rank R&B songs by play_count.
- Supporting detail APIs: `apis.spotify.show_album(album_id)` (`songs[]` list),
  `apis.spotify.show_playlist(playlist_id, access_token)` (`songs[]` list),
  `apis.spotify.show_song_privates(song_id, access_token)` (`liked, reviewed,
  in_song_library, downloaded`).

Pagination note: default `page_limit` is 5 and max is 20. Loop `page_index` from 0 until a
short/empty page to enumerate a library fully.

### 3.3 Observed current library contents (data actually present now)
Logged in as `glenn.burton@gmail.com` (is_premium: False, verified: True).

**Song library — 19 songs** (song_id, title, genre, play_count):
- 33 'Unveiled' | hip-hop | 809
- 44 'The Illusion of Eternal Spring' | jazz | 671
- 56 'Distant Love' | EDM | 334
- 92 'Crimson Veil' | R&B | 972
- 109 'The Weight of a Thousand Stars' | EDM | 973
- 114 'When All Hope Seems Lost' | EDM | 868
- 135 'In the Silence of Your Absence' | rock | 346
- 137 'A Symphony of Fading Hopes' | rock | 915
- 156 'Whispers in the Night' | reggae | 488
- 159 'The Forgotten Pages of Time' | reggae | 844
- 202 "Summer's End" | indie | 356
- 225 'Shadows in the Twilight' | reggae | 854
- 226 'Midnight Whispers' | reggae | 721
- 241 'Wilted Roses on the Vine' | R&B | 713
- 265 'Secrets of the Heart' | classical | 773
- 302 'An Ode to Forgotten Dreams' | R&B | 619
- 303 'Searching for a Lost Horizon' | R&B | 443
- 308 'The Melancholy of Wasted Years' | classical | 907
- 324 'Sacred Ground' | indie | 743

**Album library — 8 albums** (album_id, title, genre, song_ids):
- 2 'Celestial Harmonies' | R&B | [8, 9, 10]
- 3 'Nocturnal Melodies' | R&B | [11, 12, 13, 14, 15]
- 8 'Velvet Underground' | jazz | [37, 38, 39, 40, 41, 42, 43]
- 10 'Dreamscape Delights' | jazz | [47, 48, 49, 50, 51, 52, 53]
- 11 'Synaptic Serenity' | EDM | [54, 55, 56, 57]
- 12 'Astral Journey' | rock | [58, 59, 60, 61, 62]
- 13 'Starlight Serenades' | EDM | [63, 64, 65, 66]
- 14 'Midnight Serenade' | rock | [67, 68, 69]

**Playlist library — 4 playlists** (playlist_id, title, song_ids):
- 621 'Heartbreak Hotel: Songs of Sorrow' | [76, 99, 111, 273, 323]
- 622 'Classical Cornerstones' | [14, 64, 80, 105, 145, 154, 175, 185, 228, 284]
- 623 'Groove Galaxy: Funk & Soul' | [39, 52, 70, 120, 135, 232]
- 624 "Retro Rewind: 80's & 90's Mix" | [40, 87, 136, 165, 186, 233, 290, 292]

## 4. Notes for the downstream computation (Task 2)
- "Across my Spotify song, album and playlist libraries" = union of: song-library song_ids,
  every `song_ids` entry of album-library albums, and every `song_ids` entry of playlist-library
  playlists.
- For each candidate song, fetch `show_song(song_id)` to get `genre` and `play_count`.
- Keep songs whose `genre == 'R&B'`, rank by `play_count` descending, take top 4 titles.
- Song library titles only show R&B genre after a `show_song` lookup; the library listing
  itself omits genre. Album genre 'R&B' does not by itself guarantee each contained song is
  genre 'R&B' — per-song `show_song` is authoritative.

## 5. Limitations / caveats
- This task only documents reconnaissance; it does NOT assert the final top-4 answer.
- Library listings are live state and may change; the counts/IDs above are as observed during
  this task's execution.
- `page_limit` max is 20, so full enumeration requires explicit pagination.
