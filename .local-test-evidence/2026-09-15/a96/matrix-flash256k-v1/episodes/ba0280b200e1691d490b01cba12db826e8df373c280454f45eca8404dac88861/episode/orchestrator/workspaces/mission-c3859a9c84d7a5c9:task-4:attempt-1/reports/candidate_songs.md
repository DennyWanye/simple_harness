# Candidate Songs Report — Lily Moon songs played over 980 times

Task: `mission-c3859a9c84d7a5c9:task-2` (read-only candidate discovery — NO queue mutations performed)

Goal: Query Lily Moon's songs and their play counts in the simulated Spotify world; identify every song whose play count is **greater than 980**. Do not enqueue anything yet.

All observations below were produced by running `appworld_execute(code)` against the shared AppWorld shell. The `apis` module is pre-injected into the shell (no `import` needed). Strings in quotes are verbatim API outputs.

## 1. Resolution of "Lily Moon"

`apis.spotify.search_artists(query='Lily Moon', page_limit=20)` returned as its top relevance match:

```json
{"artist_id": 34, "name": "Lily Moon", "genre": "rock", "follower_count": 25, "created_at": "2018-12-04T01:37:41"}
```

Lily Moon = **artist_id 34**.

## 2. Source API calls used

- `apis.spotify.login(username='susanmiller@gmail.com', password='%CCvl8v')` — authenticated session, returned `token_type: Bearer`. The Spotify password came from `apis.supervisor.show_account_passwords()`.
- `apis.spotify.search_artists(query='Lily Moon', page_limit=20)` — resolve artist → id 34.
- `apis.spotify.search_songs(artist_id=34, page_limit=20)` and `page_index=1` — full listing + pagination completeness check.
- `apis.spotify.search_songs(artist_id=34, sort_by='-play_count', page_limit=20)` — cross-check ordering.
- `apis.spotify.search_songs(artist_id=34, min_play_count=981, page_limit=20)` and `sort_by='-play_count'` — apply the `play_count > 980` filter.
- `apis.spotify.show_song(song_id=<id>)` — per-song confirmation of `play_count` for all 11 songs.

`search_songs` endpoint: `GET /songs`; relevant parameters include `query`, `artist_id`, `min_play_count`, `max_play_count`, `sort_by`, `page_index`, `page_limit` (1–20). Each result includes `song_id`, `title`, `artists[]`, `play_count`.

## 3. Complete Lily Moon song listing with play counts

From `apis.spotify.search_songs(artist_id=34, page_limit=20)` (11 songs total; page_index 1 returned 0 rows, so the listing is complete). Play counts were independently confirmed with `apis.spotify.show_song(song_id=<id>)`:

| song_id | title | play_count | artists |
|--------:|-------|-----------:|---------|
| 67 | The Echoes of a Silent Heart | 916 | Lily Moon, Zoey James |
| 68 | Lost in the Wilderness of Love | 156 | Lily Moon, Zoey James |
| 69 | Whispers of a Forgotten Love | 645 | Lily Moon, Zoey James |
| 74 | On the Border of Reality | 846 | Lily Moon, Zoey James |
| 75 | Walking Through the Valley of Shadows | 806 | Lily Moon, Zoey James |
| 76 | Whispers of the Heart | 330 | Lily Moon, Zoey James |
| 309 | Final Act | 520 | Lily Moon |
| 310 | Harmony of the Distant Stars | 715 | Lily Moon |
| 311 | Infinite Dreams | 990 | Lily Moon |
| 312 | Eternal Tears | 562 | Lily Moon |
| 313 | Mystical Dreamscape | 864 | Lily Moon |

## 4. Qualification rule

"played over 980 times" ⇒ `play_count > 980` (strictly greater; 980 itself does not qualify). The equivalent Spotify filter is `min_play_count = 981`.

## 5. Qualifying list (`play_count > 980`)

`apis.spotify.search_songs(artist_id=34, min_play_count=981, page_limit=20)` returned exactly **one** song:

```json
{
  "song_id": 311,
  "title": "Infinite Dreams",
  "album_id": null,
  "duration": 258,
  "artists": [{"id": 34, "name": "Lily Moon"}],
  "release_date": "2020-07-26T22:25:50",
  "genre": "rock",
  "play_count": 990,
  "rating": 0.0,
  "like_count": 4,
  "review_count": 0,
  "shareable_link": "https://spotify.com/songs/311"
}
```

**Qualifying songs:**

1. `song_id = 311` — **"Infinite Dreams"** — `play_count = 990` — artist: Lily Moon (id 34)

No other Lily Moon song exceeds 980 (highest non-qualifying: song 67 "The Echoes of a Silent Heart" at 916). This is the authoritative candidate set for the subsequent enqueue task.

## 6. Queue mutation status

No song was added to, removed from, or otherwise modified in the Spotify player queue during this task. This report is read-only candidate discovery; enqueueing is deferred to a later task.

## 7. Caveats

- `play_count` is read at query time; a later re-read may differ. This snapshot was taken during this task against the current world.
- The `artist_id=34` filter also matched collaborative tracks where Lily Moon is a co-artist (songs 67–76, shared with Zoey James). Those were included in the scan; none qualify.
- A free-text query (`search_songs(query='Lily Moon')`) returns songs from other artists whose titles contain "Moon" / "Lily"; therefore the authoritative identification uses the `artist_id=34` filter, not free text.
- Listing completeness was verified via pagination (`page_index` 0 → 11 rows; `page_index` 1 → 0 rows).
- Two initial shell calls in this attempt (`import apis`, `import sys`) failed with "Usage of the following module is not allowed"; thereafter `apis` was used directly as a pre-injected global, which succeeded.
