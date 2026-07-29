"""
Pal imports — reading a Pal back out of an export document.

WHAT THIS MODULE IS NOT
-----------------------
It is not a write path. It has no `guarded_save_write` call, no property writing
and no record creation of its own. Like `slotedit.py`, it *translates*: it turns
an export document into the change set one of the two existing Pal writers
already takes, and hands it over.

    overwrite  ->  charedit.plan_pal_batch / apply_pal_batch
    create     ->  palclone.plan_clone / apply_clone

That is the whole design. Pal edits are the most-tested write path in this
project and the record-creating one is the most dangerous; neither gets a second
entrance for the sake of a file format.

ONE FORMAT, SHARED WITH THE EXPORT
----------------------------------
The document is a `saveexport` envelope of kind `pal` or `player`, and the Pal
dicts inside are byte-for-byte the shape the parser produces. A `player` export
already embeds its owner's whole team in `pals`, so "restore this Pal" and
"restore this player's team" are the same file format read two ways.

AN EXPORT SAYS MORE THAN AN IMPORT MAY WRITE
--------------------------------------------
A parsed Pal carries `ownerUid`, `containerId`, `slotIndex`, `guildId`, `hp`,
`isBoss` and `instanceId`. None of those are settable — they describe where the
Pal *is*, not what it is, and moving a Pal is a different operation from editing
one.

Silently dropping them would be the wrong call: someone importing a Pal from
another server would reasonably assume `ownerUid` came with it. So every
unwritten field is returned in `ignored`, with a reason, and the UI shows the
list before anything is applied. A field that cannot be honoured is stated, not
omitted.

CREATE IS ONE PAL PER REQUEST, AND CLONES A TEMPLATE
----------------------------------------------------
There are two hard limits here and both come from the save format rather than
from caution:

1. **A species cannot be fabricated.** `palclone` deep-copies an existing
   character record precisely because the right values for `CustomVersionData`,
   `permission_tribe_id` and the rest are whatever this save already uses.
   Creating a Pal therefore needs a template of the *same species* already in the
   world; if there is none, the import is refused with that reason rather than
   guessing. Catching one is the fix.
2. **Gender comes from the template**, since it is read-only on an edit. When the
   document disagrees it is reported in `ignored`, never quietly applied.

`create` handles one Pal per request so that it can delegate to `apply_clone`
unchanged and keep that function's all-or-nothing verification exactly as tested.
`overwrite` takes any number, because `apply_pal_batch` is already a batch writer
with the same guarantee. A multi-Pal document in create mode is refused with a
count, not half-applied.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import charedit
import editschema
import palclone
import saveexport

logger = logging.getLogger(__name__)

MODES = ("overwrite", "create")

# The editable fields, in schema names. Derived from the schema rather than
# listed, so a field added to `PAL_FIELDS` becomes importable without a second
# edit here — and one removed cannot linger as a write nothing validates.
IMPORTABLE = tuple(
    name for name in editschema.PAL_FIELDS if name not in charedit.PAL_READ_ONLY
)

# Fields a parsed Pal carries that an import must never write, and why. Stated
# rather than dropped: see the module docstring.
NOT_IMPORTABLE: dict[str, str] = {
    "instanceId": "identifies the record itself; a Pal cannot be given another Pal's id",
    "ownerUid": "ownership is not an editable field — move the Pal instead",
    "containerId": "where the Pal is kept, not what it is",
    "slotIndex": "where the Pal is kept, not what it is",
    "guildId": "follows the owner, and is not settable directly",
    "hp": "current health is recomputed by the game from level and IVs",
    "isBoss": "derived from the species id, not stored separately",
    "characterId": "the raw species id including any BOSS_ prefix; species is not editable",
    "speciesId": "species is not editable on an existing Pal, and a clone takes its template's",
    "speciesName": "a display name resolved from the species id",
    "gender": "not editable; a created Pal takes its template's gender",
}

# A document naming more Pals than this is refused rather than walked. Matches
# the bulk-edit ceiling, since overwrite mode is exactly a bulk edit.
MAX_PALS = charedit.MAX_BULK


class PalImportError(Exception):
    """The document cannot be imported."""


class PalImportRefused(PalImportError):
    """Well-formed, but this build will not apply it."""


def _problem(field: Any, message: str) -> dict:
    return {"field": field, "problem": message}


# ─── Reading the document ────────────────────────────────


def pals_in(document: dict) -> list[dict]:
    """
    Every Pal a document carries, whichever kind it is.

    `pal` holds one under `payload.pal`; `player` holds a team under
    `payload.pals`. Anything else is not a Pal document.
    """
    report = saveexport.verify(document)
    if not report["ok"]:
        raise PalImportError("; ".join(report["problems"]))

    kind = report["kind"]
    payload = document.get("payload") or {}

    if kind == "pal":
        pal = payload.get("pal")
        if not isinstance(pal, dict):
            raise PalImportError("A 'pal' export must carry payload.pal as an object")
        return [pal]

    if kind == "player":
        pals = payload.get("pals")
        if not isinstance(pals, list):
            raise PalImportError("A 'player' export must carry payload.pals as a list")
        return [p for p in pals if isinstance(p, dict)]

    raise PalImportRefused(
        f"A {kind!r} export does not contain Pals. Import a 'pal' or 'player' export, "
        "or use the container importer for inventory."
    )


def extract_changes(pal: dict) -> tuple[dict, list[dict]]:
    """
    Split a parsed Pal into (what can be written, what cannot).

    `ivs` is flattened to `ivs.hp` and friends because that is how the schema
    names them; the export nests them because that is how the parser emits them.
    Missing and null fields are left out entirely rather than written as defaults
    — a document that does not mention a field must not silently zero it.
    """
    changes: dict[str, Any] = {}
    ignored: list[dict] = []

    for key, value in pal.items():
        if key == "ivs":
            if isinstance(value, dict):
                for iv, iv_value in value.items():
                    name = f"ivs.{iv}"
                    if name in IMPORTABLE and iv_value is not None:
                        changes[name] = iv_value
                    elif iv_value is not None:
                        ignored.append(_problem(name, f"{iv!r} is not a known IV"))
            continue

        if key in NOT_IMPORTABLE:
            if value not in (None, "", [], {}):
                ignored.append(_problem(key, NOT_IMPORTABLE[key]))
            continue

        if key not in IMPORTABLE:
            ignored.append(_problem(key, "not a field this build can write"))
            continue

        # `activeSkills` is None on a Pal whose save has no EquipWaza property.
        # Absent means absent — writing an ArrayProperty that is not there means
        # guessing its array_type, which is exactly the MasteredWaza refusal.
        if value is None:
            ignored.append(_problem(key, "absent in the document, so left as it is"))
            continue

        changes[key] = value

    return changes, ignored


# ─── Planning ────────────────────────────────────────────


def _find_pal(gvas: Any, instance_id: str) -> Optional[dict]:
    found = charedit._index_pals(gvas, {instance_id})
    return found.get(instance_id)


def _same_species_template(gvas: Any, species_id: str) -> Optional[str]:
    """
    The instance id of any Pal of this species already in the world.

    `palclone` copies a record rather than building one, so a create needs
    something of the right species to copy. Preferring a non-boss keeps a BOSS_
    prefix from riding along into an ordinary Pal.
    """
    wanted = str(species_id or "").lower()
    if not wanted:
        return None

    fallback = None
    for entry in charedit._character_entries(gvas):
        key = entry.get("key") if isinstance(entry, dict) else None
        instance_id = str(charedit._v(key, "InstanceId", "value", default="") or "")
        obj = charedit._save_parameter(entry)
        if not instance_id or obj is None:
            continue
        character_id = str(charedit._prop(obj, "CharacterID", "") or "")
        if not character_id:
            continue
        if character_id.lower() == wanted:
            return instance_id
        if character_id.upper().startswith("BOSS_") and character_id[5:].lower() == wanted:
            fallback = fallback or instance_id
    return fallback


def plan_import(
    gvas: Any,
    document: dict,
    mode: str,
    instance_id: str = "",
    container_id: str = "",
) -> dict:
    """
    What importing this document would do. Pure — no writes, no I/O.

    In `overwrite` mode the document's Pals are matched to live Pals by their own
    `instanceId` unless `instance_id` names one explicitly, which is what makes
    re-importing an export of this same world a straight restore.
    """
    if mode not in MODES:
        return {
            "ok": False,
            "problems": [_problem("mode", f"Unknown mode {mode!r}. Known: {', '.join(MODES)}")],
            "planHash": "",
        }

    pals = pals_in(document)
    if not pals:
        return {"ok": False, "problems": [_problem(None, "The document names no Pals")],
                "planHash": ""}
    if len(pals) > MAX_PALS:
        return {"ok": False, "planHash": "", "problems": [
            _problem(None, f"{len(pals)} Pals exceeds the {MAX_PALS} maximum for one import")
        ]}

    if mode == "create":
        return _plan_create(gvas, pals, container_id)
    return _plan_overwrite(gvas, pals, instance_id)


def _plan_overwrite(gvas: Any, pals: list[dict], instance_id: str) -> dict:
    if instance_id and len(pals) > 1:
        return {"ok": False, "planHash": "", "problems": [_problem(
            None,
            f"The document names {len(pals)} Pals but a single target was given. Import "
            "without a target to match each Pal to its own instance id.",
        )]}

    subjects: list[tuple[str, dict, dict]] = []
    problems: list[dict] = []
    ignored: list[dict] = []
    unmatched: list[str] = []

    for pal in pals:
        target_id = instance_id or str(pal.get("instanceId") or "")
        if not target_id:
            problems.append(_problem(None, "A Pal in the document has no instanceId to match"))
            continue

        obj = _find_pal(gvas, target_id)
        if obj is None:
            unmatched.append(target_id)
            continue

        changes, dropped = extract_changes(pal)
        ignored.extend({**d, "instanceId": target_id} for d in dropped)
        if not changes:
            continue
        subjects.append((target_id, obj, changes))

    if unmatched:
        problems.append(_problem(None, (
            f"{len(unmatched)} Pal(s) in the document are not in this world: "
            f"{', '.join(unmatched[:5])}{'…' if len(unmatched) > 5 else ''}. "
            "Overwrite needs an existing Pal; use create mode to add one."
        )))

    if problems:
        return {"ok": False, "problems": problems, "ignored": ignored, "planHash": ""}
    if not subjects:
        return {"ok": False, "planHash": "", "ignored": ignored, "problems": [
            _problem(None, "Nothing to change — every field in the document is already set "
                           "or cannot be written")
        ]}

    plan = charedit.plan_pal_batch(subjects)
    plan["mode"] = "overwrite"
    plan["ignored"] = ignored
    return plan


def _plan_create(gvas: Any, pals: list[dict], container_id: str) -> dict:
    if len(pals) != 1:
        return {"ok": False, "planHash": "", "problems": [_problem(None, (
            f"Create mode takes one Pal per request; this document names {len(pals)}. "
            "This is a deliberate limit: creating a Pal appends records to two arrays "
            "and the verification that both grew correctly is written for one request "
            "at a time."
        ))]}
    if not container_id:
        return {"ok": False, "planHash": "", "problems": [
            _problem("containerId", "Create mode needs a destination container")
        ]}

    pal = pals[0]
    species_id = str(pal.get("speciesId") or pal.get("characterId") or "")
    if not species_id:
        return {"ok": False, "planHash": "", "problems": [
            _problem("speciesId", "The document does not say which species this Pal is")
        ]}

    template_id = _same_species_template(gvas, species_id)
    if not template_id:
        return {"ok": False, "planHash": "", "problems": [_problem("speciesId", (
            f"No {species_id} exists in this world to copy. A Pal's record carries values "
            "that are specific to the save it lives in, so one is copied rather than "
            "invented — catch or hatch a single one and the import will work."
        ))]}

    changes, ignored = extract_changes(pal)
    plan = palclone.plan_clone(gvas, template_id, container_id, 1, changes or None)
    plan["mode"] = "create"
    plan["ignored"] = ignored
    plan["templateInstanceId"] = template_id
    plan["speciesId"] = species_id
    return plan


# ─── Applying ────────────────────────────────────────────


def apply_import(
    document: dict,
    mode: str,
    instance_id: str = "",
    container_id: str = "",
    template_instance_id: str = "",
    expected_plan_hash: Optional[str] = None,
    label: str = "pal import",
) -> dict:
    """
    Apply a Pal import by handing it to the writer that already owns that shape.

    This function reads the *document* only. It deliberately does not load the
    world: both writers open Level.sav inside `guarded_save_write`, re-plan
    against that live tree and refuse on a `planHash` mismatch. Planning here as
    well would mean validating against a copy read a moment earlier — a second
    source of truth, and the one that is guaranteed to be the staler of the two.

    `template_instance_id` comes from the preview for the same reason a
    `planHash` does. If that Pal has since been released, `apply_clone` refuses
    because it cannot find it, which is the correct failure.
    """
    if mode not in MODES:
        raise PalImportError(f"Unknown mode {mode!r}. Known: {', '.join(MODES)}")

    pals = pals_in(document)
    if not pals:
        raise PalImportError("The document names no Pals")
    if len(pals) > MAX_PALS:
        raise PalImportRefused(
            f"{len(pals)} Pals exceeds the {MAX_PALS} maximum for one import"
        )

    if mode == "create":
        if len(pals) != 1:
            raise PalImportRefused(
                f"Create mode takes one Pal per request; this document names {len(pals)}"
            )
        if not container_id:
            raise PalImportError("Create mode needs a destination container")
        if not template_instance_id:
            raise PalImportError(
                "Create mode needs the template Pal chosen at preview time. Preview the "
                "import first."
            )
        changes, ignored = extract_changes(pals[0])
        result = palclone.apply_clone(
            template_instance_id, container_id, 1, changes or None,
            expected_plan_hash=expected_plan_hash,
        )
        return {**result, "mode": "create", "ignored": ignored}

    if instance_id and len(pals) > 1:
        raise PalImportRefused(
            f"The document names {len(pals)} Pals but a single target was given"
        )

    edits: dict[str, dict] = {}
    ignored = []
    for pal in pals:
        target_id = instance_id or str(pal.get("instanceId") or "")
        if not target_id:
            raise PalImportError("A Pal in the document has no instanceId to match")
        changes, dropped = extract_changes(pal)
        ignored.extend({**d, "instanceId": target_id} for d in dropped)
        if changes:
            edits[target_id] = changes

    if not edits:
        raise PalImportRefused(
            "Nothing to change — every field in the document is either already set or "
            "cannot be written"
        )

    result = charedit.apply_pal_batch(edits, label=label, expected_plan_hash=expected_plan_hash)
    return {**result, "mode": "overwrite", "ignored": ignored}
