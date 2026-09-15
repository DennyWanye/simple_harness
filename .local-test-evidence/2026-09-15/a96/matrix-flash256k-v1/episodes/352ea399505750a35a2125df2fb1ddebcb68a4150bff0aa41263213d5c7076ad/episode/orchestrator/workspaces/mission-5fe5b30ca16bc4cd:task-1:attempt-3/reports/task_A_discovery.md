# Task A — Discovery of the live AppWorld Spotify environment

Mission root goal: *"How many unique songs are there across my Spotify song library, albums library and all playlists?"*

This report is **discovery only**. It records the verified API contract, the simulated user's
account context, observed counts / sample ids, and verbatim errors. It does **not** assert the
final mission answer (that belongs to later tasks). All calls performed here were read-only
except the required `spotify.login`, which only returns an access token.

---

## 1. Environment discovery

`apis.api_docs.show_app_descriptions()` returned the apps:
`api_docs, supervisor, amazon, phone, file_system, spotify, venmo, gmail, splitwise, simple_note, todoist`.

The Spotify app is the one of interest:
> "spotify": "A music streaming app to stream songs and manage song, album and playlist libraries."

## 2. Supervisor / account context

- `apis.supervisor.show_profile()` →
  `{"first_name": "Debra", "last_name": "Ritter", "email": "de_ritt@gmail.com",
    "phone_number": "3375602296", "birthday": "1994-11-30", "sex": "female"}`
- `apis.supervisor.show_active_task()` →
  `{"instruction": "How many unique songs are there across my Spotify song library, albums library and all playlists?",
    "status": null, "answer": "<<NOT_GIVEN>>"}`
- `apis.supervisor.show_account_passwords()` → Spotify credential:
  `{"account_name": "spotify", "password": "7s7!cA8"}`

**Login** (`apis.spotify.login`, `POST /auth/token`, params `username`=email, `password`):
succeeded for `username="de_ritt@gmail.com"`, `password="7s7!cA8"`, returning
`{"access_token": "<JWT>", "token_type": "Bearer"}`.
All library list endpoints below require this `access_token`.

## 3. Relevant Spotify APIs and exact contracts

`apis.api_docs.show_api_descriptions(app_name='spotify')` listed 90 APIs. The relevant ones:

### 3.1 `show_song_library` — the user's song library
- path `/library/songs`, method **GET**
- params: `access_token` (string, **required**); `page_index` (int, optional, default **0**, `>=0`);
  `page_limit` (int, optional, default **5**, `>=1, <=20`)
- response (success): list of `{song_id, title, album_id, album_title, duration, artists[{id,name}], added_at}`
- pagination: page-based; returns up to `page_limit` items per page.

### 3.2 `show_album_library` — the user's albums library
- path `/library/albums`, method **GET**
- params: `access_token` (**required**); `page_index` (optional, default **0**, `>=0`);
  `page_limit` (optional, default **5**, `>=1, <=20`)
- response (success): list of `{album_id, title, genre, artists[{id,name}], rating, like_count,
  review_count, release_date, song_ids[...], added_at}`
- **The album tracks are already included as `song_ids` in this list response.**

### 3.3 `show_album` — an album's tracks (detail)
- path `/albums/{album_id}`, method **GET**
- params: `album_id` (int, **required**). **No `access_token` is required.**
- response (success): `{album_id, title, genre, artists, rating, like_count, review_count,
  release_date, shareable_link, songs[{id, title, artist_ids}]}`
- Useful to expand an album into its tracks if `song_ids` from 3.2 were insufficient.

### 3.4 `show_playlist_library` — the user's playlists
- path `/library/playlists`, method **GET**
- params: `access_token` (**required**); `is_public` (bool, optional, default **null**);
  `page_index` (optional, default **0**, `>=0`); `page_limit` (optional, default **5**, `>=1, <=20`)
- response (success): list of `{playlist_id, title, is_public, rating, like_count, review_count,
  owner{name,email}, created_at, song_ids[...]}`
- **The playlist songs are already included as `song_ids` in this list response.**
- With `is_public=null` (default) it returns BOTH public and private playlists.
  `is_public=true` → 3 playlists; `is_public=false` → 2 playlists.

### 3.5 `show_playlist` — a playlist's songs (detail)
- path `/playlists/{playlist_id}`, method **GET**
- params: `playlist_id` (int, **required**); `access_token` (**required**)
- response (success): `{playlist_id, title, is_public, rating, like_count, review_count,
  owner, created_at, shareable_link, songs[{id, title, artist_ids}]}`

### 3.6 Related APIs checked for ambiguity
- `show_liked_songs` (`/liked_songs`) — songs the user *liked* (NOT the same as the song library).
- `show_liked_albums` (`/liked_albums`) — albums the user *liked*.
- `show_liked_playlists` (`/liked_playlists`) — playlists the user *liked*, **including playlists
  owned by other people**.

## 4. Observed counts and sample / full ids (read-only, live state)

### 4.1 Song library — `show_song_library`
- **Total = 16** (paged with `page_limit=20`: page0 → 16, page1 → `[]`;
  boundary check `page_limit=5`: pages 0..2 gave 5/5/5, page3 gave 1, page4 gave 0 → 16).
- Full `song_id` list: `[3, 15, 20, 62, 73, 81, 90, 96, 98, 105, 120, 154, 173, 217, 248, 269]`
- Note: several library songs have `album_id = null` (e.g. 90, 96, 98, 105, 120, 154, 173, 217, 248, 269).

### 4.2 Album library — `show_album_library`
- **Total = 8** (page0 limit20 → 8, page1 → `[]`; with limit5 pages gave 5/3).
- Albums and their `song_ids`:
  - 7 "Vibrant Visions" → [33, 34, 35, 36]
  - 9 "Mystical Crescendo" → [44, 45, 46]
  - 10 "Dreamscape Delights" → [47, 48, 49, 50, 51, 52, 53]
  - 11 "Synaptic Serenity" → [54, 55, 56, 57]
  - 13 "Starlight Serenades" → [63, 64, 65, 66]
  - 15 "Whispers in the Wind" → [70, 71, 72, 73]
  - 16 "Electric Dreamscape" → [74, 75, 76]
  - 18 "Echoes of Eternity" → [80, 81, 82]
- Union of all album `song_ids` = **32** distinct song ids.
- Cross-check: `show_album(album_id=7)` returned `songs` = ids [33,34,35,36] — matches `song_ids`.

### 4.3 Playlist library — `show_playlist_library`
- **Total = 5** (page0 limit20 → 5, page1 → `[]`; all owned by `de_ritt@gmail.com`).
- Playlists and their `song_ids`:
  - 593 "October Feels: Autumn Aesthetics" (private) → [58, 125, 136, 160, 197, 199, 236, 299, 306]
  - 594 "Heartbreak Hotel: Songs of Sorrow" (public) → [32, 74, 147, 153, 175, 200, 253]
  - 595 "Velvet Voices: Best of R&B" (public) → [57, 66, 173, 183, 215, 265, 280, 319]
  - 596 "Countryside Chronicles: Folk Favorites" (private) → [32, 46, 89, 108, 121, 128, 156, 210, 256]
  - 597 "Art & Soul: Masterful Melodies" (public) → [57, 113, 115, 128, 161, 169, 224, 244, 310, 323]
- Union of all playlist `song_ids` = **40** distinct song ids.
- Cross-check: `show_playlist(playlist_id=593)` returned `songs` ids [58,125,136,160,197,199,236,299,306] — matches `song_ids`.

### 4.4 Preliminary union (for reference only — NOT the final mission answer)
- `song_library ∪ album_library_songs ∪ playlist_library_songs` = **81** distinct song ids.
- Sorted union:
  `[3, 15, 20, 32, 33, 34, 35, 36, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 62,
   63, 64, 65, 66, 70, 71, 72, 73, 74, 75, 76, 80, 81, 82, 89, 90, 96, 98, 105, 108, 113, 115, 120,
   121, 125, 128, 136, 147, 153, 154, 156, 160, 161, 169, 173, 175, 183, 197, 199, 200, 210, 215, 217,
   224, 236, 244, 248, 253, 256, 265, 269, 280, 299, 306, 310, 319, 323]`
- Overlap between song library and album-library songs: `{73, 81}` (both appear in library and in albums 15 / 18).

### 4.5 Liked-* endpoints (checked to characterize ambiguity)
- `show_liked_songs` total = **26** (differs from the 16-song library).
- `show_liked_albums` total = **13** (differs from the 8-album library).
- `show_liked_playlists` total = **5**, of which only **2** (593, 596) are owned by `de_ritt@gmail.com`;
  the other 3 (112 ric.riddle, 298 nicholas.weber, 49 thomas.solomon) are other users' public playlists.

## 5. Errors observed verbatim

- Missing/invalid token (any library GET without `access_token`):
  `Exception('Response status code is 401:\n{"message":"You are either not authorized to access this spotify API endpoint or your access token is missing, invalid or expired."}')`
- `show_song_library(access_token=..., page_limit=25)`:
  `Exception('Response status code is 422:\n{"message":"Validation error. Reason: \\npage_limit: ensure this value is less than or equal to 20"}')`
- `show_album(album_id=99999)`:
  `Exception('Response status code is 409:\n{"message":"The album with id 99999 does not exist."}')`

## 6. Enumeration plan (which APIs will be used)

To enumerate the three sources and compute a unique-song count:

1. **Song library**: page `apis.spotify.show_song_library(access_token=TOKEN, page_index=i, page_limit=20)`
   for i = 0,1,2,… until an empty page; collect `song_id`.
2. **Album library**: page `apis.spotify.show_album_library(...)` the same way; collect every id in
   each album's `song_ids`. (Optionally expand via `show_album(album_id=...)` to confirm tracks.)
3. **Playlists**: page `apis.spotify.show_playlist_library(access_token=TOKEN, is_public=null, page_index=i, page_limit=20)`
   until empty; collect every id in each playlist's `song_ids`. (Optionally confirm via
   `show_playlist(playlist_id=..., access_token=TOKEN)`.)
4. Deduplicate by **`song_id`** (the unique identifier) across all three sets and count.

`page_limit` max is 20, so paging must be explicit; relying on the default `page_limit=5` risks
under-counting.

## 7. Remaining ambiguities

1. **"all playlists" scope.** `show_playlist_library` returns the user's *own* 5 playlists (both
   public and private). `show_liked_playlists` additionally returns 3 playlists owned by other
   people. "my … all playlists" most plausibly means the user's playlist library (5 playlists,
   contributing 40 ids to the union), but if liked/followed playlists were intended the count would
   differ. This is the main open question for the counting task.
2. **Optional detail endpoints.** The list endpoints already embed `song_ids`, so `show_album` /
   `show_playlist` are optional confirmations, not strictly required for the union.
3. **Unique key.** "Unique songs" is interpreted as distinct `song_id`; whether a song appearing in
   both a library and an album/playlist is one song (yes) is assumed.
4. **Library vs liked.** The song library (16) and album library (8) are distinct from liked songs
   (26) and liked albums (13); the mission wording maps to the *library* endpoints.

## 8. Calls performed (all read-only except login)

`api_docs.show_app_descriptions`; `api_docs.show_api_descriptions(supervisor/spotify)`;
`api_docs.show_api_doc` for show_song_library, show_album_library, show_album, show_playlist_library,
show_playlist, show_liked_songs, show_liked_albums, login, show_account;
`supervisor.show_profile`, `supervisor.show_account_passwords`, `supervisor.show_active_task`;
`spotify.login`; `spotify.show_song_library` (paged), `spotify.show_album_library` (paged),
`spotify.show_album(7)`, `spotify.show_playlist_library` (paged, incl. is_public True/False),
`spotify.show_playlist(593)`, `spotify.show_liked_songs`, `spotify.show_liked_albums`,
`spotify.show_liked_playlists`. No mutating Spotify calls were made.
