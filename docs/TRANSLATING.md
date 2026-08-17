# Translating the dashboard

**Two different things are translated here, from two different sources, and
keeping them apart is the whole design.**

| | Who wrote it | Where the translation comes from |
|---|---|---|
| **Game nouns** — Pal names, item names, structures | Pocketpair | The game's own `L10N/` tables, **already shipped in 15 languages** |
| **Our chrome** — buttons, headings, error messages | This project | **Contributed by people. This document.** |

The first is done and needs nobody: pick a language in the header and Pal and
item names switch to Pocketpair's own words. The second is what this file is
about.

---

## The chrome IS machine-translated now — as a LABELLED beta (operator decision, 2026-08-17)

The original refusal below was about provenance: a sentence this project wrote,
machine-translated into German, renders **identically** to one Pocketpair
published — and gets trusted the same way. The operator chose the middle path
that keeps what the refusal protected while shipping the feature:

- Every machine pack carries **`provenance: "machine"` and `verified: false`**,
  and the language picker shows an **"auto-translated β"** badge whenever one
  is active. The label IS the feature.
- **The game's own words win wherever a concept key exists** — the Paldeck tab
  renders whatever Pocketpair calls the Palpedia in your language (and in
  English: the game's 1.0 word is "Palpedia"), fast travel is the game's own
  term, and so on. `scripts/build-chrome-packs.py` reads those from the server
  pak per language.
- **Safety-critical strings stay English in every machine pack** — the
  save-editing preconditions and the backup/restore confirmations
  (`docs/chrome-wrapped.json` `safetyCritical`). A mistranslated "The server
  must be stopped first" can cost someone a world; those wait for a human.
- A human contributor **promotes** a language: review
  `scripts/chrome-mt/<code>.json`, fix what needs fixing, translate the safety
  strings, and flip the pack to `provenance: "human", verified: true` in
  `build-chrome-packs.py`'s output — the badge disappears.

The pipeline: `scripts/wrap-chrome-strings.py` wraps display positions in
`t()` (JSX text nodes and display attributes only — never logic strings),
`scripts/chrome-mt/<code>.json` holds the translations,
`scripts/build-chrome-packs.py` builds `src/lib/chrome-langs/<code>.json`, and
`src/lib/chrome.ts` loads them as code-split chunks. `src/lib/chrome.test.ts`
pins the contract: provenance on every pack, no safety string in any machine
pack.

## Why it was not machine-translated before (kept for the reasoning)

Because a sentence this project wrote, machine-translated into German, renders
**identically** to one Pocketpair published — and gets trusted the same way. The
whole method here is that a claim carries its provenance; shipping 600 invented
German strings beside 1,800 real ones destroys exactly that distinction, and
nobody downstream can tell which is which. *(The labelled beta above is the
answer to this, not an exception to it: the provenance now travels in the pack
and on the screen.)*

Measured, so this is a finding rather than a policy preference: of the
dashboard's own chrome, **8 strings have a checkable equivalent** among the
game's 405 concept-keyed `common_*` rows — **3% by occurrence**. There is no
existing source for the other 97%.

*(An earlier pass matched 32 by joining on the English **value** and reported
14%. That join is unsound and its own sample proves it: `Clear` matched a quest
string meaning "Completed!", `Detail` matched one containing "not implemented".
Three quarters of those matches were spurious. The concept join is the sound
instrument, and 3% is the real number.)*

So: an untranslated string stays **visibly English**. That is a worse-looking
dashboard and an honest one, and it is recoverable — a wrong translation nobody
can identify as ours is not.

---

## The catalogue

```bash
python3 scripts/extract-chrome-strings.py            # regenerate
python3 scripts/extract-chrome-strings.py --report   # summarise, write nothing
```

Writes `docs/chrome-strings.json`: **631 distinct strings, 718 occurrences,
across 75 files**, each with the files it appears in.

**It is generated, never hand-maintained.** A hand-written list stops covering
the UI the first time somebody adds a component, and nothing tells you — the
same failure mode as a filter applied to one of two endpoints. Re-run it after
any UI change and commit the diff.

### Two kinds of entry you must NOT translate

- **`useGameString`** — the game ships this word, keyed by concept
  (`common_cancel`, `common_work_suitability`). Take Pocketpair's; it is what
  the player already reads in-game.
- **`gameNoun: true`** — a display name the game itself ships, detected by
  checking the string against the bundled catalogue rather than by a hand list.
  "Dimensional Pal Storage", "AI Core", "Lifmunk Effigy". The overlay already
  has these in fifteen languages, and a hand translation would **overwrite a
  better source with a worse one** — and disagree with the same noun rendered
  two panels over.

That leaves **622 strings that are genuinely ours**.

### Where to start

Sorted by occurrence count, so the top of the file is the best value — one
string, many screens. The first few: `Loading…` (8 files), `Preview failed` (8),
`The server must be stopped first` (5).

---

## Things worth knowing before you translate

- **"Alpha" and "Lucky" are the game's concepts** even where our string is our
  own wording. Check what your language's Palworld build calls them and match
  it, or a player reads two names for one thing.
- **Do not translate an internal id.** Anything in `mono` type next to a name —
  `SheepBall`, `AIcore`, a GUID — is what the save stores and what the API
  speaks. The dashboard shows it deliberately.
- **Error messages are read under stress.** Prefer the plain form. "The server
  must be stopped first" is a precondition, not a failure, and should not read
  as one.
- **Some strings are deliberately hedged** and the hedge is load-bearing.
  "Approximate", "not verified", "the game does not state this" mark places
  where this project refuses to assert something. Translate the uncertainty;
  do not tidy it away into a confident sentence.
- **Numbers and units stay.** `×1.2`, `25,600`, `Lv 50`.

---

## Status

**Shipped 2026-08-17** as the labelled beta described at the top: the runtime
(`src/lib/chrome.ts`), 15 machine packs plus an `en` pack carrying the game's
own vocabulary, the picker badge, and the safety-critical holdout. One picker
drives game nouns and chrome together.

**If you want to verify a language**, edit `scripts/chrome-mt/<code>.json`,
translate the `safetyCritical` strings from `docs/chrome-wrapped.json`, run
`python3 scripts/build-chrome-packs.py`, and open a PR — the maintainer flips
the pack's provenance to `human` and the badge goes away.
