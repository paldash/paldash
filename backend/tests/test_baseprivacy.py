"""
Per-base map visibility.

The permission model is the interesting part: a base belongs to a *guild*, so the
setting is the guild master's rather than any individual's. The rank rule from
`privacy.py` still applies on top — staff see everything — which is why there is
no staff override to test for.

The other half is that hiding has to cover three endpoints. A base marker dropped
from `/api/bases` while `/api/mapobjects` still returns the palbox standing in it
has hidden a name and published a position, so `filter_objects` is tested as
carefully as `filter_bases`.
"""

from __future__ import annotations

import pytest

import accounts
import baseprivacy
import privacy
import savecache

MASTER_UID = "aaaaaaaa-0000-0000-0000-000000000001"
MEMBER_UID = "bbbbbbbb-0000-0000-0000-000000000002"
OUTSIDER_UID = "cccccccc-0000-0000-0000-000000000003"

BASE_A = "base-aaa"
BASE_SOLO = "base-solo"

BASES = [
    {"id": BASE_A, "name": "Main Base", "guildId": "guild-1", "guildName": "Alpha"},
    {"id": BASE_SOLO, "name": "Hideout", "guildId": "guild-2", "guildName": "Solo"},
]

GUILDS = [
    {
        "id": "guild-1",
        "name": "Alpha",
        "adminPlayerUid": MASTER_UID,
        "members": [{"uid": MASTER_UID, "name": "Master"},
                    {"uid": MEMBER_UID, "name": "Member"}],
    },
    {
        "id": "guild-2",
        "name": "Solo",
        "adminPlayerUid": OUTSIDER_UID,
        "members": [{"uid": OUTSIDER_UID, "name": "Loner"}],
    },
]


@pytest.fixture
def world(monkeypatch):
    """A tiny two-guild world, without parsing anything."""
    monkeypatch.setattr(
        savecache, "get_section",
        lambda name: {"bases": BASES, "guilds": GUILDS}.get(name, []),
    )


@pytest.fixture
def people(fresh_db):
    accounts.create_user("master", "password-long", role="player", steam_uid=MASTER_UID)
    accounts.create_user("member", "password-long", role="player", steam_uid=MEMBER_UID)
    accounts.create_user("peer", "password-long", role="player", steam_uid=OUTSIDER_UID)
    accounts.create_user("mod", "password-long", role="moderator", steam_uid="")
    accounts.create_user("unlinked", "password-long", role="player", steam_uid="")
    return None


# ─── Who may set it ──────────────────────────────────────


def test_the_guild_master_may_hide_their_guilds_base(world, people):
    allowed, why = baseprivacy.can_manage(BASE_A, "master")
    assert allowed
    assert why == "guild master"


def test_an_ordinary_member_may_not_when_the_master_has_an_account(world, people):
    """
    The base is the guild's, and the guild has a leader who uses the dashboard.
    Letting any member hide a shared base would mean the quietest member decides.
    """
    allowed, why = baseprivacy.can_manage(BASE_A, "member")
    assert not allowed
    assert "guild master" in why


def test_a_member_may_when_the_master_has_no_dashboard_account(world, fresh_db):
    """
    The fallback. Without it a guild whose leader does not use the dashboard could
    never hide anything — the feature would be dead for exactly the guilds most
    likely to want it.
    """
    accounts.create_user("member", "password-long", role="player", steam_uid=MEMBER_UID)
    allowed, why = baseprivacy.can_manage(BASE_A, "member")
    assert allowed
    assert "no dashboard account" in why


def test_someone_from_another_guild_may_not(world, people):
    allowed, why = baseprivacy.can_manage(BASE_A, "peer")
    assert not allowed
    assert "guild" in why


def test_staff_get_no_override(world, people):
    """
    Deliberate. A Moderator already sees every hidden base, so an override would
    only let them change other people's settings — which is not moderation.
    """
    allowed, _ = baseprivacy.can_manage(BASE_A, "mod")
    assert not allowed


def test_an_unlinked_account_is_told_why(world, people):
    allowed, why = baseprivacy.can_manage(BASE_A, "unlinked")
    assert not allowed
    assert "not linked" in why


def test_an_unknown_base_is_refused(world, people):
    allowed, why = baseprivacy.can_manage("base-nope", "master")
    assert not allowed
    assert "No base" in why


def test_it_fails_closed_with_no_parsed_world(people, monkeypatch):
    """
    A permission check that cannot see who owns the base must refuse, not guess.
    """
    monkeypatch.setattr(savecache, "get_section", lambda name: [])
    allowed, why = baseprivacy.can_manage(BASE_A, "master")
    assert not allowed
    assert "not been parsed" in why


# ─── Who it hides from ───────────────────────────────────


def hide(base_id="base-aaa", username="master", role="player"):
    baseprivacy.set_hidden(base_id, True, username=username, role=role)


def test_a_hidden_base_is_concealed_from_a_peer(world, people):
    hide()
    assert baseprivacy.hidden_base_ids("player", "peer") == {BASE_A}


def test_a_hidden_base_is_still_visible_to_staff(world, people):
    """The rule that makes the whole model safe: you cannot hide from above."""
    hide()
    assert baseprivacy.hidden_base_ids("moderator", "mod") == set()


def test_a_guild_member_still_sees_their_own_guilds_hidden_base(world, people):
    """
    Otherwise hiding a base would remove it from the map of the people who need
    it most, and read as data loss rather than as a setting.
    """
    hide()
    assert baseprivacy.hidden_base_ids("player", "member") == set()
    assert baseprivacy.hidden_base_ids("player", "master") == set()


def test_a_guest_sees_nothing_hidden(world, people):
    hide()
    assert baseprivacy.hidden_base_ids("guest", "") == {BASE_A}


def test_unhiding_removes_the_row_rather_than_flipping_it(world, people):
    hide()
    baseprivacy.set_hidden(BASE_A, False, username="master", role="player")
    assert baseprivacy.hidden_base_ids("guest", "") == set()
    assert baseprivacy._rows() == []


def test_a_demotion_narrows_what_an_old_setting_conceals(world, people):
    """
    The hider's role is re-resolved on read, not trusted from the row. A base
    hidden while its owner was a Moderator must stop hiding from Moderators once
    they are a Player again.
    """
    accounts.update_user("master", role="moderator")
    hide(username="master", role="moderator")
    assert baseprivacy.hidden_base_ids("moderator", "mod") == {BASE_A}

    accounts.update_user("master", role="player")
    assert baseprivacy.hidden_base_ids("moderator", "mod") == set()
    # Still hidden from peers, though — a demotion narrows the reach, it does not
    # cancel the setting.
    assert baseprivacy.hidden_base_ids("player", "peer") == {BASE_A}


def test_a_deleted_account_falls_back_to_its_stored_role(world, people):
    """
    An orphaned row should neither silently expose the base nor silently promote
    what it can hide from.
    """
    hide(username="master", role="player")
    accounts.delete_user("master")
    assert baseprivacy.hidden_base_ids("player", "peer") == {BASE_A}
    assert baseprivacy.hidden_base_ids("moderator", "mod") == set()


def test_nothing_hidden_costs_no_world_lookup(people, monkeypatch):
    """
    The common case is that nobody uses this. It must not pay for a base and
    guild lookup on every request to find that out.
    """
    calls = []
    monkeypatch.setattr(savecache, "get_section", lambda name: calls.append(name) or [])
    assert baseprivacy.hidden_base_ids("player", "peer") == set()
    assert calls == []


# ─── The three filters ───────────────────────────────────


def test_filter_bases_drops_the_marker():
    kept = baseprivacy.filter_bases(BASES, {BASE_A})
    assert [b["id"] for b in kept] == [BASE_SOLO]


def test_filter_objects_drops_objects_standing_in_a_hidden_base():
    """
    The half that is easy to miss. These objects carry the base's coordinates, so
    leaving them in hides the label and publishes the location.
    """
    objects = [
        {"id": "o1", "baseCampId": BASE_A, "x": 1.0, "y": 2.0},
        {"id": "o2", "baseCampId": BASE_SOLO, "x": 3.0, "y": 4.0},
        {"id": "o3", "baseCampId": "", "worldPlaced": True, "x": 5.0, "y": 6.0},
    ]
    kept = baseprivacy.filter_objects(objects, {BASE_A})
    assert [o["id"] for o in kept] == ["o2", "o3"]


def test_filter_objects_keeps_world_placed_objects():
    """A world chest is not in anybody's base and must never be filtered."""
    objects = [{"id": "chest", "baseCampId": "", "worldPlaced": True}]
    assert baseprivacy.filter_objects(objects, {BASE_A}) == objects


def test_filter_storage_drops_the_contents():
    summaries = [{"baseId": BASE_A, "items": 40}, {"baseId": BASE_SOLO, "items": 2}]
    kept = baseprivacy.filter_storage(summaries, {BASE_A})
    assert [s["baseId"] for s in kept] == [BASE_SOLO]


def test_the_filters_are_free_when_nothing_is_hidden():
    assert baseprivacy.filter_bases(BASES, set()) is BASES
    assert baseprivacy.filter_objects(BASES, set()) is BASES
    assert baseprivacy.filter_storage(BASES, set()) is BASES


# ─── Listing ─────────────────────────────────────────────


def test_manageable_bases_lists_only_what_you_control(world, people):
    result = baseprivacy.manageable_bases("master", "player")
    assert [b["baseId"] for b in result["bases"]] == [BASE_A]
    assert result["bases"][0]["hidden"] is False
    assert result["reason"] == ""


def test_manageable_bases_gives_a_reason_rather_than_an_empty_list(world, people):
    """
    "You are not a guild master" and "nothing is hidden" look identical as an
    empty array, and only one of them is worth explaining.
    """
    result = baseprivacy.manageable_bases("peer", "player")
    # The solo guild's own base is still theirs to manage.
    assert [b["baseId"] for b in result["bases"]] == [BASE_SOLO]

    result = baseprivacy.manageable_bases("unlinked", "player")
    assert result["bases"] == []
    assert "not linked" in result["reason"]


def test_manageable_bases_reflects_the_current_state(world, people):
    hide()
    result = baseprivacy.manageable_bases("master", "player")
    assert result["bases"][0]["hidden"] is True


# ─── Against the real world ──────────────────────────────


@pytest.mark.integration
@pytest.mark.slow
def test_every_reference_guild_has_a_master_who_is_a_member(palsav_available, level_sav):
    """
    The permission model rests on `admin_player_uid` being populated and naming an
    actual member. If it were blank or dangling, every guild would silently fall
    through to the member fallback and the "guild master decides" rule would be
    decoration.
    """
    from parser import extract_base_camps, extract_guilds, load_gvas

    gvas = load_gvas(level_sav)
    guilds = extract_guilds(gvas)
    assert guilds, "reference world has guilds"

    for guild in guilds:
        master = privacy.normalise_uid(guild["adminPlayerUid"])
        members = {privacy.normalise_uid(m["uid"]) for m in guild["members"]}
        assert master, f"guild {guild['name']} has no admin_player_uid"
        assert master in members, (
            f"guild {guild['name']} master {master} is not one of its members"
        )

    # And every base resolves to one of those guilds — a base whose guild is
    # missing would be unmanageable by anyone.
    bases = extract_base_camps(gvas)
    guild_ids = {g["id"] for g in guilds}
    orphans = [b["id"] for b in bases if b["guildId"] not in guild_ids]
    assert not orphans, f"{len(orphans)} bases have no matching guild"
