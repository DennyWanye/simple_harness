# R&B Ranking — Task B (mission-2c3916d5020304d8:task-2)

Goal: pull the user's **song, album and playlist libraries** from Spotify, resolve every
candidate song's `genre` and `play_count`, dedupe across the three libraries by `song_id`,
filter to genre `"R&B"`, sort by `play_count` descending, and report the **top 4 most played
R&B song titles**.

All data below came from live `appworld_execute` calls in the shared AppWorld world
(Glenn Burton / glenn.burton@gmail.com). No values were guessed.

---

## 1. Authentication (input + output)

Call: `apis.spotify.login(username="glenn.burton@gmail.com", password="EbhXe%D")`
Output: `{"access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...", "token_type": "Bearer"}`
(157-char JWT). Token held in shell variable `token`.

## 2. Library enumeration (paginated walks, page_limit=20)

Helper `walk(fn, **kw)` fetched page_index=0,1,... until an empty page or a short page.

- Command: `apis.spotify.show_song_library(access_token=token)`
  → **19 songs**, ids `[33,44,56,92,109,114,135,137,156,159,202,225,226,241,265,302,303,308,324]`
- Command: `apis.spotify.show_album_library(access_token=token)`
  → **8 albums**, ids `[2,3,8,10,11,12,13,14]`
- Command: `apis.spotify.show_playlist_library(access_token=token)` (no is_public → mixed)
  → **4 playlists**, ids `[621,622,623,624]`
- Cross-check `is_public=True` → `[621,622]`; `is_public=False` → `[623,624]`.
  Union = all 4 playlists, so no playlist is missed.

### Album → song_ids (from library rows AND cross-checked with `show_album`)
| album_id | title | album genre | song_ids |
|---|---|---|---|
| 2 | Celestial Harmonies | R&B | 8,9,10 |
| 3 | Nocturnal Melodies | R&B | 11,12,13,14,15 |
| 8 | Velvet Underground | jazz | 37,38,39,40,41,42,43 |
| 10 | Dreamscape Delights | jazz | 47,48,49,50,51,52,53 |
| 11 | Synaptic Serenity | EDM | 54,55,56,57 |
| 12 | Astral Journey | rock | 58,59,60,61,62 |
| 13 | Starlight Serenades | EDM | 63,64,65,66 |
| 14 | Midnight Serenade | rock | 67,68,69 |

### Playlist → song_ids (from library rows AND cross-checked with `show_playlist`)
| playlist_id | title | is_public | song_ids |
|---|---|---|---|
| 621 | Heartbreak Hotel: Songs of Sorrow | True | 76,99,111,273,323 |
| 622 | Classical Cornerstones | True | 14,64,80,105,145,154,175,185,228,284 |
| 623 | Groove Galaxy: Funk & Soul | False | 39,52,70,120,135,232 |
| 624 | Retro Rewind: 80's & 90's Mix | False | 40,87,136,165,186,233,290,292 |

`show_album`/`show_playlist` `songs[].id` matched the library-row `song_ids` exactly
(no discrepancy).

## 3. Dedupe / union logic

`union = sorted(set(song_library_ids) | set(all album song_ids) | set(all playlist song_ids))`
→ **79 unique song ids**:
```
[8,9,10,11,12,13,14,15,33,37,38,39,40,41,42,43,44,47,48,49,50,51,52,53,54,55,56,57,58,59,
 60,61,62,63,64,65,66,67,68,69,70,76,80,87,92,99,105,109,111,114,120,135,136,137,145,154,
 156,159,165,175,185,186,202,225,226,228,232,233,241,265,273,284,290,292,302,303,308,323,324]
```
Dedupe is by `song_id` (the same song in multiple libraries has the same id). Every one of
the 79 ids was resolved with `apis.spotify.show_song(song_id=...)` (79/79 succeeded).
No two distinct song ids share a title in this union (duplicate-title check: `{}`), so the
song_id dedupe is unambiguous.

## 4. Genre + play_count source

Per-song `genre` and `play_count` come from `apis.spotify.show_song(song_id)` (the library
listings omit them). Example raw outputs:
- `show_song(185)` → title "Mysteries of the Silent Sea", genre "R&B", play_count 990
- `show_song(92)`  → title "Crimson Veil", genre "R&B", play_count 972
- `show_song(12)`  → title "Haunted Memories", genre "R&B", play_count 965
- `show_song(233)` → title "Fire and Ice", genre "R&B", play_count 926

`spotify.show_genres()` vocabulary includes the exact label `"R&B"` (uppercase, ampersand),
so the genre filter `== "R&B"` is exact-match valid.

## 5. R&B subset (18 songs) sorted by play_count DESC

| rank | song_id | title | play_count |
|---|---|---|---|
| 1 | 185 | Mysteries of the Silent Sea | 990 |
| 2 | 92 | Crimson Veil | 972 |
| 3 | 12 | Haunted Memories | 965 |
| **4** | **233** | **Fire and Ice** | **926** |
| 5 | 8 | Shadows of the Past | 905 |
| 6 | 13 | Beneath the Veil of Illusion | 852 |
| 7 | 9 | When Fate Becomes a Foe | 826 |
| 8 | 241 | Wilted Roses on the Vine | 713 |
| 9 | 302 | An Ode to Forgotten Dreams | 619 |
| 10 | 14 | Memories Etched in Melancholy | 527 |
| 11 | 228 | The Last Waltz of a Broken Heart | 522 |
| 12 | 232 | The Road Less Traveled By | 507 |
| 13 | 303 | Searching for a Lost Horizon | 443 |
| 14 | 11 | Lost in a Moment's Grace | 428 |
| 15 | 10 | The Curse of Loving You | 386 |
| 16 | 15 | In the Depths of Despair | 293 |
| 17 | 87 | Eternal Melancholy | 257 |
| 18 | 105 | Beyond the Horizon's Reach | 231 |

Boundary check: 4th (926) > 5th (905) — no tie at the cutoff, so the top-4 set is unambiguous.

## 6. FINAL ANSWER — Top 4 most played R&B song titles (comma-separated)

```
Mysteries of the Silent Sea, Crimson Veil, Haunted Memories, Fire and Ice
```

Provenance of the top 4 (which library each was found in):
- `Mysteries of the Silent Sea` (id 185) — playlist library only (playlist 622)
- `Crimson Veil` (id 92) — song library only
- `Haunted Memories` (id 12) — album library only (album 3, "Nocturnal Melodies")
- `Fire and Ice` (id 233) — playlist library only (playlist 624)
(Each top-4 song was reachable from exactly one of the three libraries, which is why all
three libraries had to be unioned.)

## 7. Caveats / ambiguities

- **Song-level genre is authoritative.** Album genre (e.g. album 2/3 = "R&B") was only a
  corroboration; playlist songs mix genres, and album membership is not exhaustive (some
  song-library rows have `album_id: null`). The R&B filter used `show_song.genre == "R&B"`.
- **play_count units**: returned as integer values; all distinct, so ranking is strict.
- **No missing data**: all 79 union songs resolved successfully (0 errors); 18 of them are R&B.
- **is_public coverage**: playlist library default returned a public+private mix, and the
  explicit `is_public=True/False` walks together equal the same 4 playlists — no playlist
  omitted.
- Ties: none among the top 5; if the user's "most played" concept ever considered album-level
  duplicates, no such duplicates exist here (unique titles across the union).
