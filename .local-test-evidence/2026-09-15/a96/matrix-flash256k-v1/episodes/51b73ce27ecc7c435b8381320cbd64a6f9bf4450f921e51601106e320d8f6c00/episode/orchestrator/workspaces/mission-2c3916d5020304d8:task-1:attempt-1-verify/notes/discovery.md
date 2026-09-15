# Discovery Notes (Task A) — Accounts & Spotify API Contract

All statements below come from real `appworld_execute` tool observations in the shared
world. Raw tool outputs are quoted/reproduced verbatim where possible.

## 1. App inventory (apis.api_docs.show_app_descriptions())

Apps present: `api_docs`, `supervisor`, `amazon`, `phone`, `file_system`, `spotify`,
`venmo`, `gmail`, `splitwise`, `simple_note`, `todoist`.

The relevant app is **`spotify`** — "A music streaming app to stream songs and manage
song, album and playlist libraries."

## 2. User account discovery (supervisor)

### supervisor.show_profile()
```
{
 "first_name": "Glenn",
 "last_name": "Burton",
 "email": "glenn.burton@gmail.com",
 "phone_number": "8638518861",
 "birthday": "1993-08-13",
 "sex": "male"
}
```

### supervisor.show_account_passwords()  (account_name -> app account)
- amazon
- file_system
- gmail
- phone
- simple_note
- splitwise
- **spotify  -> password `EbhXe%D`**  (account_name key is the app name)
- todoist
- venmo

The Spotify credential's login identifier is the supervisor's email
`glenn.burton@gmail.com` (see below).

### spotify.login(username="glenn.burton@gmail.com", password="EbhXe%D")
POST `/auth/token`, params `username` (=account email) + `password`.
Success → `{"access_token": "<JWT>", "token_type": "Bearer"}`.
Observed token (truncated): `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9....`

### spotify.show_account(access_token=<token>)  → GET /account
```
{
 "first_name": "Glenn",
 "last_name": "Burton",
 "email": "glenn.burton@gmail.com",
 "registered_at": "2022-04-03T10:27:47",
 "last_logged_in": "2022-04-03T10:27:47",
 "verified": true,
 "is_premium": false
}
```
The authenticated identity is therefore **Glenn Burton / glenn.burton@gmail.com**.

Error evidence (auth required): call without token →
```
Response status code is 401:
{"message":"You are either not authorized to access this spotify API endpoint or your access token is missing, invalid or expired."}
```
`show_account` also returned this 401 before login.

## 3. The three libraries — which API provides each

All three libraries are provided by the **spotify** app; every one requires a valid
`access_token` (401 otherwise).

| Library         | API                          | HTTP path             |
|-----------------|------------------------------|-----------------------|
| song library    | `spotify.show_song_library`  | GET /library/songs    |
| album library   | `spotify.show_album_library` | GET /library/albums   |
| playlist library| `spotify.show_playlist_library` | GET /library/playlists |

### 3.1 show_song_library(access_token, page_index=0, page_limit=5)
- `page_index` optional, default 0, `>=0`.
- `page_limit` optional, default **5**, constraint `>=1, <=20` (21 → HTTP 422:
  `page_limit: ensure this value is less than or equal to 20`).
- Success = list of:
```
{
 "song_id": int, "title": str, "album_id": int|null, "duration": int,
 "artists": [{"id": int, "name": str}],
 "added_at": "YYYY-MM-DDTHH:MM:SS"
}
```
- **NOT included**: no `genre`, no `play_count`, no `album_title` in actual output
  (the doc schema lists `album_title`, but real rows returned `album_id` only, sometimes
  `null` — e.g. song 92, 109 had `album_id: null`).

### 3.2 show_album_library(access_token, page_index=0, page_limit=5)
- Same pagination defaults/constraints (default 5, max 20).
- Success = list of:
```
{
 "album_id": int, "title": str, "genre": str,
 "artists": [{"id": int, "name": str}],
 "rating": float, "like_count": int, "review_count": int,
 "release_date": "...", "song_ids": [int, ...], "added_at": "..."
}
```
- **`genre` IS present here** (album-level genre). No `play_count`.

### 3.3 show_playlist_library(access_token, is_public=None, page_index=0, page_limit=5)
- `is_public` optional boolean: whether to return public or private playlists.
  When omitted, the default page returned a **mix** of public and private.
- Success = list of:
```
{
 "playlist_id": int, "title": str, "is_public": bool,
 "rating": float, "like_count": int, "review_count": int,
 "owner": {"name": str, "email": str},
 "created_at": "...", "song_ids": [int, ...]
}
```
- **No `genre`, no `play_count`** — only `song_ids`. Song details must be resolved
  per-song via `show_song`.

## 4. Where play_count and genre live

### spotify.show_song(song_id)  → GET /songs/{song_id}
- Requires only `song_id` (no token).
- Success includes **`genre`** and **`play_count`** plus `album_id`, `release_date`,
  `rating`, `like_count`, `review_count`, `shareable_link`.
Example (song 92): `genre: "R&B"`, `play_count: 972`.
Example (song 33): `genre: "hip-hop"`, `play_count: 809`.

So: **the authoritative per-song `play_count` and `genre` are obtained from
`spotify.show_song(song_id)`**, NOT from any library listing.

### spotify.show_album(album_id)  → GET /albums/{album_id}
- Includes `genre` (album) and `songs: [{id, title, artist_ids}]`. No play_count.
Example album 2 "Celestial Harmonies" genre "R&B", songs ids [8,9,10].

### spotify.show_playlist(playlist_id, access_token)  → GET /playlists/{playlist_id}
- Requires `playlist_id` + `access_token`.
- Includes `songs: [{id, title, artist_ids}]`, no genre / play_count.
Example playlist 621 songs ids [76,99,111,273,323].

### spotify.show_genres()  → GET /genres
Returns the canonical genre vocabulary:
```
["EDM", "R&B", "indie", "hip-hop", "jazz", "rock", "pop", "classical", "reggae", "country"]
```
⇒ **"R&B"** is a valid exact genre label; note the ampersand and uppercase.

## 5. Current-state sizes of the three libraries (page_limit=20 walk)

- song library: **19** songs — ids
  [33,44,56,92,109,114,135,137,156,159,202,225,226,241,265,302,303,308,324]
- album library: **8** albums — ids [2,3,8,10,11,12,13,14]
- playlist library: **4** playlists — ids [621,622,623,624]
  (public: 621,622 ; private: 623,624)

Pagination behaviour verified: `page_limit=6` returns the first 6 ids
[33,44,56,92,109,114]; empty list terminates a walk.

## 6. Cross-library implications for the goal (Top 4 most played R&B songs)

- R&B membership is determined per song via `show_song(...).genre == "R&B"`
  (album library `genre` can corroborate an album, but song-level genre is authoritative
  and playlists mix genres).
- Play count comes from `show_song(...).play_count` (float/int, may be large).
- Songs must be unioned across the three sources, deduped by `song_id`:
  * song library `song_id`s directly;
  * album library `song_ids` (and/or `show_album(...).songs[].id`);
  * playlist library `song_ids` (and/or `show_playlist(...).songs[].id`).
- Because album/playlist entries only expose song ids, every candidate song id must be
  resolved with `show_song` to read genre + play_count, then filter `genre=="R&B"` and
  sort by `play_count` descending to take the top 4 titles.

## 7. Errors / gotchas observed (verbatim)
- Missing/invalid token (401): "You are either not authorized to access this spotify API
  endpoint or your access token is missing, invalid or expired."
- `page_limit=21` (422): "Validation error. Reason: page_limit: ensure this value is less
  than or equal to 20".
- Library list rows omit `genre`/`play_count`; do not assume they carry them.
- Some library songs have `album_id: null` (e.g. 92, 109), so album-based lookups are not
  exhaustive — always also consult the direct song-library ids and playlist ids.
