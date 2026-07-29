"""
Moderation.

These commands were reachable before this module existed — through the Next.js
proxy, gated on a capability, and leaving no trace. So the tests that matter are
the ones about the record: that a kick is audited, that a *failed* kick is also
audited, and that the target's name is captured while it is still knowable.
"""

from __future__ import annotations

import pytest

import audit
import db
import gameapi
import moderate

ACTOR = {"username": "mod", "role": "moderator"}
UID = "22b22b02-0000-0000-0000-000000000000"


@pytest.fixture
def online(monkeypatch):
    """A server with one player on it."""
    monkeypatch.setattr(gameapi, "players", lambda: [
        {"userId": UID, "name": "Griefer", "playerId": "abc"},
    ])
    return None


@pytest.fixture
def offline(monkeypatch):
    def boom():
        raise gameapi.GameApiUnavailable("server is stopped")
    monkeypatch.setattr(gameapi, "players", boom)
    return None


def entries(action=None):
    sql = "SELECT * FROM audit_log"
    params: tuple = ()
    if action:
        sql += " WHERE action = ?"
        params = (action,)
    return db.connect().execute(sql + " ORDER BY id", params).fetchall()


# ─── Message hygiene ─────────────────────────────────────


def test_newlines_are_stripped_from_a_message():
    """
    A newline ends the command early, so the tail of the message would vanish with
    no error at all — worth removing rather than discovering in-game.
    """
    assert moderate.clean_message("hello\nworld") == "hello world"
    assert moderate.clean_message("a\r\nb\tc") == "a b c"


def test_a_message_is_length_capped():
    assert len(moderate.clean_message("x" * 1000)) == moderate.MAX_MESSAGE


def test_a_blank_required_message_is_refused():
    with pytest.raises(moderate.ModerationError, match="required"):
        moderate.clean_message("   ", required=True)


def test_a_blank_optional_message_is_fine():
    assert moderate.clean_message(None) == ""


# ─── The audit record ────────────────────────────────────


def test_a_kick_is_audited_with_the_player_name(fresh_db, online, monkeypatch):
    """
    The name is the point. A uid is unreadable and a player can rename themselves,
    so "who was 22b22b02?" has no answer later unless it was written down now.
    """
    monkeypatch.setattr(gameapi, "kick", lambda uid, msg: {"ok": True})
    moderate.kick(UID, "griefing", actor=ACTOR, ip="10.0.0.5")

    logged = entries(audit.PLAYER_KICK)
    assert len(logged) == 1
    assert logged[0]["target"] == UID
    assert logged[0]["result"] == audit.RESULT_OK
    assert "Griefer" in logged[0]["detail"]
    assert "griefing" in logged[0]["detail"]


def test_a_failed_kick_is_audited_too(fresh_db, online, monkeypatch):
    """
    An attempt that did not land still says who tried. Auditing only successes
    hides exactly the case an operator is investigating.
    """
    def boom(uid, msg):
        raise gameapi.GameApiError("server said no")
    monkeypatch.setattr(gameapi, "kick", boom)

    with pytest.raises(moderate.ModerationError):
        moderate.kick(UID, actor=ACTOR, ip="10.0.0.5")

    logged = entries(audit.PLAYER_KICK)
    assert len(logged) == 1
    assert logged[0]["result"] == audit.RESULT_FAILED
    assert "server said no" in logged[0]["detail"]


def test_a_ban_is_audited_separately_from_a_kick(fresh_db, online, monkeypatch):
    """
    Distinct action names so "who has been banned here" is one filter rather than
    a scan through every server action.
    """
    monkeypatch.setattr(gameapi, "ban", lambda uid, msg: {})
    moderate.ban(UID, "cheating", actor=ACTOR)

    assert len(entries(audit.PLAYER_BAN)) == 1
    assert len(entries(audit.PLAYER_KICK)) == 0


def test_an_announce_is_audited_with_its_text(fresh_db, monkeypatch):
    monkeypatch.setattr(gameapi, "announce", lambda msg: {})
    moderate.announce("restarting in 5", actor=ACTOR)

    logged = entries(audit.SERVER_ANNOUNCE)
    assert len(logged) == 1
    assert "restarting in 5" in logged[0]["detail"]


def test_an_unban_is_audited(fresh_db, monkeypatch):
    monkeypatch.setattr(gameapi, "unban", lambda uid: {})
    moderate.unban(UID, actor=ACTOR)
    assert len(entries(audit.PLAYER_UNBAN)) == 1


# ─── Resolving a target ──────────────────────────────────


def test_an_offline_server_does_not_block_the_command(fresh_db, offline, monkeypatch):
    """
    The name lookup is for readability, not correctness. Failing the whole ban
    because the roster could not be read would refuse a valid action; an unnamed
    ban is better than none.
    """
    sent = []
    monkeypatch.setattr(gameapi, "ban", lambda uid, msg: sent.append(uid) or {})
    moderate.ban(UID, actor=ACTOR)
    assert sent == [UID]


def test_the_uid_is_passed_through_unchanged(fresh_db, online, monkeypatch):
    """
    The game's own listing is the authority on how a uid is spelled. Normalising
    it here could produce a form the game does not match.
    """
    dashed = UID
    sent = []
    monkeypatch.setattr(gameapi, "kick", lambda uid, msg: sent.append(uid) or {})
    moderate.kick(dashed, actor=ACTOR)
    assert sent == [dashed]


def test_a_uid_is_matched_across_spellings_for_the_name(fresh_db, monkeypatch):
    """The roster may say undashed where the caller said dashed, or vice versa."""
    monkeypatch.setattr(gameapi, "players", lambda: [
        {"userId": UID.replace("-", "").upper(), "name": "Griefer"},
    ])
    monkeypatch.setattr(gameapi, "kick", lambda uid, msg: {})
    moderate.kick(UID, actor=ACTOR)

    assert "Griefer" in entries(audit.PLAYER_KICK)[0]["detail"]


def test_no_player_named_is_refused(fresh_db):
    for command in (moderate.kick, moderate.ban):
        with pytest.raises(moderate.ModerationError, match="No player"):
            command("", actor=ACTOR)
    with pytest.raises(moderate.ModerationError, match="No player"):
        moderate.unban("   ", actor=ACTOR)


# ─── The ban list ────────────────────────────────────────


def test_a_missing_ban_list_says_so_rather_than_showing_an_empty_one(monkeypatch):
    """
    An empty array and "the file is not there" look identical to a reader and mean
    completely different things — one says nobody is banned.
    """
    monkeypatch.setattr(moderate, "ban_list_path", lambda: None)
    result = moderate.list_bans()

    assert result["found"] is False
    assert result["bans"] == []
    assert "not found" in result["note"]


def test_the_ban_list_is_read_from_the_servers_own_file(tmp_path, monkeypatch):
    path = tmp_path / "banlist.txt"
    path.write_text("# comment\nsteam_7656119\n\nsteam_7656120\n")
    monkeypatch.setattr(moderate, "ban_list_path", lambda: str(path))

    result = moderate.list_bans()
    assert result["found"] is True
    assert result["bans"] == ["steam_7656119", "steam_7656120"]


def test_an_unreadable_ban_list_reports_why(tmp_path, monkeypatch):
    monkeypatch.setattr(moderate, "ban_list_path", lambda: str(tmp_path / "nope.txt"))
    result = moderate.list_bans()
    assert result["found"] is False
    assert result["note"]
