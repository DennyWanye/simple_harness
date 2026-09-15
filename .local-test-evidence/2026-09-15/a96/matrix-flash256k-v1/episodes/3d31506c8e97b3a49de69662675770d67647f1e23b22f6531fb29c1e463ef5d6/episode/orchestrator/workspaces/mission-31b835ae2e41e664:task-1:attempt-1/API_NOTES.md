# API_NOTES.md — Spotify wiring & endpoint contract for counting unique songs

Task: mission-31b835ae2e41e664:task-1
Scope: discover the simulated user's Spotify account wiring and the public API surface
needed to enumerate songs across (1) the song library, (2) the albums library and
(3) all playlists. Read-only discovery only. No final count is computed here.

---

## 0. Environment / how to call APIs

- The Python shell exposes a global `apis` object. **`import apis` is NOT allowed**
  (raises `Usage of the following module is not allowed: apis`). Call endpoints directly
  as `apis.<app>.<api>(...)`.
- Docs discovery:
  - `apis.api_docs.show_app_descriptions()` -> list of apps. Relevant app name is
    lowercase **`spotify`** ("A music streaming app to stream songs and manage song,
    album and playlist libraries"), plus `supervisor` and `api_docs`.
  - `apis.api_docs.show_api_descriptions(app_name='spotify')` -> list of ~90 endpoints.
  - `apis.api_docs.show_api_doc(app_name='spotify', api_name='<name>')` -> path, method,
    parameters (name/type/required/default/constraints) and response schemas.
- Every Spotify endpoint that touches private data requires `access_token` (obtained
  from login). A missing/invalid/expired token raises a 401 `Exception`:
  `Response status code is 401: {"message":"You are either not authorized to access this
  spotify API endpoint or your access token is missing, invalid or expired."}`

## 1. Account wiring (who is the user, how to log in)

- `apis.supervisor.show_profile()` ->
  `{"first_name":"Debra","last_name":"Ritter","email":"de_ritt@gmail.com",
    "phone_number":"3375602296","birthday":"1994-11-30","sex":"female"}`
- `apis.supervisor.show_account_passwords()` -> list of `{account_name, password}` for
  every app; the Spotify entry is `{"account_name":"spotify","password":"7s7!cA8"}`.
  (`show_account_passwords` takes no parameters.)
- Spotify login: `apis.spotify.login(username=<email>, password=<password>)`
  - `username` (required) = the account **email** (`de_ritt@gmail.com`).
  - Returns `{"access_token":"<jwt>","token_type":"Bearer"}`. The JWT payload `sub`
    is `spotify+de_ritt@gmail.com`, confirming the account identity.
  - The token is time-limited (`exp` claim). Obtain a fresh token per session.
- Identity confirmation: `apis.spotify.show_account(access_token=<jwt>)` returned
  `{"first_name":"Debra","last_name":"Ritter","email":"de_ritt@gmail.com",
    "registered_at":"2022-07-06T10:52:39","last_logged_in":"2022-07-06T10:52:39",
    "verified":true,"is_premium":false}`.
  This exactly matches the supervisor profile, so the Spotify account belongs to the
  supervisor (Debra Ritter).

## 2. Endpoints for the three required surfaces

All three are GET, require `access_token`, are offset-paginated, and accept
`page_index` (default 0, >=0) and `page_limit` (default 5, 1..20). Default sort is
ascending by the item's numeric id.

### 2.1 Song library
- API: `apis.spotify.show_song_library(access_token, page_index=0, page_limit=20)`
- Path/method: `GET /library/songs`
- Required param: `access_token`. Optional: `page_index` (default 0), `page_limit`
  (default 5, max 20).
- Success shape: **list** of
  `{song_id:int, title:str, album_id:int|null, duration:int,
    artists:[{id:int,name:str}], added_at:"YYYY-MM-DDTHH:MM:SS"}`
  (note: `album_id` may be `null`; `album_title` is not returned by this endpoint).

### 2.2 Albums library
- API: `apis.spotify.show_album_library(access_token, page_index=0, page_limit=20)`
- Path/method: `GET /library/albums`
- Required param: `access_token`. Optional: `page_index`, `page_limit` (as above).
- Success shape: **list** of
  `{album_id:int, title:str, genre:str, artists:[{id:int,name:str}], rating:float,
    like_count:int, review_count:int, release_date:"...", song_ids:[int], added_at:"..."}`
- IMPORTANT: each album already exposes `song_ids` — the album's track list — so the
  album surface can be expanded to songs without extra calls.

### 2.3 Playlist library ("all playlists")
- API: `apis.spotify.show_playlist_library(access_token, is_public=None,
  page_index=0, page_limit=20)`
- Path/method: `GET /library/playlists`
- Required param: `access_token`. Optional: `is_public` (bool, default **null = both**),
  `page_index`, `page_limit` (as above).
- Success shape: **list** of
  `{playlist_id:int, title:str, is_public:bool, rating:float, like_count:int,
    review_count:int, owner:{name:str,email:str}, created_at:"...", song_ids:[int]}`
- Filtering behaviour verified:
  - omitted `is_public` -> all library playlists (public AND private),
  - `is_public=True` -> only public, `is_public=False` -> only private.
  In this world `all` = 3 public + 2 private (`[593,594,595,596,597]`), matching the
  union of the two filtered calls.
- IMPORTANT: each playlist already exposes `song_ids`.
- Detail endpoint (alternative / cross-check):
  `apis.spotify.show_playlist(playlist_id, access_token)` -> single object with
  `shareable_link` and a full `songs:[{id,title,artist_ids:[int]}]` array (verified on
  playlist 593; its `songs` ids equal its `song_ids` from the library call).

### 2.4 Ambiguity note on "all playlists" (flag for the counting task)
"all playlists" is ambiguous between two distinct surfaces:
1. **Playlist library** (`show_playlist_library`, default = public+private) — the
   playlists the user *owns/holds* in their library. In this world: 5 playlists
   (593,594,595,596,597), all owned by Debra Ritter.
2. **Liked playlists** (`apis.spotify.show_liked_playlists(access_token, page_index=0,
   page_limit=5, sort_by='-liked_at')`, `GET /liked_playlists`) — playlists the user
   *liked*, which include playlists **owned by other users**. Verified shape:
   same fields as the library plus `liked_at`. In this world it also returns 593, 596
   (own) plus others owned by e.g. Richard Riddle, Nicholas Weber, Thomas Solomon.
These are NOT the same set. The default reading of "my ... playlists" is the **playlist
library** (surface 1); the counting task must state which one it unions and, if it uses
the library, must NOT be misled by `show_liked_playlists` returning other owners' lists.

## 3. Pagination contract (verified)

- Offset semantics: `page_index * page_limit` items are skipped; each page returns up to
  `page_limit` items in ascending id order.
- Termination: a page returns an **empty list** when the offset is past the end.
- `page_limit` max is 20 (constraint `>=1, <=20`); default is 5.
- Verified examples:
  - Songs `page_index=0,page_limit=5` -> ids `[3,15,20,62,73]`;
    `page_index=1,page_limit=5` -> `[81,90,96,98,105]`; `page_index=3,page_limit=5` -> `[269]`;
    `page_index=4,page_limit=5` -> `[]`.
  - Albums `page_index=0,page_limit=3` -> `[7,9,10]`; `p1` -> `[11,13,15]`; `p2` -> `[16,18]`.
  - Playlists `page_index=0,page_limit=2` -> `[593,594]`; `p1` -> `[595,596]`.
- Observed page counts (raw pagination facts, NOT the unique-song union; the union is
  the counting task's job):
  - `show_song_library(page_limit=20)` -> 16 items; `page_index=1` empty.
  - `show_album_library(page_limit=20)` -> 8 items; `page_index=1` empty.
  - `show_playlist_library(page_limit=20)` -> 5 items; `page_index=1` empty.
- Safe enumeration recipe: loop `page_index=0,1,2,...` with `page_limit=20` and stop on
  the first empty page; concatenate. (An empty page is guaranteed to terminate.)

## 4. Supporting / cross-check endpoints

- `apis.spotify.show_song(song_id)` (`GET /songs/{song_id}`) -> full song object incl.
  `album_title`, `genre`, `play_count`, `rating`, `like_count`, `review_count`,
  `shareable_link`. No `access_token` required in its schema.
- `apis.spotify.show_song_privates(song_id, access_token)` ->
  `{liked, reviewed, in_song_library, downloaded}` (per-user flags).
- `apis.spotify.show_album_privates(album_id, access_token)` ->
  `{liked, reviewed, in_album_library}`.
- `apis.spotify.search_songs(...)`, `apis.spotify.search_albums(...)`,
  `apis.spotify.search_playlists(access_token, query='', min_like_count=0,
  min_rating=0, page_index=0, page_limit=5, sort_by=None)` — search surfaces; the
  playlist search covers **all public playlists + your own private playlists**, so it
  is NOT a substitute for the user's own playlist library when counting only own lists.
- `apis.spotify.show_liked_songs`, `show_liked_albums`, `show_downloaded_songs` —
  other personal song lists; only relevant if the counting task's scope explicitly
  includes "liked"/"downloaded" songs (the stated scope is song library + albums
  library + playlists, so these are out of scope unless the user says otherwise).
- `apis.supervisor.show_active_task()` -> `{instruction, status, answer}` for the
  assigned task; `apis.supervisor.complete_task()` marks it done (mutating — do not
  call until the whole goal is met).

## 5. Fresh access token (for the counting task)

```
tok = apis.spotify.login(username='de_ritt@gmail.com', password='7s7!cA8')['access_token']
```

## 6. What was actually executed (read-only) in this task

1. `apis.api_docs.show_app_descriptions()`
2. `apis.api_docs.show_api_descriptions(app_name='spotify')`
3. `apis.api_docs.show_api_descriptions(app_name='supervisor')`
4. `apis.api_docs.show_api_doc(app_name='supervisor', api_name='show_account_passwords')`
5. `apis.api_docs.show_api_doc(app_name='spotify', api_name=...)` for `login`,
   `show_account`, `show_song_library`, `show_album_library`, `show_playlist_library`,
   `show_playlist`, `show_song`, `show_liked_songs`, `show_liked_albums`,
   `show_liked_playlists`, `show_downloaded_songs`, `show_song_privates`,
   `show_album_privates`, `search_playlists`
6. `apis.supervisor.show_account_passwords()`, `apis.supervisor.show_profile()`
7. `apis.spotify.login(...)`, `apis.spotify.show_account(...)`
8. `apis.spotify.show_song_library/show_album_library/show_playlist_library` at multiple
   page_index/page_limit combinations; `show_playlist_library(is_public=True/False)`;
   `show_playlist(593)`; `show_liked_playlists(...)`; `search_playlists(...)`;
   invalid-token 401 check.

No state was mutated. Uncompleted / limitations:
- The unique-song count itself is intentionally NOT computed here (next task's job).
- The "all playlists" scope ambiguity (library vs liked) is flagged above and must be
  resolved by the counting task before concluding.
- All observations are limited to the calls actually made; the access token is
  time-limited, so downstream tasks must re-login.
