# Mixtape Bug Hunt — Submission

## AI Usage

<!-- Fill this in at the end (Milestone 4). Be specific: what you asked,
what the AI helped explain/trace, and at least one place where you had
to verify or correct something the AI said. -->

TODO

---

## Codebase Map

### Main files and their roles

- **`app.py`** — Flask application factory. Initializes SQLAlchemy (`db`),
  registers four blueprints (`songs_bp` at `/songs`, `playlists_bp` at
  `/playlists`, `users_bp` at `/users`, `feed_bp` at `/feed`), and creates
  all tables on startup.
- **`models.py`** — Defines 7 SQLAlchemy models: `User`, `Song`, `Tag`,
  `Playlist`, `Rating`, `Notification`, `ListeningEvent`. Three association
  tables handle many-to-many relationships: `friendships` (symmetric
  user-to-user), `song_tags` (song-to-tag), and `playlist_entries`
  (playlist-to-song, which additionally carries `position`, `added_by`,
  and `added_at` — it's not a plain join table).
- **`routes/`** — Thin layer that parses requests and delegates to
  `services/`. `songs.py` handles search/rate/listen, `playlists.py`
  handles playlist creation and song management, `users.py` handles
  profile/streak/notifications, `feed.py` handles the friends feed.
- **`services/streak_service.py`** — Computes a user's listening streak.
  A streak increments on consecutive calendar days, resets to 1 if a day
  is skipped. Core logic lives in `update_listening_streak()`.
- **`services/feed_service.py`** — Two functions: `get_friends_listening_now()`
  (filters to events within `RECENT_THRESHOLD` = 24h, deduplicated to one
  song per friend) and `get_activity_feed()` (last N events, no recency
  filter).
- **`services/search_service.py`** — Searches songs by title/artist
  (case-insensitive `ilike`), joined with the `song_tags` table to
  include tags in the result.
- **`services/notification_service.py`** — Creates and retrieves
  notifications. `add_to_playlist()` adds a song to a playlist and
  notifies the original sharer. `rate_song()` saves a rating.
- **`services/playlist_service.py`** — Retrieves playlist metadata and
  ordered song lists.

### Data flow — adding a song to a playlist

```mermaid
flowchart LR
    A[Client] --> B["POST /playlists/&lt;id&gt;/songs"]
    B --> C["routes/playlists.py"]
    C --> D["notification_service.add_to_playlist()"]

    D --> E["Load Song, User (adder), Playlist"]
    E --> F{"Song already in\nplaylist.songs?"}
    F -->|No| G["Append song to playlist\ncommit"]
    F -->|Yes| H["Skip append"]
    G --> I{"song.shared_by !=\nadded_by_user_id?"}
    H --> I
    I -->|Yes| J["create_notification()\nnotify original sharer"]
    I -->|No| K["No notification\n(adder is the sharer)"]

    style A fill:#E3F2FD
    style C fill:#FFF8E1
    style D fill:#FFF8E1
    style F fill:#E8F5E9
    style I fill:#E8F5E9
    style J fill:#F3E5F5
    style K fill:#ECEFF1
```

**Summary:** Adding a song to a playlist always appends it to `playlist.songs`
if it isn't already there, but a notification is only created for the
song's original sharer — and only if the person adding it isn't that same
sharer. This is the working notification path referenced in Issue #4,
which is useful to compare against the missing `rate_song()` notification.

### Pattern noticed

Every route delegates immediately to a function in `services/`; routes
only handle request parsing and response formatting. Business logic and
DB queries live entirely in the service layer, which is also where all
five known bugs live.

---

## Root Cause Analysis

### Issue #1: My listening streak keeps resetting

**How I reproduced it:**

Using `flask shell`, I called `update_listening_streak()` directly with
controlled `datetime` values to simulate 3 consecutive days of listening
(Saturday July 4 -> Sunday July 5 -> Monday July 6, all UTC):

- Day 1 (Saturday, `weekday()`=5): streak went 0 -> 1 (expected — first
  listening event).
- Day 2 (Sunday, `weekday()`=6): streak stayed at 1 instead of
  incrementing to 2.
- Day 3 (Monday, `weekday()`=0): streak went to 2 instead of 3, because
  Day 2 had incorrectly reset instead of incrementing.

This confirms the streak breaks specifically when the day being recorded
is a Sunday, even though the user listened on consecutive calendar days.

**How I found the root cause:**

I traced `update_listening_streak()` in `streak_service.py` line by line
using the values printed during reproduction. With `days_since_last == 1`
between Saturday and Sunday, I checked the `elif` condition manually:
`today.weekday() != 6` evaluates to `6 != 6`, which is `False`, since
Python's `date.weekday()` returns 6 for Sunday. Because the `elif`
condition as a whole (`True and False`) is `False`, execution falls
through to the `else` branch, which resets the streak to 1 instead of
incrementing it. This confirmed the exact line responsible, rather than
just "somewhere in the streak logic."

**The root cause:**

The `elif` condition `days_since_last == 1 and today.weekday() != 6` was
intended to increment the streak on any consecutive day, but the added
`today.weekday() != 6` clause excludes Sundays specifically — Python's
`date.weekday()` returns 6 for Sunday. So whenever a user's *current*
listening event falls on a Sunday (regardless of whether the previous
day was covered), the condition evaluates to `False` and execution falls
into the `else` branch, which unconditionally resets the streak to 1.
This is why a user listening on consecutive days, where one of those
days happens to be a Sunday, sees their streak reset instead of
increment.

**My fix and side-effect check:**

**My fix and side-effect check:**

I removed the `and today.weekday() != 6` clause from the `elif` condition
in `update_listening_streak()`, so it now reads simply
`elif days_since_last == 1:`. This clause had no basis in the documented
streak rules and was the sole cause of the Sunday reset.

I re-ran the original reproduction (Saturday -> Sunday -> Monday) and
confirmed the streak now increments correctly: 1 -> 2 -> 3, instead of
resetting on Sunday.

To check I hadn't broken the other two documented rules, I tested both
remaining cases directly via `update_listening_streak()`:
- Two listens on the same calendar day: streak stayed at 5 (unchanged),
  confirming the `days_since_last == 0` branch still works correctly.
- A 3-day gap since the last listen: streak reset to 1, confirming the
  `else` branch (skipped day handling) is unaffected.

Both cases behaved as expected, so the fix only changes behavior for the
specific Sunday case it was meant to address.

---

### Issue #2: Friends Listening Now shows people from yesterday

**How I reproduced it:**

I queried `get_friends_listening_now()` directly in `flask shell` for
each seeded user as viewer, and computed the actual age of each returned
event. For example, viewing as `kenji`, the feed included `nova` with an
event 3.6 hours old. Since "Listening Now" implies the friend is
currently listening, an event several hours old being shown as "now" is
the reported bug. I confirmed this wasn't a one-off by checking multiple
viewers (`darius`, `simone`, `aaliya`) — all consistently showed friends
with events 1.8–3.6 hours old as "listening now."

**How I found the root cause:**

I first suspected a timezone/string-comparison issue between the naive
datetimes stored in SQLite and the timezone-aware `cutoff` value used in
the filter, since SQLite stores datetimes as text and compares them as
strings. I compiled the actual SQL query with literal binds and found
the stored values and the `cutoff` value did have different string
formats (the cutoff included a `+00:00` suffix, stored rows did not).
However, testing this directly — including edge cases at 23:59 and
24:01 hours — showed the filter behaved correctly regardless, so the
format mismatch was a red herring and not the actual cause.

Re-reading `seed_data.py`, a comment on the older listening events read
"should NOT appear in 'listening now' after fix" — which pointed me back
to `RECENT_THRESHOLD` itself rather than the comparison logic. I
confirmed this by temporarily overriding `RECENT_THRESHOLD` to 30 minutes
in the shell and re-querying: friends with events older than 30 minutes
(like `nova` at 3.6h) disappeared from the results, confirming the
constant's value — not the filtering logic — was the problem.

**The root cause:**

`RECENT_THRESHOLD = timedelta(hours=24)` in `feed_service.py` is far too
wide a window for a feature meant to show who is listening to music
*right now*. Any friend who listened at any point in the last 24 hours —
including late the previous evening — is shown as currently listening,
which is exactly the "shows people from yesterday" behavior reported.
The comparison logic itself (`listened_at >= cutoff`) was correct; the
threshold value it was being compared against was set too high for the
feature's intent.

**My fix and side-effect check:**

I changed `RECENT_THRESHOLD` from `timedelta(hours=24)` to
`timedelta(minutes=30)`. After the fix, re-running the same reproduction
across all 5 seeded users returned an empty feed for everyone, since all
seeded listening events were now older than 30 minutes — confirming
stale events no longer appear. To verify the feed still works for
genuinely recent activity, I created a new `ListeningEvent` timestamped
at the current moment for `nova`, a friend of `kenji`, and re-queried:
`nova` correctly appeared in `kenji`'s "listening now" feed. I also
checked `get_activity_feed()`, which does not use `RECENT_THRESHOLD` at
all, so this change has no effect on the general activity feed.

---

### Issue #3: The same song keeps showing up twice in search

**How I reproduced it:**

TODO — fill in after running the `flask shell` steps for search_service.

**How I found the root cause:**

TODO

**The root cause:**

TODO

**My fix and side-effect check:**

TODO