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

    # Asserted behaviourally rather than as `MODES[-1]`. That held while the
    # modes were a strict ladder, and stopped holding when `bases_only` was
    # added — it sits last but is deliberately *less* private, hiding bases
    # while leaving the player visible. Position was never the property that
    # mattered; concealing the most is.
    hidden = privacy.hidden_uids("player")
    uid = privacy.normalise_uid(SAVE_UID_A)
    assert uid in hidden["players"]
    assert uid in hidden["bases"]
    assert uid in hidden["guilds"]
    for mode in privacy.MODES:
        if mode == privacy.DEFAULT_MODE:
            continue
        privacy.set_mode("alice", mode)
        other = privacy.hidden_uids("player")
        assert not (
            other["players"] >= hidden["players"]
            and other["bases"] >= hidden["bases"]
            and other["guilds"] >= hidden["guilds"]
            and (other["players"] | other["bases"] | other["guilds"])
            > (hidden["players"] | hidden["bases"] | hidden["guilds"])
        ), f"{mode} conceals more than the default"
    privacy.set_mode("alice", privacy.DEFAULT_MODE)


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


# ─── bases_only: the one mode that is not a rung on the ladder ────


def test_bases_only_hides_bases_but_not_the_player(defaults):
    """
    The inverse of `player`, and the reason `MODES` is no longer an ordered
    ladder: some people do not mind being seen playing and simply would rather
    their base locations were not advertised.
    """
    privacy.set_mode("alice", "bases_only")
    hidden = privacy.hidden_uids("player")
    uid = privacy.normalise_uid(SAVE_UID_A)

    assert uid not in hidden["players"], "the player should stay visible"
    assert uid in hidden["bases"]
    assert uid in hidden["guilds"]


def test_bases_only_still_never_hides_from_staff(defaults):
    """`hidden ⟺ viewer_rank <= hider_rank` applies to every mode alike."""
    privacy.set_mode("alice", "bases_only")
    hidden = privacy.hidden_uids("moderator")
    uid = privacy.normalise_uid(SAVE_UID_A)
    assert uid not in hidden["bases"]
    assert uid not in hidden["guilds"]


def test_bases_only_is_offered_to_the_ui_with_an_explanation():
    modes = {m["id"]: m for m in privacy.describe_modes()}
    assert "bases_only" in modes
    assert modes["bases_only"]["label"]
    assert "position" in modes["bases_only"]["description"].lower()


# ─── You never hide from your own guild ───────────────────
#
# The rule `baseprivacy.py` had and this module did not. Combined with
# `DEFAULT_MODE` being the most private option, the effect on a fresh server was
# that two friends in one guild, both on defaults, could not see each other's
# base, each other's position, or each other at all — and nothing looked broken,
# the map was simply empty.


@pytest.fixture
def one_guild(monkeypatch):
    """Alice and Carol share a guild; Dave is in his own."""
    import savecache

    monkeypatch.setattr(
        savecache, "get_section",
        lambda name: [
            {"id": "g1", "name": "Ours", "members": [
                {"uid": SAVE_UID_A, "name": "Alice"},
                {"uid": SAVE_UID_C, "name": "Carol"},
            ]},
            {"id": "g2", "name": "Theirs", "members": [
                {"uid": SAVE_UID_B, "name": "Dave"},
            ]},
        ] if name == "guilds" else [],
    )


def test_guildmates_see_each_other_on_the_default(fresh_db, one_guild):
    """
    Two friends, one guild, nobody has touched a setting. This is the exact
    case that broke, and the one a fresh server starts in.
    """
    accounts.create_user("alice", PASSWORD, "player", steam_uid=SAVE_UID_A)
    accounts.create_user("carol", PASSWORD, "player", steam_uid=SAVE_UID_C)

    hidden = privacy.hidden_uids("player", "alice")
    assert privacy.normalise_uid(SAVE_UID_C) not in hidden["players"]
    assert privacy.normalise_uid(SAVE_UID_C) not in hidden["bases"]
    assert privacy.normalise_uid(SAVE_UID_C) not in hidden["guilds"]


def test_someone_in_another_guild_is_still_hidden(fresh_db, one_guild):
    """The exemption must not become 'privacy does nothing'."""
    accounts.create_user("alice", PASSWORD, "player", steam_uid=SAVE_UID_A)
    accounts.create_user("dave", PASSWORD, "player", steam_uid=SAVE_UID_B)

    hidden = privacy.hidden_uids("player", "alice")
    assert privacy.normalise_uid(SAVE_UID_B) in hidden["players"]


def test_the_exemption_works_when_the_viewers_own_privacy_is_off(fresh_db, one_guild):
    """
    `all_settings()` only returns accounts with privacy *on*, so a viewer who
    opted out is absent from it — and still has a guild whose members must stay
    visible. Resolved through `_linked_uid` for exactly this case.
    """
    accounts.create_user("alice", PASSWORD, "player", steam_uid=SAVE_UID_A)
    accounts.create_user("carol", PASSWORD, "player", steam_uid=SAVE_UID_C)
    privacy.set_mode("alice", "off")

    hidden = privacy.hidden_uids("player", "alice")
    assert privacy.normalise_uid(SAVE_UID_C) not in hidden["players"]


def test_an_unparsed_world_falls_back_to_the_plain_rank_rule(fresh_db, monkeypatch):
    """
    No guild data means the exemption cannot apply, and it must fail towards
    *more* privacy rather than less — the safe direction for concealment.
    """
    import savecache

    monkeypatch.setattr(savecache, "get_section", lambda name: [])
    accounts.create_user("alice", PASSWORD, "player", steam_uid=SAVE_UID_A)
    accounts.create_user("carol", PASSWORD, "player", steam_uid=SAVE_UID_C)

    hidden = privacy.hidden_uids("player", "alice")
    assert privacy.normalise_uid(SAVE_UID_C) in hidden["players"]


def test_a_guildmate_is_visible_even_at_the_widest_mode(fresh_db, one_guild):
    """
    `guild` mode hides a shared asset. Hiding it from the people who share it is
    not privacy, it is breakage — which is why the exemption sits above the mode
    check rather than being special-cased per mode.
    """
    accounts.create_user("alice", PASSWORD, "player", steam_uid=SAVE_UID_A)
    accounts.create_user("carol", PASSWORD, "player", steam_uid=SAVE_UID_C)
    privacy.set_mode("carol", "guild")

    hidden = privacy.hidden_uids("player", "alice")
    assert not any(
        privacy.normalise_uid(SAVE_UID_C) in hidden[key]
        for key in ("players", "bases", "guilds")
    )
