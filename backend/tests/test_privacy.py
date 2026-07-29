"""
Per-player map privacy.

The rule is one line — `hidden ⟺ viewer_rank <= hider_rank` — so most of these
tests are about the consequences of that line holding at the edges, plus the one
thing that would make the whole feature silently do nothing: uid normalisation.
"""

from __future__ import annotations

import pytest

import accounts
import privacy

PASSWORD = "LongEnoughPw1!"
SAVE_UID_A = "22b22b02-0000-0000-0000-000000000000"
SAVE_UID_B = "11a11a01-0000-0000-0000-000000000000"
SAVE_UID_C = "44d44d04-0000-0000-0000-000000000000"


@pytest.fixture
def people(fresh_db):
    """
    Three accounts, all explicitly visible.

    The default is the most private mode, so a test that did not opt everyone in
    would be measuring the default rather than whatever it set — every other
    account would also be hidden and the assertions would pass or fail for the
    wrong reason. `test_default_*` covers the default on purpose.
    """
    accounts.create_user("alice", PASSWORD, "player", steam_uid=SAVE_UID_A)
    accounts.create_user("mod", PASSWORD, "moderator", steam_uid=SAVE_UID_B)
    accounts.create_user("carol", PASSWORD, "player", steam_uid=SAVE_UID_C)
    for name in ("alice", "mod", "carol"):
        privacy.set_mode(name, "off")
    return None


@pytest.fixture
def defaults(fresh_db):
    """The same accounts, left at whatever the default is."""
    accounts.create_user("alice", PASSWORD, "player", steam_uid=SAVE_UID_A)
    accounts.create_user("mod", PASSWORD, "moderator", steam_uid=SAVE_UID_B)
    return None


# ─── The rule ────────────────────────────────────────────────────


@pytest.mark.parametrize("viewer,hider,expected", [
    # peers and below are concealed
    ("guest", "player", True), ("readonly", "player", True), ("player", "player", True),
    # anyone above is not
    ("trusted", "player", False), ("moderator", "player", False),
    ("admin", "player", False), ("owner", "player", False),
    # a moderator hiding conceals from players, not from admins
    ("player", "moderator", True), ("moderator", "moderator", True),
    ("admin", "moderator", False),
])
def test_rank_comparison(viewer, hider, expected):
    assert privacy.conceals(viewer, hider, "player") is expected


def test_a_player_cannot_hide_from_staff():
    """
    The property that makes this safe to offer at all: no setting a player can
    choose removes them from a moderator's view.
    """
    for mode in privacy.MODES:
        assert privacy.conceals("moderator", "player", mode) is False
        assert privacy.conceals("owner", "player", mode) is False


def test_off_conceals_from_nobody():
    for viewer in ("guest", "player", "owner"):
        assert privacy.conceals(viewer, "owner", "off") is False


def test_an_unknown_mode_conceals_nothing():
    """Fail open here, not closed — a bad value must not blank the map."""
    assert privacy.conceals("guest", "owner", "invisible") is False


def test_an_unknown_viewer_role_is_treated_as_lowest():
    """An unrecognised role ranks below everyone, so it is concealed from."""
    assert privacy.conceals("wizard", "readonly", "player") is True


# ─── uid normalisation ───────────────────────────────────────────


def test_account_and_save_uids_normalise_to_the_same_thing():
    """
    The bug this prevents is silent: accounts store the uid dash-stripped and
    lowercased, saves store it dashed. Comparing raw matches nothing, so privacy
    would hide nobody while every setting still read as enabled.
    """
    assert privacy.normalise_uid(SAVE_UID_A) == privacy.normalise_uid(
        SAVE_UID_A.replace("-", "").upper()
    )


def test_hidden_uids_match_save_shaped_ids(people):
    privacy.set_mode("alice", "player")
    hidden = privacy.hidden_uids("player")

    players = [{"uid": SAVE_UID_A, "name": "Alice"}, {"uid": SAVE_UID_C, "name": "Carol"}]
    assert [p["name"] for p in privacy.filter_players(players, hidden["players"])] == ["Carol"]


# ─── Whole-flow behaviour ────────────────────────────────────────


def test_a_viewer_never_hides_from_themselves(defaults):
    """Whatever the default, you can always see your own things."""
    hidden = privacy.hidden_uids("player", "alice")
    assert privacy.normalise_uid(SAVE_UID_A) not in hidden["players"]


def test_case_insensitive_on_the_username(defaults):
    hidden = privacy.hidden_uids("player", "ALICE")
    assert privacy.normalise_uid(SAVE_UID_A) not in hidden["players"]


def test_an_account_with_no_character_hides_nothing(fresh_db):
    accounts.create_user("ghost", PASSWORD, "player")
    privacy.set_mode("ghost", "guild")
    assert privacy.hidden_uids("guest")["players"] == set()


def test_off_is_an_explicit_opt_in_to_being_seen(people):
    privacy.set_mode("alice", "off")
    assert privacy.hidden_uids("player")["players"] == set()


def test_modes_conceal_progressively(people):
    expected = {
        "off": (False, False, False),
        "player": (True, False, False),
        "player_bases": (True, True, False),
        "guild": (True, True, True),
    }
    for mode, (as_player, as_base, as_guild) in expected.items():
        privacy.set_mode("alice", mode)
        hidden = privacy.hidden_uids("player")
        uid = privacy.normalise_uid(SAVE_UID_A)
        assert (uid in hidden["players"]) is as_player, mode
        assert (uid in hidden["bases"]) is as_base, mode
        assert (uid in hidden["guilds"]) is as_guild, mode


def test_setting_an_unknown_mode_is_refused(people):
    with pytest.raises(ValueError, match="Unknown privacy mode"):
        privacy.set_mode("alice", "invisible")


def test_the_default_is_the_most_private_option(defaults):
    """
    Nobody should have to find out a privacy setting exists before they stop
    being exposed. Opting in to visibility is a choice; opting out is a thing
    people discover too late.
    """
    assert privacy.get_mode("alice") == "guild"
    assert privacy.DEFAULT_MODE == privacy.MODES[-1]

    hidden = privacy.hidden_uids("player")
    assert privacy.normalise_uid(SAVE_UID_A) in hidden["players"]


def test_the_private_default_still_leaves_staff_able_to_see(defaults):
    """
    Private-by-default must not mean moderation starts out blind. A Player on the
    default setting is invisible to peers and fully visible to staff.
    """
    alice = privacy.normalise_uid(SAVE_UID_A)
    for viewer in ("trusted", "moderator", "admin", "owner"):
        assert alice not in privacy.hidden_uids(viewer)["players"], viewer
    for viewer in ("guest", "readonly", "player"):
        assert alice in privacy.hidden_uids(viewer)["players"], viewer


# ─── Base filtering ──────────────────────────────────────────────


def guild(gid, *uids):
    return {"id": gid, "members": [{"uid": u} for u in uids]}


def test_player_bases_hides_only_a_solo_guild():
    """
    Bases belong to guilds. Hiding a shared guild's bases because one member
    opted out would take away other people's things.
    """
    guilds = [guild("solo", SAVE_UID_A), guild("shared", SAVE_UID_A, SAVE_UID_C)]
    bases = [{"guildId": "solo"}, {"guildId": "shared"}]
    hidden = {privacy.normalise_uid(SAVE_UID_A)}

    kept = privacy.filter_bases(bases, guilds, hidden, set())
    assert [b["guildId"] for b in kept] == ["shared"]


def test_guild_mode_hides_a_shared_guild_too():
    guilds = [guild("shared", SAVE_UID_A, SAVE_UID_C)]
    bases = [{"guildId": "shared"}]
    wide = {privacy.normalise_uid(SAVE_UID_A)}

    assert privacy.filter_bases(bases, guilds, set(), wide) == []


def test_no_privacy_leaves_bases_untouched():
    bases = [{"guildId": "a"}, {"guildId": "b"}]
    assert privacy.filter_bases(bases, [], set(), set()) == bases


def test_modes_are_described_for_the_ui():
    described = privacy.describe_modes()
    assert [d["id"] for d in described] == list(privacy.MODES)
    assert all(d["label"] and d["description"] for d in described)
