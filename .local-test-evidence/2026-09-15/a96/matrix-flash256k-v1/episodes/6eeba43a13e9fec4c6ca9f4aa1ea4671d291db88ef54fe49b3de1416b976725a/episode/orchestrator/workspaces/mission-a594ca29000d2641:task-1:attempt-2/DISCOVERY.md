# DISCOVERY.md — API & Account Discovery (Task-1, read-only)

Mission: "Make me a Spotify playlist called 'My Most Played Album Songs' containing only the
most-played song from each album in my album library."

This Task performed **no application-state mutations**. Only read-only GET discovery calls were made.

---

## 1. App catalogue (`apis.api_docs.show_app_descriptions()`)

Verbatim observed app names/descriptions:

| name | description |
|---|---|
| api_docs | An app to search and explore API documentation. |
| supervisor | An app to access supervisor's personal information, account credentials, addresses, payment cards, and manage the assigned task. |
| amazon | An online shopping app to buy products and manage orders, returns, etc. |
| phone | An app to find and manage contact information for friends, family members, etc., send and receive messages, and manage alarms. |
| file_system | A file system app to create and manage files and folders. |
| spotify | A music streaming app to stream songs and manage song, album and playlist libraries. |
| venmo | A social payment app to send, receive and request money to and from others. |
| gmail | An email app to draft, send, receive, and manage emails. |
| splitwise | A bill splitting app to track and split expenses with people. |
| simple_note | A note-taking app to create and manage notes |
| todoist | A task management app to manage todo lists and collaborate on them with others. |

Relevant apps for this Mission: **spotify** (playlist/album/song operations) and **supervisor** (user accounts/credentials).

---

## 2. Supervisor API (read + task management)

`apis.api_docs.show_api_descriptions(app_name='supervisor')` returned:
- `show_active_task` — Show the currently active task assigned to you by the supervisor.
- `complete_task` — Mark the currently active task as complete with the given answer.
- `show_profile` — Show your supervisor's profile information.
- `show_addresses` — Show your supervisor's addresses.
- `show_payment_cards` — Show your supervisor's payment_cards.
- `show_account_passwords` — Show your supervisor's app account passwords.

### Signatures read (`apis.api_docs.show_api_doc`)

**supervisor.show_profile** — `GET /profile`, parameters: `[]`
Response (success): `first_name`, `last_name`, `email`, `phone_number`, `birthday`, `sex`.

**supervisor.show_account_passwords** — `GET /account_passwords`, parameters: `[]`
Response (success): list of `{account_name: string, password: string}`.

**supervisor.show_active_task** — `GET /active_task`, parameters: `[]`
Response (success): `instruction`, `status`, `answer`.

**supervisor.show_addresses** — `GET /addresses` (documented; called, see §4).

---

## 3. Spotify API — signatures read

### 3.1 Account / auth

**spotify.show_account** — `GET /account`. Required param: `access_token` (string; "Access token obtained from spotify app login.")
Response (success): `first_name`, `last_name`, `email`, `registered_at`, `last_logged_in`, `verified`, `is_premium`.

**spotify.show_profile** — `GET /profile`. Params: `email` (optional string, email address; default null). NOTE: in practice, calling with neither `email` nor `phone_number` fails with HTTP 422 `{"message":"Either email or phone_number must be provided."}`.
Response (success): `first_name`, `last_name`, `email`, `registered_at`.

**spotify.login** — `POST /auth/token`. Required params: `username` (string; "Your account email."), `password` (string).
Response (success): `access_token`, `token_type`.

### 3.2 Library / albums / songs (all need `access_token`)

**spotify.show_album_library** — `GET /library/albums`. Required: `access_token`; optional: `page_index` (default 0, >=0), `page_limit` (default 5, 1..20).
Response (success): list of `{album_id, title, genre, artists:[{id,name}], rating, like_count, review_count, release_date, song_ids:[...], added_at}`.

**spotify.show_album** — `GET /albums/{album_id}`. Required: `album_id` (integer).
Response (success): `album_id`, `title`, `genre`, `artists:[{id,name}]`, `rating`, `like_count`, `review_count`, `release_date`, `shareable_link`, `songs:[{id,title,artist_ids:[...]}]`.

**spotify.show_album_privates** — `GET /albums/{album_id}/privates`. Required: `album_id`, `access_token`.
Response (success): `liked`, `reviewed`, `in_album_library`.

**spotify.show_song** — `GET /songs/{song_id}`. Required: `song_id` (integer).
Response (success): `song_id`, `title`, `album_id`, `album_title`, `duration`, `artists:[{id,name}]`, `release_date`, `genre`, **`play_count`**, `rating`, `like_count`, `review_count`, `shareable_link`.
(Note: `play_count` is the per-song play statistic relevant to "most-played song".)

**spotify.show_song_privates** — `GET /songs/{song_id}/privates`. Required: `song_id`, `access_token`.
Response (success): `liked`, `reviewed`, `in_song_library`, `downloaded`.

**spotify.show_song_library** — `GET /library/songs`. Required: `access_token`; optional: `page_index` (0), `page_limit` (5, 1..20).
Response (success): list of `{song_id, title, album_id, album_title, duration, artists:[{id,name}], added_at}`.

### 3.3 Playlists (mutating — for later Tasks, NOT executed here)

**spotify.create_playlist** — `POST /playlists`. Required: `title` (length>=1), `access_token`; optional: `is_public` (boolean, default false).
Response (success): `message`, `playlist_id`.

**spotify.add_song_to_playlist** — `POST /playlists/{playlist_id}/songs/{song_id}`. Required: `playlist_id` (int), `song_id` (int), `access_token`.
Response (success): `message`.

**spotify.show_playlist_library** — `GET /library/playlists`. Required: `access_token`; optional: `is_public` (boolean, default null), `page_index` (0), `page_limit` (5, 1..20).
Response (success): list of `{playlist_id, title, is_public, rating, like_count, review_count, owner:{name,email}, created_at, song_ids:[...]}`.

**spotify.show_playlist** — `GET /playlists/{playlist_id}`. Required: `playlist_id` (int), `access_token`.
Response (success): `playlist_id`, `title`, `is_public`, `rating`, `like_count`, `review_count`, `owner:{name,email}`, `created_at`, `shareable_link`, `songs:[{id,title,artist_ids:[...]}]`.

**spotify.update_playlist** — `PATCH /playlists/{playlist_id}`. Required: `playlist_id`, `access_token`; optional: `title` (length>=1), `is_public` (boolean).
Response (success): `message`.

---

## 4. Accounts actually found (verbatim tool observations)

### Supervisor profile — `apis.supervisor.show_profile()`
```json
{
 "first_name": "Debra",
 "last_name": "Ritter",
 "email": "de_ritt@gmail.com",
 "phone_number": "3375602296",
 "birthday": "1994-11-30",
 "sex": "female"
}
```

### App account passwords — `apis.supervisor.show_account_passwords()`
```json
[
 {"account_name": "amazon",      "password": "3[Jx#!7"},
 {"account_name": "file_system", "password": "3=gqs{w"},
 {"account_name": "gmail",       "password": "@)9JquT"},
 {"account_name": "phone",       "password": "ag+6@4h"},
 {"account_name": "simple_note", "password": "Tr+)!LH"},
 {"account_name": "splitwise",   "password": "KOl5bPb"},
 {"account_name": "spotify",     "password": "7s7!cA8"},
 {"account_name": "todoist",     "password": "3HD)_re"},
 {"account_name": "venmo",       "password": "hlIf+qU"}
]
```

### Addresses — `apis.supervisor.show_addresses()`
- Home: 5309 Rios Cliff Suite 287, Seattle, Washington, United States, 84348
- Work: 162 Smith Lake Suite 664, Seattle, Washington, United States, 18461

### Spotify profile — `apis.spotify.show_profile(email='de_ritt@gmail.com')`
```json
{
 "first_name": "Debra",
 "last_name": "Ritter",
 "email": "de_ritt@gmail.com",
 "registered_at": "2022-07-06T10:52:39"
}
```

### Derived account identifiers (for downstream Tasks)
- **Spotify username (login email): `de_ritt@gmail.com`**
- **Spotify password: `7s7!cA8`** (from `supervisor.show_account_passwords`, `account_name == "spotify"`)
- Spotify owner name: **Debra Ritter**

---

## 5. Things NOT found / limitations (read-only constraint)

- **No `access_token` was obtained.** `spotify.login` is a `POST`, and this Task must not mutate
  application state, so login was NOT executed. Consequently all authenticated endpoints
  (`show_album_library`, `show_song`, `show_song_privates`, `show_playlist*`, etc.) were not called
  and their real data (album list, per-song play counts, existing playlists) is **not** recorded here.
- **`spotify.show_account`** was NOT called (it requires an `access_token` from login); its private
  fields (`last_logged_in`, `verified`, `is_premium`) were therefore not observed.
- **`spotify.show_profile()` with no arguments fails**: HTTP 422 `{"message":"Either email or phone_number must be provided."}`.
- **`spotify.show_album_library()` without login fails**: HTTP 401
  `{"message":"You are either not authorized to access this spotify API endpoint or your access token is missing, invalid or expired."}`
- No Spotify playlist, album, or song state was read or changed in this Task.

## 6. Notes for downstream Tasks

1. To read the album library: `login(username='de_ritt@gmail.com', password='7s7!cA8')` → capture
   `access_token`, then `show_album_library(access_token=..., page_index=..., page_limit=...)`.
2. Album → songs: `show_album(album_id=...)` returns `songs:[{id,title,...}]`.
3. Per-song play count for "most-played": `show_song(song_id=...)` returns `play_count`.
4. Create playlist: `create_playlist(title='My Most Played Album Songs', access_token=...)`.
5. Add chosen songs: `add_song_to_playlist(playlist_id=..., song_id=..., access_token=...)`.
6. Verify: `show_playlist_library` / `show_playlist` (owner email should be `de_ritt@gmail.com`).
