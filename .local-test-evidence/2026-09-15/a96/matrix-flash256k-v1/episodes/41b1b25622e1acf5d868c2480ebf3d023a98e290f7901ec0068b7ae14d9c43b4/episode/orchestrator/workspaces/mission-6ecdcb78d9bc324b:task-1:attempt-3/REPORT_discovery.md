# Task A — Discovery Report (Spotify APIs & Accounts)

Task: `mission-6ecdcb78d9bc324b:task-1` — Discover the simulated user's accounts and the
Spotify public APIs needed to find the top-4 most-played R&B songs across the song, album
and playlist libraries. **No final top-4 answer is computed in this task.**

All observations below were produced by live calls through `appworld_execute` (the shared
persistent world). They are only evidence for the scope stated.

---

## 1. Simulated user / accounts

- `apis.supervisor.show_profile()` returned:
  - first_name = `Glenn`, last_name = `Burton`
  - email = `glenn.burton@gmail.com`
  - phone_number = `8638518861`, birthday = `1993-08-13`, sex = `male`
- `apis.supervisor.show_account_passwords()` returned 9 app accounts:
  amazon, file_system, gmail, phone, simple_note, splitwise, **spotify**, todoist, venmo.
  - Relevant Spotify credential: account_name = `spotify`, password = `EbhXe%D`.
- `apis.spotify.login(username='glenn.burton@gmail.com', password='EbhXe%D')` succeeded and
  returned a bearer `access_token` (token_type = Bearer). All library calls below used that token.
- `apis.spotify.show_account(access_token=...)` returned:
  - first_name = Glenn, last_name = Burton, email = glenn.burton@gmail.com,
    registered_at = 2022-04-03T10:27:47, last_logged_in = 2022-04-03T10:27:47,
    verified = true, is_premium = false.

Login flow: `apis.spotify.login(username=<email>, password=<password>)` -> `access_token`;
every library/list endpoint requires `access_token`.

---

## 2. Spotify public API surface (relevant subset)

Discovered via `apis.api_docs.show_api_descriptions(app_name='spotify')`.
The three library endpoints and their song/play-count exposure:

| API | path | method | required params | song title? | play_count? | genre? |
|---|---|---|---|---|---|---|
| `show_song_library` | `/library/songs` | GET | `access_token` | YES (`title`) | **NO** | **NO** |
| `show_album_library` | `/library/albums` | GET | `access_token` | only via `song_ids` (no titles) | **NO** | YES (`genre`) |
| `show_playlist_library` | `/library/playlists` | GET | `access_token` | only via `song_ids` (no titles) | **NO** | **NO** |
| `show_song` | `/songs/{song_id}` | GET | `song_id` | YES (`title`) | **YES (`play_count`)** | YES (`genre`) |

### Signature details

- `show_song_library(access_token, page_index=0, page_limit=5)`
  - page_index >= 0; page_limit 1..20 (default 5).
  - success fields per song: `song_id`, `title`, `album_id`, `duration`, `artists`
    (`id`,`name`), `added_at`.
- `show_album_library(access_token, page_index=0, page_limit=5)`
  - success fields per album: `album_id`, `title`, `genre`, `artists`, `rating`,
    `like_count`, `review_count`, `release_date`, `song_ids`, `added_at`.
- `show_playlist_library(access_token, is_public=None, page_index=0, page_limit=5)`
  - `is_public` optional boolean; success fields per playlist: `playlist_id`, `title`,
    `is_public`, `rating`, `like_count`, `review_count`, `owner`(`name`,`email`),
    `created_at`, `song_ids`.
- `show_song(song_id)` — no access_token required on this call.
  - success fields: `song_id`, `title`, `album_id`, `album_title`, `duration`, `artists`,
    `release_date`, `genre`, **`play_count`**, `rating`, `like_count`, `review_count`,
    `shareable_link`.

### Supporting / expansion endpoints

- `show_playlist(playlist_id, access_token)` -> includes `songs`: [{`id`,`title`,`artist_ids`}].
- `show_album(album_id)` -> includes `songs`: [{`id`,`title`,`artist_ids`}].
- `show_genres()` -> full genre list (for the R&B filter).
- `show_song_privates(song_id, access_token)` -> {`liked`,`reviewed`,`in_song_library`,`downloaded`}.
- `show_liked_songs(access_token, page_index, page_limit, sort_by)` and
  `show_liked_albums(...)` also relate to the library surface but are the *liked* lists, not
  the song/album/playlist libraries.

**Key conclusion:** `play_count` is exposed ONLY by `show_song`. The list/library endpoints
return song ids and titles but no play counts, so the ranking requires expanding the union of
`song_id`s from the three libraries and calling `show_song(song_id)` per song to read
`play_count` and `genre` (filter `genre == "R&B"`).

---

## 3. Observed sample results (live calls)

- `show_song_library(page_index=0, page_limit=5)` returned 5 songs, e.g.
  song_id=33 title=`Unveiled`, album_id=7, artist Eliana Harper, added_at 2022-09-16T15:36:56;
  song_id=44 `The Illusion of Eternal Spring`; song_id=56 `Distant Love`; song_id=92
  `Crimson Veil`; song_id=109 `The Weight of a Thousand Stars`. No play_count field present.
- `show_album_library(page_index=0, page_limit=5)` returned albums with genre and song_ids, e.g.
  album_id=2 `Celestial Harmonies` genre=`R&B` song_ids=[8,9,10];
  album_id=3 `Nocturnal Melodies` genre=`R&B` song_ids=[11,12,13,14,15];
  album_id=8 `Velvet Underground` genre=`jazz`; album_id=10 `Dreamscape Delights` genre=`jazz`;
  album_id=11 `Synaptic Serenity` genre=`EDM`. No play_count field.
- `show_playlist_library(page_index=0, page_limit=5)` returned 4 playlists owned by Glenn Burton,
  e.g. playlist_id=621 `Heartbreak Hotel: Songs of Sorrow` (public) song_ids=[76,99,111,273,323];
  playlist_id=622 `Classical Cornerstones`; playlist_id=623 `Groove Galaxy: Funk & Soul` (private);
  playlist_id=624 `Retro Rewind: 80's & 90's Mix` (private). No play_count field.
- `show_song(song_id=33)` returned genre=`hip-hop`, **play_count=`809`**, rating=2.0,
  like_count=12, review_count=2, release_date=2021-02-05T04:10:20. This confirms `play_count`
  is available here.
- `show_playlist(621, token)` returned `songs` with titles, e.g. {id=76 `Whispers of the Heart`},
  {id=99 `The Distance Between Two Hearts`}, {id=111 `Lonesome Road`}, {id=273 `Endless Drift`},
  {id=323 `Innocent Lies`} — titles but no play_count.
- `show_album(2)` returned `songs` with titles: {id=8 `Shadows of the Past`},
  {id=9 `When Fate Becomes a Foe`}, {id=10 `The Curse of Loving You`}.
- `show_song_privates(33, token)` returned `{liked:false, reviewed:false, in_song_library:true, downloaded:true}`.
- `show_genres()` returned: `["EDM","R&B","indie","hip-hop","jazz","rock","pop","classical","reggae","country"]`.
  The R&B genre string is exactly `"R&B"`.

---

## 4. Limitations / gotchas observed

- **`play_count` is not in any library listing.** It must be read per song via `show_song`.
- `show_liked_songs` documents `sort_by` accepting `liked_at, play_count, title`, but a live call
  with `sort_by='-play_count'` was REJECTED with HTTP 422:
  `"If sort_by is passed, it must be one of liked_at, title and must be prefixed with + or -."`
  So `play_count` sorting is NOT actually usable on `show_liked_songs` (documentation drift).
  Ranking must be computed client-side from `show_song.play_count`.
- `show_song_library` schema lists `album_title`, but the observed live rows did not include it.
- Pagination: `page_index` (>=0) and `page_limit` (1..20, default 5) on all three list endpoints;
  iterate pages until an empty/short page to enumerate the full library.
- `show_song` and `show_album` do not need `access_token`; library endpoints do.
- Genre for a song comes from `show_song.genre` (e.g. song 33 is `hip-hop`), while album-level
  genre comes from `show_album_library.genre`. Filter R&B songs using each song's own
  `show_song.genre` to be safe.

---

## 5. Handoff for Task B

To rank the top-4 most-played R&B song titles across the song, album and playlist libraries:
1. Login: `apis.spotify.login(username='glenn.burton@gmail.com', password='EbhXe%D')`.
2. Enumerate `show_song_library` -> song_ids directly.
3. Enumerate `show_album_library` -> collect `song_ids` per album.
4. Enumerate `show_playlist_library` (**both public and private**, i.e. call with/without
   `is_public`) -> collect `song_ids` per playlist.
5. Union all song_ids, dedupe, call `show_song(song_id)` for each.
6. Keep songs with `genre == "R&B"`, rank by `play_count` descending, take top 4 `title`s.

No final ranking is produced here (Task A scope).

---

## 6. Attempt-2 independent verification (retry_of attempt-1)

This attempt re-ran the discovery from scratch on the same shared world and independently
re-confirmed the key claims listed above (so the report is not merely carried over):

- `apis.spotify.login(username='glenn.burton@gmail.com', password='EbhXe%D')` -> succeeded,
  bearer `access_token` returned.
- `apis.spotify.show_account(access_token=...)` -> re-confirmed
  `{first_name: Glenn, last_name: Burton, email: glenn.burton@gmail.com,
    registered_at: 2022-04-03T10:27:47, last_logged_in: 2022-04-03T10:27:47,
    verified: true, is_premium: false}`.
- `show_song_library` / `show_album_library` / `show_playlist_library` (page 0) -> re-confirmed
  the sample rows and, importantly, that **none** of them expose `play_count`.
- `show_song(33)=play_count 809 genre hip-hop`, `show_song(8)=play_count 905 genre R&B`,
  `show_song(56)=play_count 334 genre EDM` -> re-confirmed `play_count` + `genre` are exposed here.
- `show_playlist(621)` and `show_album(2)` -> re-confirmed song `title`s are exposed but no `play_count`.
- `show_song_privates(33)` -> re-confirmed `{liked:false, reviewed:false, in_song_library:true, downloaded:true}`.
- `show_liked_songs(sort_by='-play_count')` -> re-confirmed HTTP 422 rejection.
- `show_genres()` -> re-confirmed the exact R&B string `"R&B"`.

No final top-4 answer was computed in this task.

---

## 7. Attempt-3 independent verification (retry_of attempt-2)

This attempt re-ran the whole discovery from scratch on the same shared world and again
independently confirmed the report (not merely carried over). Observed this attempt:

- `apis.supervisor.show_profile()` -> `Glenn Burton / glenn.burton@gmail.com / 8638518861`.
- `apis.supervisor.show_account_passwords()` -> spotify account password `EbhXe%D`
  (among amazon, file_system, gmail, phone, simple_note, splitwise, todoist, venmo).
- `apis.spotify.login(username='glenn.burton@gmail.com', password='EbhXe%D')` -> succeeded,
  returned bearer `access_token` (`token_type=Bearer`); `show_account` gave
  `{Glenn Burton, glenn.burton@gmail.com, registered_at 2022-04-03T10:27:47, is_premium false}`.
- Full-library enumeration via pagination (`page_index` 0.. , `page_limit=20`):
  **song library = 19 songs, album library = 8 albums, playlist library = 2 public + 2 private
  playlists**. Album genres sampled: `['R&B','R&B','jazz','jazz','EDM','rock','EDM','rock']`.
- Page-0 samples of all three libraries re-confirmed the exact rows in section 3 and confirmed
  **no `play_count` field** on any of `show_song_library` / `show_album_library` /
  `show_playlist_library` (song rows for ids 33,44,56,92,109 etc.).
- `show_song(8)=play_count 905 genre R&B`, `show_song(11)=play_count 428 genre R&B`,
  `show_song(33)=play_count 809 genre hip-hop`, `show_song(56)=play_count 334 genre EDM`
  -> `play_count` + `genre` live only on `show_song`.
- `show_album(2)` -> `songs` titles `Shadows of the Past` / `When Fate Becomes a Foe` /
  `The Curse of Loving You`, no `play_count`.
- `show_playlist(621, token)` -> `songs` titles `Whispers of the Heart` (76),
  `The Distance Between Two Hearts` (99), `Lonesome Road` (111), `Endless Drift` (273) ...
  no `play_count`.
- `show_song_privates(33, token)` -> `{liked:false, reviewed:false, in_song_library:true, downloaded:true}`.
- `show_genres()` -> `["EDM","R&B","indie","hip-hop","jazz","rock","pop","classical","reggae","country"]`
  (R&B string is exactly `"R&B"`).
- `show_liked_songs(sort_by='-play_count')` -> REJECTED (HTTP 422:
  `"If sort_by is passed, it must be one of liked_at, title and must be prefixed with + or -."`),
  confirming `play_count` cannot be sorted through `show_liked_songs`.

Conclusion unchanged: `play_count` is only retrievable per song via `show_song`, so Task B must
union the `song_ids` from all three libraries, call `show_song` for each, filter
`genre == "R&B"`, and rank by `play_count` descending. No final top-4 answer is computed here.
