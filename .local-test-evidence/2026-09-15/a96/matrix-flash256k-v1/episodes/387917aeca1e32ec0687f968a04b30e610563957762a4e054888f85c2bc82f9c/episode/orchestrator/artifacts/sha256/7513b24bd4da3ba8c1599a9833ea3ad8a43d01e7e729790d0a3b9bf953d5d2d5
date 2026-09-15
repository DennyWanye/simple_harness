# SONG_DATA.md — Raw evidence and unique-song computation

## Scope
Task: enumerate, via read-only Spotify calls, (1) every song in the user's **song library**,
(2) every album in the user's **album library** and each album's full track list, and
(3) every **playlist** belonging to the user and each playlist's full track/song list;
then deduplicate by the identifier the API returns and compute per-source and total unique counts.

This report records raw observations only for these calls. All calls below were executed through
`appworld_execute(code)` against the live shared world. No mutations were performed.

## Account / auth (re-verified this attempt, read-only)
- `apis.spotify.login(username='de_ritt@gmail.com', password='7s7!cA8')` →
  `{'access_token': '<JWT>', 'token_type': 'Bearer'}`
- `apis.spotify.show_account(access_token=<token>)` →
  `{'first_name': 'Debra', 'last_name': 'Ritter', 'email': 'de_ritt@gmail.com', 'registered_at': '2022-07-06T10:52:39', 'last_logged_in': '2022-07-06T10:52:39', 'verified': True, 'is_premium': False}`

## Deduplication key
**`song_id`** — every song object returned by `show_song_library` carries a `song_id`; albums carry
`song_ids` (list of song ids); playlists carry `song_ids` (list of song ids) and `show_playlist`
returns `songs:[{id,...}]`. The `id` in `show_playlist.songs` equals the `song_ids` element in the
playlist library row (verified equal for all 5 playlists). Deduplication is therefore a set-union on
the integer song id.

## Method / pagination
`page_limit=20` (max allowed). Each list API was called with `page_index=0,1,...` until a page
returned fewer than `page_limit` rows (complete-enumeration stop rule). Results:

| Collection | API | pages fetched | rows returned | stop reason |
|---|---|---|---|---|
| Song library | `show_song_library` | page_index=0 only (16 rows) | 16 | 16 < 20 → complete |
| Album library | `show_album_library` | page_index=0 only (8 rows) | 8 | 8 < 20 → complete |
| Playlist library | `show_playlist_library` | page_index=0 only (5 rows) | 5 | 5 < 20 → complete |

**No unfetched page and no error was encountered.** Every call returned a `list` (never a failure
dict).

Playlist library completeness cross-check with the `is_public` filter:
- `is_public=True` → 3 playlists `[594, 595, 597]`
- `is_public=False` → 2 playlists `[593, 596]`
- default (`is_public=None`) → 5 playlists `[593, 594, 595, 596, 597]`
Union of the two filtered sets equals the default set, confirming 5 playlists total (no hidden page).

## Raw evidence

### 1. Song library — `show_song_library` (16 rows)
song_ids in order: `[3, 15, 20, 62, 73, 81, 90, 96, 98, 105, 120, 154, 173, 217, 248, 269]`

Raw rows (song_id, title, album_id, added_at):
```
3   The Fragrance of Fading Roses         album_id=1    added_at=2023-01-15T02:56:19
15  In the Depths of Despair              album_id=3    added_at=2022-07-28T02:31:22
20  Between the Depths and Heights        album_id=4    added_at=2022-08-22T21:50:32
62  Crimson Sunset Sonata                 album_id=12   added_at=2023-02-28T21:47:53
73  When Silence Becomes Deafening        album_id=15   added_at=2023-04-03T03:40:07
81  Echoes of the Whispering Wind         album_id=18   added_at=2022-10-19T11:15:03
90  Whispers of Tomorrow                  album_id=None added_at=2023-03-13T11:17:52
96  In the Chambers of My Mind            album_id=None added_at=2022-08-13T20:03:52
98  Chasing Echoes in the Rain            album_id=None added_at=2022-11-21T07:15:47
105 Beyond the Horizon's Reach            album_id=None added_at=2023-02-03T14:09:23
120 Elusive Joy                           album_id=None added_at=2023-03-30T08:56:59
154 The Ghosts of Our Past                album_id=None added_at=2022-07-26T03:43:55
173 Beyond the Echo                       album_id=None added_at=2022-09-22T08:42:43
217 Torn Between Two Worlds               album_id=None added_at=2023-01-23T12:15:50
248 Chasing the Mirage of Happiness       album_id=None added_at=2023-04-03T19:30:12
269 Bittersweet Goodbye                   album_id=None added_at=2023-03-03T20:18:37
```
Unique song ids in song library: **16**.

### 2. Album library — `show_album_library` (8 rows, full track lists)
```
album_id=7  'Vibrant Visions'                    song_ids=[33, 34, 35, 36]
album_id=9  'Mystical Crescendo'                 song_ids=[44, 45, 46]
album_id=10 'Dreamscape Delights'                song_ids=[47, 48, 49, 50, 51, 52, 53]
album_id=11 'Synaptic Serenity'                  song_ids=[54, 55, 56, 57]
album_id=13 'Starlight Serenades'                song_ids=[63, 64, 65, 66]
album_id=15 'Whispers in the Wind'               song_ids=[70, 71, 72, 73]
album_id=16 'Electric Dreamscape'                song_ids=[74, 75, 76]
album_id=18 'Echoes of Eternity'                 song_ids=[80, 81, 82]
```
Total album-song entries: 32. Unique album song ids: **32**.
Union of album song_ids:
`[33,34,35,36,44,45,46,47,48,49,50,51,52,53,54,55,56,57,63,64,65,66,70,71,72,73,74,75,76,80,81,82]`

### 3. Playlist library — `show_playlist_library` (5 rows) + `show_playlist` (per-playlist full song list)
All 5 playlists are owned by `{'name': 'Debra Ritter', 'email': 'de_ritt@gmail.com'}`.
For each, `show_playlist` returned `songs` whose ids exactly match the library row's `song_ids`
(`match: True` for all 5).
```
playlist_id=593 'October Feels: Autumn Aesthetics'        is_public=False  songs=[58,125,136,160,197,199,236,299,306] (9)
playlist_id=594 'Heartbreak Hotel: Songs of Sorrow'       is_public=True   songs=[32,74,147,153,175,200,253] (7)
playlist_id=595 'Velvet Voices: Best of R&B'              is_public=True   songs=[57,66,173,183,215,265,280,319] (8)
playlist_id=596 'Countryside Chronicles: Folk Favorites'  is_public=False  songs=[32,46,89,108,121,128,156,210,256] (9)
playlist_id=597 'Art & Soul: Masterful Melodies'          is_public=True   songs=[57,113,115,128,161,169,224,244,310,323] (10)
```
Total playlist-song entries: 43. Unique playlist song ids: **40** (dupes within playlists:
`32` appears in 594 & 596; `57` in 595 & 597; `128` in 596 & 597).
Union of playlist song_ids:
`[32,46,57,58,66,74,89,108,113,115,121,125,128,136,147,153,156,160,161,169,173,175,183,197,199,200,210,215,224,236,244,253,256,265,280,299,306,310,319,323]`

## Per-source and total counts
| Source | API | rows | unique songs (song_id) |
|---|---|---:|---:|
| Song library | `show_song_library` | 16 | 16 |
| Album library (union of album `song_ids`) | `show_album_library` | 8 albums | 32 |
| All playlists (union of playlist `song_ids`) | `show_playlist_library` (+`show_playlist`) | 5 | 40 |

Cross-source overlaps (by song_id):
- song-library ∩ album-library: `{73, 81}` (2)
- song-library ∩ playlists: `{173}` (1)
- album-library ∩ playlists: `{46, 57, 66, 74}` (4)
- all three: `{}` (0)

Set-union arithmetic:
- |song ∪ album| = 16 + 32 − 2 = 46
- |song ∪ playlist| = 16 + 40 − 1 = 55
- |album ∪ playlist| = 32 + 40 − 4 = 68
- **|song ∪ album ∪ playlist| = 81**

### TOTAL UNIQUE SONGS ACROSS SONG LIBRARY + ALBUM LIBRARY + ALL PLAYLISTS = **81**

Union (sorted song_id):
`[3,15,20,32,33,34,35,36,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,62,63,64,65,66,70,71,72,73,74,75,76,80,81,82,89,90,96,98,105,108,113,115,120,121,125,128,136,147,153,154,156,160,161,169,173,175,183,197,199,200,210,215,217,224,236,244,248,253,256,265,269,280,299,306,310,319,323]`
(count = 81)

## Errors / unfetched pages
- None. No call returned a failure dict; every list terminated before `page_limit` was exhausted
  (no unfetched page remained).

## Limitations / notes
- "All playlists" is interpreted as the user's **playlist library** (`show_playlist_library`), which
  returns only playlists owned by the user. Cross-check `show_liked_playlists` returned 5 playlists
  `[593, 112, 298, 49, 596]`, which includes playlists owned by others (112, 298, 49); these are
  *liked* playlists, not the user's own playlists, and were NOT included.
- `show_liked_songs` (26 rows) and `show_liked_albums` (13 rows) are separate "liked" collections,
  distinct from the "song library" (`show_song_library`, 16) and "album library"
  (`show_album_library`, 8). The question names "song library, albums library and all playlists",
  so those library APIs were used. If the intended meaning were "liked" collections, counts would
  differ — flagged here for transparency.
- Knowledge validity: `knowledge_list` currently returns 0 items, so no external knowledge ID is
  cited as a currently-valid fact for this computation; all figures above come from live
  read-only API output in this episode.
