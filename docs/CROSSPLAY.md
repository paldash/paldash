# Non-Steam players — what is known, and what is not

Palworld supports crossplay, so a dedicated server can hold Xbox, PS5 and Mac
players alongside Steam ones. **This project has only ever been run against Steam
accounts**, and this file records exactly where that matters so nobody has to
rediscover it.

## What is verified

**The save format carries the platform.** Every player `.sav` has
`SaveData.PlayerPlatform`, an EnumProperty. The game's own values, read out of
`PalServer-Linux-Shipping` rather than guessed:

    EPalPlayerPlatform::Steam
    EPalPlayerPlatform::Xbox
    EPalPlayerPlatform::PS5
    EPalPlayerPlatform::Mac
    EPalPlayerPlatform::None

All five players on the reference world are `Steam`. `parser.py` now surfaces this
as `platform` on every player, so the moment a console player appears it is visible
rather than something to infer from a uid that looks unusual.

**Every Steam uid observed has the same shape**: a Steam ID32 in the first eight hex
digits, zeros after — `11a11a01-0000-0000-0000-000000000000`. That is 5 of 5 on the
reference world.

## What the community documents (checked 2026-08-16)

Nothing online states the console uid byte format either — the unknowns below
survive a search, not just our own reading. What the hosting guides and admin
docs do agree on:

- **A Steam player's uid is the low 32 bits of their SteamID64** (cheahjs'
  converter and several hosting tools implement exactly this), which confirms
  the Steam-ID32-then-zeros shape this project observed independently.
- **Admin actions on console players key on the in-game UID**, never a Steam
  ID — the game's own player list is the authority. That is already how this
  dashboard works: kick/ban travel by uid.
- **Console players only reach a server through the community-server list**:
  the server needs `CrossplayPlatforms=(Steam,Xbox,PS5,Mac)` in
  `PalWorldSettings.ini` plus the `-publiclobby` launch flag. Note the game's
  own `DefaultPalWorldSettings.ini` ships all four platforms enabled, and the
  key is one of the ones Pocketpair's settings docs do not describe — it shows
  in the Settings tab's "other settings" with no tooltip.
- Game Pass **save files** are CNK-wrapped and need conversion before a
  dedicated server can read them — a storage detail for imports, not an
  identity one.

## What is unknown

**What a non-Steam `PlayerUId` looks like.** It could be the platform's own account
id padded the same way, a hash, or a full-entropy GUID. Nothing in the save, the
server binary, or either reference implementation says.

**Neither reference project handles it.** `PalWorldSaveTools` has no
`PlayerPlatform` handling anywhere — its only Xbox code locates Game Pass *save
files* on disk, which is a storage question, not an identity one. The original
palworld-server-dashboard has none either. So there is no prior art to copy, and
this is not an oversight anybody else has already solved.

**Whether the game's REST API reports console uids in the same format** as the save
does. The live map, kick and ban all join REST `userId` against save uids, so a
format difference would break those joins specifically — while save parsing kept
working, which is the confusing failure mode.

## Why the dashboard should work anyway

Everything treats a uid as an **opaque string**, and the two places that could have
baked in the Steam shape do not:

- `privacy.normalise_uid` strips dashes and lowercases. No shape assumption.
- `savefiles._player_index` normalises filenames the same way. No shape assumption.
- `soloexport._fmt_uid` requires 32 hex characters — which any GUID satisfies.
- `soloexport`'s value-based remap rests on two ids being *equal*, not on their
  shape. (An earlier version of that comment argued from the Steam shape; it was
  corrected when this was written, because the reasoning would not have generalised
  even though the code does.)

`accounts.steam_uid` is a misleading **column name** — it stores whatever uid links
an account to a character, on any platform. Renaming it means a migration for no
behavioural gain, so it stays and is noted here instead.

## If you get a console player

Three things to check, in order:

1. `GET /api/save/players` — does the new player appear, and what is their
   `platform` and `uid`?
2. Does that `uid` match what the game's REST `/v1/api/players` reports for them?
   If not, the live map and moderation will not find them, and
   `privacy.normalise_uid` is where the join happens.
3. Does `Players/<UID>.sav` follow the uppercase-undashed filename convention?
   `savefiles._player_index` assumes it.

If all three hold, nothing needs changing. If any fails, this file is where the
finding belongs.
