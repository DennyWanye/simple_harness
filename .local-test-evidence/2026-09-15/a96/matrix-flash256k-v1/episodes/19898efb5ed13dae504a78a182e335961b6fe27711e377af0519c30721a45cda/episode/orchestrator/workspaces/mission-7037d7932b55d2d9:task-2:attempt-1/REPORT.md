# REPORT — Top 4 Most Played R&B Song Titles

Task: mission-7037d7932b55d2d9:task-2
Goal: Query the user's Spotify song, album, and playlist libraries, determine each track's
play count and R&B genre classification, rank the R&B tracks, and write the comma-separated
top 4 R&B song titles to RESULT.md.

## Deliverable
RESULT.md contains:
`Mysteries of the Silent Sea, Crimson Veil, Haunted Memories, Fire and Ice`

## Actions performed (all via appworld_execute; state persisted)
1. Logged into Spotify: `apis.spotify.login(username='glenn.burton@gmail.com',
   password='EbhXe%D')` → returned a Bearer access_token.
2. Enumerated the three libraries (paginated, page_limit=20; all returned short pages, so
   enumeration is complete):
   - `apis.spotify.show_song_library(access_token, page_index, page_limit)` → **19 songs**
   - `apis.spotify.show_album_library(access_token, page_index, page_limit)` → **8 albums**
     (song_ids collected per album: 38 ids)
   - `apis.spotify.show_playlist_library(access_token, page_index, page_limit)` → **4
     playlists** (song_ids collected per playlist: 29 ids)
3. Built the union of song IDs across all three libraries: 19 (song lib) ∪ 38 (albums) ∪ 29
   (playlists) = **79 unique song IDs**.
4. Fetched each candidate: `apis.spotify.show_song(song_id)` (79 calls) to obtain the
   authoritative `genre` and `play_count` per song.
5. Filtered to `genre == 'R&B'` (18 songs) and ranked by `play_count` descending.

## Ranking of R&B songs (play_count desc)
| rank | title | play_count | song_id |
|------|-------|-----------|---------|
| 1 | Mysteries of the Silent Sea | 990 | 185 |
| 2 | Crimson Veil | 972 | 92 |
| 3 | Haunted Memories | 965 | 12 |
| 4 | Fire and Ice | 926 | 233 |
| 5 | Shadows of the Past | 905 | 8 |
| 6 | Beneath the Veil of Illusion | 852 | 13 |
| 7 | When Fate Becomes a Foe | 826 | 9 |
| 8 | Wilted Roses on the Vine | 713 | 241 |
| 9 | An Ode to Forgotten Dreams | 619 | 302 |
| 10 | Memories Etched in Melancholy | 527 | 14 |
| 11 | The Last Waltz of a Broken Heart | 522 | 228 |
| 12 | The Road Less Traveled By | 507 | 232 |
| 13 | Searching for a Lost Horizon | 443 | 303 |
| 14 | Lost in a Moment's Grace | 428 | 11 |
| 15 | The Curse of Loving You | 386 | 10 |
| 16 | In the Depths of Despair | 293 | 15 |
| 17 | Eternal Melancholy | 257 | 87 |
| 18 | Beyond the Horizon's Reach | 231 | 105 |

## Top 4 (final)
1. Mysteries of the Silent Sea
2. Crimson Veil
3. Haunted Memories
4. Fire and Ice

## Method notes / limitations
- R&B classification uses the per-song authoritative `genre` from `apis.spotify.show_song`,
  not the album's genre. Several songs in R&B-genre albums (albums 2 and 3) were indeed R&B,
  but membership in an R&B album was NOT assumed; every song's own genre was checked.
- Only the union of the three library song sets was considered; no external catalog search was
  used.
- Library listings are live state: counts/IDs above are as observed during this task's
  execution. All library pages returned fewer than page_limit rows, indicating complete
  enumeration at observation time.
- Genre string match is exact on `'R&B'` (confirmed against `apis.spotify.show_genres()`).

## Outcome
Completed. RESULT.md written with the comma-separated top 4 R&B song titles.
