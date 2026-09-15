# Task A — Live AppWorld Spotify Discovery

Scope: read-only discovery of the live AppWorld environment for the mission goal
"How many unique songs are there across my Spotify song library, albums library and all playlists?".
No mutations were performed. All observations below are from actual `appworld_execute` calls.

## 1. Supervisor / account context

- `apis.supervisor.show_profile()` →
  `{'first_name': 'Debra', 'last_name': 'Ritter', 'email': 'de_ritt@gmail.com', 'phone_number': '3375602296', 'birthday': '1994-11-30', 'sex': 'female'}`
- `apis.supervisor.show_addresses()` → Home 5309 Rios Cliff Suite 287, Seattle, Washington, 84348; Work 162 Smith Lake Suite 664, Seattle, Washington, 18461.
- `apis.supervisor.show_account_passwords()` includes the Spotify credential:
  `{'account_name': 'spotify', 'password': '7s7!cA8'}`.
- `apis.supervisor.show_active_task()` → instruction: "How many unique songs are there across my Spotify song library, albums library and all playlists?", status None, answer `<<NOT_GIVEN>>`.

## 2. Spotify authentication

- Doc `apis.api_docs.show_api_doc(app_name='spotify', api_name='login')`: POST `/auth/token`,
  required params `username` (account email) and `password`; success returns `{access_token, token_type}`.
- Unauthenticated call `apis.spotify.show_account()` (no token) failed verbatim:
  `Exception('Response status code is 401:\n{"message":"You are either not authorized to access this spotify API endpoint or your access token is missing, invalid or expired."}')`
- `apis.spotify.login(username='de_ritt@gmail.com', password='7s7!cA8')` succeeded, returning a Bearer `access_token`
  (JWT, token_type `Bearer`; exp 1684412990). All library reads below use this token.

## 3. Exact API contract for enumeration

### (1) Song library — `apis.spotify.show_song_library`
- Path GET `/library/songs`; description "Get a list of songs in the user's song library."
- Parameters: `access_token` (string, REQUIRED); `page_index` (integer, optional, default 0, value>=0);
  `page_limit` (integer, optional, default 5, 1<=value<=20).
- Success schema (list item): `song_id, title, album_id, album_title, duration, artists[{id,name}], added_at`.
- Observed fields actually returned omit `album_title`: e.g. `{'song_id': 3, 'title': 'The Fragrance of Fading Roses', 'album_id': 1, 'duration': 270, 'artists': [{'id': 3, 'name': 'Jasper Skye'}, {'id': 26, 'name': 'Isabella Cruz'}, {'id': 6, 'name': 'Seraphina Dawn'}], 'added_at': '2023-01-15T02:56:19'}`.
- Observed: `page_index=0, page_limit=20` → 16 songs; page_limit=20 = max page size.
  Sample ids: 3, 15, 20, 62, 73, 81, 90, 96, 98, 105, 120, 154, 173, 217, 248, 269.
- `page_index=1, page_limit=20` → `[]` (no further pages).
- Default call (no page args) → 5 rows (ids 3,15,20,62,73), confirming default page_limit=5.

### (2) Album library — `apis.spotify.show_album_library`
- Path GET `/library/albums`; description "Get a list of albums in the user's album library."
- Parameters: `access_token` (REQUIRED); `page_index` (default 0); `page_limit` (default 5, 1..20).
- Success schema (list item): `album_id, title, genre, artists[{id,name}], rating, like_count, review_count, release_date, song_ids[], added_at`.
- Observed: `page_index=0, page_limit=20` → 8 albums; page_index=1 → `[]`.
  Sample: album_id 7 'Vibrant Visions' song_ids [33,34,35,36]; album_id 10 'Dreamscape Delights' song_ids [47,48,49,50,51,52,53]; album 11 song_ids [54,55,56,57]; album 13 [63,64,65,66]; album 15 [70,71,72,73]; album 16 [74,75,76]; album 18 [80,81,82]; album 9 [44,45,46].
- Default call (no page args) → 5 rows (album ids 7,9,10,11,13), confirming default page_limit=5.
- Each album's tracks — `apis.spotify.show_album(album_id)` (GET `/albums/{album_id}`, only param `album_id` REQUIRED, no token required):
  returns `{album_id, title, genre, artists, rating, like_count, review_count, release_date, shareable_link, songs:[{id, title, artist_ids[]}]}`.
  Observed `show_album(7)` songs = ids 33 'Unveiled', 34, 35, 36 — matching the library's `song_ids`.
  The album library's `song_ids` field and `show_album(...).songs[].id` are equivalent sources for album tracks.

### (3) Playlists — `apis.spotify.show_playlist_library`
- Path GET `/library/playlists`; description "Get a list of playlists in the user's playlist library."
- Parameters: `access_token` (REQUIRED); `is_public` (boolean, optional, default null);
  `page_index` (default 0); `page_limit` (default 5, 1..20).
- Success schema (list item): `playlist_id, title, is_public, rating, like_count, review_count, owner{name,email}, created_at, song_ids[]`.
- Observed with no `is_public` filter: 5 playlists (ids 593,594,595,596,597), all owned by Debra Ritter (de_ritt@gmail.com). page_index=1 → `[]`.
  - `is_public=True` → 3 (594,595,597). `is_public=False` → 2 (593,596).
  - Sample song_ids: 593 [58,125,136,160,197,199,236,299,306]; 594 [32,74,147,153,175,200,253]; 595 [57,66,173,183,215,265,280,319]; 596 [32,46,89,108,121,128,156,210,256]; 597 [57,113,115,128,161,169,224,244,310,323].
- Default call (no page args) → 5 rows (ids 593..597), confirming default page_limit=5.
- Each playlist's songs — `apis.spotify.show_playlist(playlist_id, access_token)` (GET `/playlists/{playlist_id}`, both params REQUIRED):
  returns `{..., songs:[{id, title, artist_ids[]}]}`.
  Observed `show_playlist(593)` songs = ids 58,125,136,160,197,199,236,299,306 — matching the library's `song_ids`.
  (`show_playlist_privates(playlist_id, access_token)` returns only `{liked, reviewed, in_playlist_library}`, not songs.)

## 4. Related read-only endpoints checked (context, not used for the count)
- `show_liked_songs` (GET `/liked_songs`, params access_token, page_index=0, page_limit=5, sort_by='-liked_at') — a separate "liked songs" list, distinct from the song library.
- `show_liked_albums` (GET `/liked_albums`, similar) — separate from the album library.

## 5. Enumeration plan (APIs to use)
1. Song library: `show_song_library(access_token, page_index, page_limit=20)` paging until empty → collect `song_id` (observed one page of 16 here).
2. Album library: `show_album_library(access_token, page_index, page_limit=20)` paging until empty → for each album collect its tracks, either from `song_ids` or `show_album(album_id).songs[].id`.
3. Playlists: `show_playlist_library(access_token, page_index, page_limit=20)` with `is_public` unset (returns public+private) paging until empty → for each playlist collect `song_ids` (or `show_playlist(playlist_id, access_token).songs[].id` corresponds).
4. Union of all collected `song_id`s, deduplicated → unique-song count. Default page_limit is 5, so pagination must be driven with page_limit=20 (max) and page_index incremented while non-empty.

## 6. Ambiguities remaining
- "all playlists": interpreted as the user's playlist library (5 playlists via `show_playlist_library` without `is_public`). Whether "liked playlists" (`show_liked_playlists`) should also be included is not determined by the question.
- "song library" vs "liked songs": `show_song_library` and `show_liked_songs` are different endpoints; the question says "song library", so `show_song_library` is used. Confirmation of overlap between the two is not established here.
- Uniqueness identity: songs are assumed unique by `song_id` (title collisions possible). Confirmed songs can appear in both an album and a playlist (e.g. song 73 in song library and album 15; song 57 in playlists 595 and 597), and song 32 appears in playlists 594 and 596, so deduplication by id matters.
- Whether album-library tracks should be limited to songs actually present in the user's library vs every track of each album: the question says "albums library", so all tracks of each album in the album library are counted.

## 7. Errors observed (verbatim)
- `apis.spotify.show_account()` without access_token → `Exception('Response status code is 401:\n{"message":"You are either not authorized to access this spotify API endpoint or your access token is missing, invalid or expired."}')`
- No other errors were returned by the read-only library calls.
