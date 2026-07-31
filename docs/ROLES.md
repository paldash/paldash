# Roles and permissions

Who can do what, and where each rule actually lives.

`backend/roles.py` is the authority. `src/lib/permissions.ts` mirrors the names
so the UI can hide tabs, and the Next.js proxy uses it as a route allowlist — but
**the backend re-checks every request itself**, so nothing here depends on the
proxy being right.

---

## 1. The two gates

An action happens only if **both** agree:

| | Question | Where |
|---|---|---|
| **Role** | Is this account trusted with this? | `backend/roles.py` |
| **Security level** | Does this server allow it at all? | `backend/policy.py` |

They are not redundant. A server left at `SECURITY_LEVEL=readonly` refuses save
edits **from the Owner**, because that dial protects the world from mistakes
rather than expressing distrust. Raising the security level does not hand a
Player the save editor.

Only write capabilities are gated by the security level. Reads are governed by
the role alone — plus the visibility settings in §5.

---

## 2. The roles

Ranked. Each row includes everything above it unless stated.

| Rank | Role | In one line |
|---:|---|---|
| 0 | **Guest** | Not signed in. Sees only what the guest visibility toggles allow, with names and Steam IDs stripped. |
| 1 | **Read only** | A named account that can look but change nothing. |
| 2 | **Player** | Their own character: their bases, palbox, breeding planner, progression, discovered map. |
| 3 | **Trusted player** | Everything about *other* players too — rosters, guild inventories, server-wide breeding. Still read-only. |
| 4 | **Moderator** | Day-to-day operation: kick, ban, announce, restart, backups, audit log. No save edits, no accounts. |
| 5 | **Administrator** | Server settings and save editing. Cannot manage accounts or change the security policy. |
| 6 | **Owner** | Everything, including accounts and the security policy. There is always at least one. |

`Guest` is the absence of an account and cannot be assigned.

---

## 3. Capabilities

| Capability | Grants | Roles |
|---|---|---|
| `view.basic` | Server status, map, bases, guilds, the Paldeck | Guest+ |
| `view.self` | **Your own** character, palbox, items, breeding, progress | Player+ |
| `view.detail` | **Everyone's** — rosters, inventories, container contents | Trusted+ |
| `players.moderate` | Kick, ban, unban, announce | Moderator+ |
| `server.control` | Restart, stop, start, force-save, shutdown | Moderator+ |
| `backup.manage` | Create, verify, restore, prune backups; world copies | Moderator+ |
| `audit.view` | Read the audit log | Moderator+ |
| `settings.write` | Read and write `PalWorldSettings.ini` | Admin+ |
| `save.sort.stackables` | Tidy plain stackable items | Admin+ |
| `save.sort.all` | Tidy everything, equipment included | Admin+ |
| `save.edit.full` | Edit players, Pals and container slots; teleport; imports | Admin+ |
| `policy.manage` | Change the security level and visibility settings | Owner |
| `users.manage` | Create, edit and remove accounts | Owner |

**`players.moderate` and `server.control` are separate on purpose.** Taking the
server down is an operations decision; banning a griefer is a social one. Both go
to Moderator by default so no existing account changed, but either can be
withdrawn without the other by editing `ROLES` in `backend/roles.py`.

`settings.write` covers **reading** the INI as well as writing it. That file holds
the server password; `settings_ini.SECRET_KEYS` masks secrets on read and in the
audit log, but the file is still an admin document.

---

## 4. `view.self` vs `view.detail` — the line that matters most

This is the distinction that decides what a normal player experiences.

**`view.self` (Player)** — the same endpoints, scoped to you:

- **My Pals** — your palbox, party and base workers, with filters
- **Breeding** — the planner over *your* Pals, gender-aware
- **Items** — your guild's storage totals
- **Bases** — your own guild's bases, their storage, and the contents of their
  containers. Your own base's chests are something you can walk up to in game
- **Progress** — your own row
- **Discoveries** — the map you have uncovered
- **Export** — your own character and your own Pals

**`baseVisibility` does not widen the storage half.** That setting is about
*locations on a map*; an inventory is a much larger disclosure than a map pin, so
opening the map to everyone does not hand out other guilds' chest contents.
Seeing another guild's storage needs `view.detail`.

**`view.detail` (Trusted)** adds *other people*: the player roster, anyone's
inventory, any container, server-wide reports, Pal-check scans.

Below the threshold the backend **scopes rather than refuses**, and `?owner=` is
**ignored, not honoured** — a query parameter is a convenience for people who may
already see everyone, never a way around the setting. `backend/tests/test_api_scoping.py`
exists to pin exactly that.

---

## 5. Visibility settings (not capabilities)

Five settings sit *alongside* roles and answer taste questions about how a
server is run. Each takes the same vocabulary: `everyone`, a **role name**
(meaning that rank and above), or a "nobody" sentinel.

| Setting | Default | Governs |
|---|---|---|
| `discoveryVisibility` | `trusted` | Seeing locations **nobody has found yet** — the default for both categories below |
| `discoveryCategoryVisibility` | inherits | Per category: `fastTravel` and `effigies`, separately settable |
| `baseVisibility` | `own` | Seeing **other guilds'** bases |
| `serverTotalsVisibility` | `admin` | **Server-wide** item totals vs your own guild's |
| `allPalsVisibility` | `trusted` | **Everyone's** Pals in the planner vs your own |
| `worldObjectVisibility` | per category | Ore, chests, dungeons and fishing spots from the game files |

Set them on the **Access** tab, or as environment variables, where the env value
is a **ceiling the web UI cannot raise**.

**They stay five settings rather than one dial, on purpose.** They are not points
on a single "openness" axis. A completionist co-op group wants every ore node
shown (no spoiler concern) and every base hidden (six strangers on a rented box);
a competitive server wants exactly the opposite. No ordering of values makes both
right. The Access tab offers **presets** — *Private / friends*, *Community
server*, *Competitive / PvP* — that write all four thresholds at once and then
get out of the way, leaving every dial independently adjustable afterwards.

Three things worth knowing:

- **Staff are always exempt.** Anyone with `players.moderate` sees everything
  these settings withhold. Moderation cannot work through a filter, and this
  saves anyone maintaining an exemption list.
- **`baseVisibility` defaults to `own` because privacy alone could not cover
  it.** Per-player privacy reads the `users` table, so a player who has never
  signed into the dashboard has no row and nothing hides them. On a normal
  server most players never sign in.
- **Filtering happens server-side, always.** A UI that received everything and
  hid some of it would be handing out the answers in the network tab.

---

## 6. Per-player privacy — the one rule players control

`backend/privacy.py`. The whole rule:

> hidden ⟺ `viewer_rank <= hider_rank`

So a player **can never hide from staff**, and **equal rank is concealed** —
peers are exactly who a privacy setting is for.

| Mode | Hides |
|---|---|
| `off` | Nothing |
| `player` | You, on the map and in rosters |
| `player_bases` | You and your bases (solo guilds only) |
| `guild` | You, your guild and all its bases |
| `bases_only` | Your bases — but your live location still shows |

**The default is the most private mode.** Nobody should have to discover a
privacy setting exists before they stop being exposed, and it costs little
because staff see everyone regardless.

`bases_only` is the odd one out: the other four are a ladder, each hiding
everything the previous did plus more. This one is a different axis.

Separately, a **guild master** can hide one specific base
(`backend/baseprivacy.py`) — a base belongs to a guild, so that is not one
player's decision. If the guild master has no dashboard account, any member may.

Privacy governs map and roster visibility only. The audit log, account management
and save editing all work on real identities regardless.

---

## 7. Guest visibility

A guest has no account, so roles do not apply. Instead each of eight features is
individually switchable on the Access tab: `serverStatus`, `onlinePlayers`,
`bases`, `guilds`, `mapObjects`, `chests`, `items`, `breeding`.

Guests never see player names or Steam IDs, whatever is enabled.

Set `GUEST_ACCESS=false` to disable guest sessions entirely.

---

## 8. Auditing

Every mutating action is recorded (`backend/audit.py`): who, what, when, from
which IP, and the target's display name **captured at the time** — a uid is
unreadable and people rename themselves.

**Failed attempts are audited too.** An attempt that did not land still says who
tried, and auditing only successes hides exactly the case being investigated.

Read it on the **Audit log** tab (`audit.view`, Moderator+).

---

## 9. Changing any of this

- **Roles → capabilities**: edit `ROLES` in `backend/roles.py`. There is no UI;
  it is a deployment decision, not a runtime one.
- **Visibility settings**: Access tab, or environment variables as a ceiling.
- **A new backend route**: add it to `src/lib/permissions.ts` **and** call
  `authz.require` inside the route. Missing the first makes it unreachable;
  missing the second leaves it trusting the proxy, which is the one thing the
  security boundary says not to do.

See `docs/CONFIGURATION.md` for every environment variable, and
`docs/ARCHITECTURE.md` for the request path.
