# Taking your world into single-player

A player on the server can carry their own progress — character, Pals, bases,
items — into local single-player, without the server going down and without
touching anyone else's data. This page is written to be handed to a player;
the operator half is at the bottom.

## For players (Windows / Steam)

1. **In the dashboard**, open the **My account** tab and find *Take your world
   into single-player*. Press **Create my world copy**, wait for it to finish
   (up to a minute), then press **Download**.

   Don't see the card? Your account needs a linked character (ask an admin),
   and it only works when your guild is just you — if you share a guild, a
   moderator has to run the export for you, because a copy of your guild
   includes your guildmates' things.

2. **On your PC**, start Palworld, create a **new world** (any name, any
   settings), enter it once, then quit the game. This makes a fresh save
   folder for the copy to go into.

3. **Extract the downloaded file**: right-click it and choose **Extract All**
   (Windows 11), or open it with [7-Zip](https://www.7-zip.org/) (Windows 10).

4. Press **Win+R**, paste `%LOCALAPPDATA%\Pal\Saved\SaveGames` and press
   Enter. Open the folder with the long number name (that's your Steam ID),
   then sort by date — the **newest** folder is the world you made in step 2.

5. Copy everything you extracted **into** that folder, replacing files when
   asked. Don't delete anything first.

6. Start the game and open that world. It may keep the name from step 2 in
   the menu, but inside it is the server world: your character loads with
   everything they had, and you are the world's host.

**If you load in as a brand-new level-1 character** instead of your own, the
id remap didn't match your install — stop and tell your server admin rather
than playing on.

**What's different from the server:** everything outside your guild is gone by
design (other players' bases simply aren't there), and the world settings are
the server's — you can change them from the world-select screen.

## Why there's a "remap" at all

Palworld identifies your character by an id, and the id your game presents
differs by context: on a dedicated server it comes from your Steam account; in
single-player (or hosting co-op) the game always uses the fixed host id
`00000000-0000-0000-0000-000000000001`. Dropped into single-player unchanged,
your character would be *in* the world but not *yours* — the game would spawn
you a fresh one. The export rewrites every reference to your id to the host id
so the world recognises you.

## For operators

- The self-serve card is on every linked player's **My account** tab. It is
  limited to solo-guild players, throttled per account
  (`SELF_EXPORT_MIN_INTERVAL`, default one hour), runs one at a time, defers
  while the game server is under load, keeps one archive per account
  (swept after `SELF_EXPORT_RETENTION_DAYS`), and audits everything —
  including refused attempts. `SELF_EXPORT_ENABLED=false` removes it
  entirely. See `CONFIGURATION.md`.
- Players who **share a guild** need you: **Save Tools → Export a world copy**
  does the same remap with a *Single-player / co-op host* preset for the
  target id, and lets you choose which guilds the copy keeps. It reads the
  live world and writes a copy, so it is safe while the server runs.
- Moving a player to **another dedicated server** under the same Steam
  account needs no remap at all — their id is the same there. A backup
  download is the right tool for that.
