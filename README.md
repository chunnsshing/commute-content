# commute-content

Content files read by personal apps on the morning commute.

## content/louvre/feed.json

The feed for [Louvre](https://github.com/chunnsshing/Louvre), schema v3 (see Louvre's `docs/content-format.md`).
The app matches pictures by `id` and never shows an id twice, so new pictures are appended; old ones can stay.

Every picture is public domain, CC0, or Creative Commons (CC BY / CC BY-SA), with the photographer credited in
`image.credit` and the licence in `image.license`. Rights-managed work appears only as a link card (`image: null`).
Stories are written only from the sources listed in each item's `sources`.
