# RECON.md — AppWorld execution surface for the Spotify "unique songs" question

## Scope
This report establishes ONLY the reconnaissance surface needed to answer:
"How many unique songs are there across my Spotify song library, albums library and all playlists?"
It records: (a) the exact app world, (b) the exact API names and their required parameters,
(c) the resolved simulated user / Spotify account identifier, and (d) verbatim tool observations
from real read-only calls. It does NOT compute or assert the final song count.

## 1. Execution surface

`apis.api_docs.show_app_descriptions()` returned the list of available apps (verbatim output):

```
[
 {"name": "api_docs", "description": "An app to search and explore API documentation."},
 {"name": "supervisor", "description": "An app to access supervisor's personal information, account credentials, addresses, payment cards, and manage the assigned task."},
 {"name": "amazon", "description": "An online shopping app to buy products and manage orders, returns, etc."},
 {"name": "phone", "description": "An app to find and manage contact information for friends, family members, etc., send and receive messages, and manage alarms."},
 {"name": "file_system", "description": "A file system app to create and manage files and folders."},
 {"name": "spotify", "description": "A music streaming app to stream songs and manage song, album and playlist libraries."},
 {"name": "venmo", "description": "A social payment app to send, receive and request money to and from others."},
 {"name": "gmail", "description": "An email app to draft, send, receive, and manage emails."},
 {"name": "splitwise", "description": "A bill splitting app to track and split expenses with people."},
 {"name": "simple_note", "description": "A note-taking app to create and manage notes"},
 {"name": "todoist", "description": "A task management app to manage todo lists and collaborate on them with others."}
]
```

Relevant apps for this task: `supervisor` (account discovery) and `spotify` (music library).

`apis.api_docs.show_api_descriptions(app_name='supervisor')` returned (verbatim):

```
[
 {"name": "show_active_task", "description": "Show the currently active task assigned to you by the supervisor."},
 {"name": "complete_task", "description": "Mark the currently active task as complete with the given answer."},
 {"name": "show_profile", "description": "Show your supervisor's profile information."},
 {"name": "show_addresses", "description": "Show your supervisor's addresses."},
 {"name": "show_payment_cards", "description": "Show your supervisor's payment_cards."},
 {"name": "show_account_passwords", "description": "Show your supervisor's app account passwords."}
]
```

`apis.api_docs.show_api_descriptions(app_name='spotify')` returned a long list including these
APIs relevant to the goal (verbatim names): `show_account`, `login`, `logout`, `show_song_library`,
`show_album_library`, `show_playlist_library`, `show_playlist`, `show_playlist_privates`,
`add_song_to_library`, `remove_song_from_library`, `add_album_to_library`, `remove_album_from_library`,
`add_song_to_playlist`, `remove_song_from_playlist`, `show_liked_songs`, `show_liked_albums`,
`show_liked_playlists`, `search_songs`, `search_albums`, `search_playlists`.

## 2. Required API docs (exact names + required parameters)

### supervisor.show_profile
- Params: none (no `access_token` needed).
- Returns supervisor's own profile: `first_name`, `last_name`, `email`, `phone_number`, `birthday`, `sex`.

### supervisor.show_account_passwords
- Params: none.
- Returns list of `{account_name, password}` for the supervisor's app accounts, including `spotify`.

### supervisor.show_active_task
- Params: none.
- Returns `{instruction, status, answer}`.

### spotify.login
- Method: POST `/auth/token`
- Required params:
  - `username` (string, required) — "Your account email."
  - `password` (string, required) — "Your account password."
- Returns `{access_token, token_type}`.

### spotify.show_account
- Method: GET `/account`
- Required param: `access_token` (string, required) — "Access token obtained from spotify app login."
- Returns `{first_name, last_name, email, registered_at, last_logged_in, verified, is_premium}`.

### spotify.show_song_library
- Method: GET `/library/songs`
- Required param: `access_token` (string, required).
- Optional: `page_index` (integer, default 0, >=0), `page_limit` (integer, default 5, 1..20).
- Returns list of songs: `{song_id, title, album_id, album_title, duration, artists:[{id,name}], added_at}`.

### spotify.show_album_library
- Method: GET `/library/albums`
- Required param: `access_token` (string, required).
- Optional: `page_index` (integer, default 0, >=0), `page_limit` (integer, default 5, 1..20).
- Returns list of albums: `{album_id, title, genre, artists:[{id,name}], rating, like_count,
  review_count, release_date, song_ids:[...], added_at}`.

### spotify.show_playlist_library
- Method: GET `/library/playlists`
- Required param: `access_token` (string, required).
- Optional: `is_public` (boolean, default null — "Whether to show public playlists or private playlists"),
  `page_index` (integer, default 0, >=0), `page_limit` (integer, default 5, 1..20).
- Returns list of playlists: `{playlist_id, title, is_public, rating, like_count, review_count,
  owner:{name,email}, created_at, song_ids:[...]}`.

### spotify.show_playlist
- Method: GET `/playlists/{playlist_id}`
- Required params: `playlist_id` (integer, required), `access_token` (string, required).
- Returns `{playlist_id, title, is_public, rating, like_count, review_count, owner:{name,email},
  created_at, shareable_link, songs:[{id,title,artist_ids}]}`.

## 3. Resolved simulated user / account identifier

Resolved from **supervisor** (no access token required):

- `supervisor.show_profile()` →
  ```
  {'first_name': 'Debra', 'last_name': 'Ritter', 'email': 'de_ritt@gmail.com', 'phone_number': '3375602296', 'birthday': '1994-11-30', 'sex': 'female'}
  ```
- `supervisor.show_account_passwords()` contains the Spotify credential:
  ```
  {'account_name': 'spotify', 'password': '7s7!cA8'}
  ```

Therefore the Spotify login credentials are:
- **username:** `de_ritt@gmail.com`
- **password:** `7s7!cA8`

**Identity verification (real read-only call):**
`apis.spotify.login(username='de_ritt@gmail.com', password='7s7!cA8')` →
```
{'access_token': '<JWT>', 'token_type': 'Bearer'}
```
then `apis.spotify.show_account(access_token=<token>)` →
```
{'first_name': 'Debra', 'last_name': 'Ritter', 'email': 'de_ritt@gmail.com', 'registered_at': '2022-07-06T10:52:39', 'last_logged_in': '2022-07-06T10:52:39', 'verified': True, 'is_premium': False}
```
The Spotify account email `de_ritt@gmail.com` and name `Debra Ritter` match the supervisor
`Debra Ritter` / `de_ritt@gmail.com`, confirming the correct simulated user's Spotify account.

## 4. Verbatim read-only observations (reconnaissance only)

These confirm the three collection endpoints are reachable and return the expected shapes.
They are NOT a full enumeration and do NOT establish the final count.

- `apis.spotify.show_song_library(access_token=<token>)` (default page_limit=5) returned 5 song
  records, first element:
  ```
  {'song_id': 3, 'title': 'The Fragrance of Fading Roses', 'album_id': 1, 'duration': 270, 'artists': [{'id': 3, 'name': 'Jasper Skye'}, {'id': 26, 'name': 'Isabella Cruz'}, {'id': 6, 'name': 'Seraphina Dawn'}], 'added_at': '2023-01-15T02:56:19'}
  ```
- `apis.spotify.show_album_library(access_token=<token>)` (default page_limit=5) returned 5 album
  records, first element:
  ```
  {'album_id': 7, 'title': 'Vibrant Visions', 'genre': 'hip-hop', 'artists': [{'id': 11, 'name': 'Eliana Harper'}], 'rating': 3.4, 'like_count': 44, 'review_count': 20, 'release_date': '2021-02-05T04:10:20', 'song_ids': [33, 34, 35, 36], 'added_at': '2022-08-14T01:47:52'}
  ```
- `apis.spotify.show_playlist_library(access_token=<token>)` (default page_limit=5) returned 5
  playlists, first element:
  ```
  {'playlist_id': 593, 'title': 'October Feels: Autumn Aesthetics', 'is_public': False, 'rating': 0.0, 'like_count': 1, 'review_count': 0, 'owner': {'name': 'Debra Ritter', 'email': 'de_ritt@gmail.com'}, 'created_at': '2022-12-30T07:59:32', 'song_ids': [58, 125, 136, 160, 197, 199, 236, 299, 306]}
  ```

## 5. Notes / limitations for the downstream collection task

- Pagination: all three library list APIs default to `page_limit=5`, `page_index=0`. Full
  enumeration requires iterating `page_index` until fewer than `page_limit` rows are returned.
- "All playlists" per the question = the user's playlist library. `show_playlist_library` returns
  both public and private playlists owned by the user; each entry exposes `song_ids`, which is
  sufficient to union songs. `is_public` can be passed to filter if needed.
- Albums in `show_album_library` expose `song_ids`, so album songs can be unioned directly from the
  library listing; `show_playlist` gives the per-playlist `songs` list for verification.
- The unique-song count requires a set union over: song-library song_ids, union of album
  `song_ids`, and union of every playlist's `song_ids`.
- The count itself is computed in the collection task (task-2), not here.
