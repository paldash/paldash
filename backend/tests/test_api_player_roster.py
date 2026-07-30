"""
The merged player roster through the HTTP API.

The point of this endpoint is the population it covers. The live REST list knows
only who is connected right now, which is the wrong set for the thing an
operator actually wants from a roster: the person who logged off an hour ago and
needs a dashboard account. So the **save** is the base list and online status is
an annotation — and the tests that matter are the ones proving offline players
survive, and that a game server which is down degrades rather than empties it.

Two things are deliberately narrower than they look:

- account linkage is only reported to callers who could act on it
- privacy removes a player entirely rather than greying them, because a greyed
  row still discloses that the person exists and plays here
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import accounts
import gameapi
import main
import privacy
import savecache
import viewcache

PASSWORD = "correct-horse-battery-staple"

ALICE_UID = "aaaaaaaa-0000-0000-0000-000000000001"
BOB_UID = "bbbbbbbb-0000-0000-0000-000000000002"
CAROL_UID = "cccccccc-0000-0000-0000-000000000003"

PLAYERS = [
    {"uid": ALICE_UID, "name": "Alice", "level": 40},
    {"uid": BOB_UID, "name": "Bob", "level": 12},
    {"uid": CAROL_UID, "name": "Carol", "level": 7},
]

# Only Bob is connected. Note the REST id is spelled differently from the save's
# uid — dashless and uppercase — which is exactly why `restUserId` is carried
# through rather than reconstructed from the save.
ONLINE = [{"userId": BOB_UID.replace("-", "").upper(), "name": "Bob", "ping": 42.0}]


@pytest.fixture
def client(fresh_db, monkeypatch):
    monkeypatch.setattr(savecache, "get_section",
                        lambda name: {"players": PLAYERS}.get(name, []))
    monkeypatch.setattr(main, "get_players", lambda: list(PLAYERS))
    monkeypatch.setattr(gameapi, "players", lambda: list(ONLINE))
    monkeypatch.setattr(gameapi, "configured", lambda: True)
    viewcache.clear()
    return TestClient(main.app)


def sign_in(client: TestClient, username: str) -> dict:
    res = client.post("/api/auth/login",
                      json={"username": username, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()


def auth(session: dict) -> dict:
    return {"X-Session-Token": session["token"]}


@pytest.fixture
def owner(client):
    accounts.create_user("owner", PASSWORD, role="owner")
    return sign_in(client, "owner")


@pytest.fixture
def moderator(client):
    accounts.create_user("mod", PASSWORD, role="moderator")
    return sign_in(client, "mod")


def roster(client, session) -> dict:
    res = client.get("/api/players/roster", headers=auth(session))
    assert res.status_code == 200, res.text
    return res.json()


# ─── The population ───────────────────────────────────────


def test_offline_players_are_included(client, owner):
    """The whole reason this endpoint exists."""
    names = {p["name"] for p in roster(client, owner)["players"]}
    assert names == {"Alice", "Bob", "Carol"}


def test_online_status_is_an_annotation_not_a_filter(client, owner):
    body = roster(client, owner)
    assert body["onlineCount"] == 1
    by_name = {p["name"]: p for p in body["players"]}
    assert by_name["Bob"]["online"] is True
    assert by_name["Alice"]["online"] is False
    assert by_name["Carol"]["online"] is False


def test_online_players_sort_first(client, owner):
    """An operator scanning for someone to kick should not have to hunt."""
    players = roster(client, owner)["players"]
    assert players[0]["name"] == "Bob"


def test_rest_user_id_is_carried_through_not_reconstructed(client, owner):
    """
    Kick and ban take the REST id, which is spelled differently from the save's
    uid. Rebuilding it from the uid would work until it didn't.
    """
    bob = next(p for p in roster(client, owner)["players"] if p["name"] == "Bob")
    assert bob["restUserId"] == ONLINE[0]["userId"]
    assert bob["restUserId"] != bob["uid"]


def test_ping_is_reported_for_online_players_only(client, owner):
    by_name = {p["name"]: p for p in roster(client, owner)["players"]}
    assert by_name["Bob"]["ping"] == 42.0
    assert by_name["Alice"]["ping"] is None


# ─── A game server that is down ───────────────────────────


def test_an_unreachable_game_server_degrades_rather_than_empties(client, owner, monkeypatch):
    """
    "Nobody is known to be online" is not "there are no players". The save half
    of this view is still the useful half, and losing it because the game is
    down would be the wrong trade.
    """
    def boom():
        raise ConnectionError("game server unreachable")

    monkeypatch.setattr(gameapi, "players", boom)
    body = roster(client, owner)
    assert len(body["players"]) == 3
    assert body["onlineCount"] == 0
    assert all(p["online"] is False for p in body["players"])


def test_reachability_is_reported_so_the_ui_can_explain_itself(client, owner, monkeypatch):
    monkeypatch.setattr(gameapi, "players", lambda: [])
    monkeypatch.setattr(gameapi, "configured", lambda: False)
    assert roster(client, owner)["gameApiReachable"] is False


# ─── Account linkage ──────────────────────────────────────


def test_linkage_is_reported_to_an_owner(client, owner):
    accounts.create_user("alice", PASSWORD, role="player", steam_uid=ALICE_UID)
    viewcache.clear()
    by_name = {p["name"]: p for p in roster(client, owner)["players"]}
    assert by_name["Alice"]["hasAccount"] is True
    assert by_name["Alice"]["accountUsername"] == "alice"
    assert by_name["Bob"]["hasAccount"] is False


def test_linkage_is_withheld_from_callers_who_could_not_act_on_it(client, moderator):
    """
    Only Owner holds `users.manage`. "Which of your players has a dashboard
    login" is not a roster fact and does not belong in a Moderator's view.
    """
    accounts.create_user("alice", PASSWORD, role="player", steam_uid=ALICE_UID)
    body = roster(client, moderator)
    assert body["canManageAccounts"] is False
    assert all("hasAccount" not in p for p in body["players"])


def test_linkage_matches_on_a_normalised_uid(client, owner):
    """
    `accounts` stores the uid dash-stripped and lowercased; the save uses dashed
    lowercase. Comparing them raw matches nothing, and fails silently.
    """
    accounts.create_user("carol", PASSWORD, role="player",
                         steam_uid=CAROL_UID.replace("-", "").upper())
    viewcache.clear()
    by_name = {p["name"]: p for p in roster(client, owner)["players"]}
    assert by_name["Carol"]["hasAccount"] is True


# ─── Access ───────────────────────────────────────────────


def test_a_guest_cannot_read_the_roster(client):
    assert client.get("/api/players/roster").status_code == 401


def test_a_player_role_cannot_read_the_roster(client):
    """It is VIEW_DETAIL, like the plain player list — the same population."""
    accounts.create_user("pleb", PASSWORD, role="player", steam_uid=ALICE_UID)
    session = sign_in(client, "pleb")
    assert client.get("/api/players/roster", headers=auth(session)).status_code == 403


# ─── Privacy ──────────────────────────────────────────────


def test_a_hidden_player_is_absent_from_a_peer_rather_than_greyed(client):
    """
    A greyed row still says the person exists and plays here, which is most of
    what they asked to conceal.

    Bob is a **peer** — the same role as Alice. Equal rank is concealed, because
    peers are exactly who a privacy setting is for.

    Both are Trusted rather than Player, and that is forced rather than
    arbitrary: this endpoint is VIEW_DETAIL, which a Player does not have, so
    the peer case can only be observed here from Trusted upwards.
    """
    accounts.create_user("alice", PASSWORD, role="trusted", steam_uid=ALICE_UID)
    accounts.create_user("bob", PASSWORD, role="trusted", steam_uid=BOB_UID)
    privacy.set_mode("alice", "player")
    privacy.set_mode("bob", "off")
    viewcache.clear()

    session = sign_in(client, "bob")
    names = {p["name"] for p in roster(client, session)["players"]}
    assert "Alice" not in names
    assert "Bob" in names


def test_privacy_never_applies_upward(client):
    """
    The other half of `hidden ⟺ viewer_rank <= hider_rank`, and the half that is
    easy to get backwards — an earlier version of this test made the viewer a
    Trusted player and expected concealment, which would have meant a Player
    could hide from everyone above them.
    """
    accounts.create_user("alice", PASSWORD, role="trusted", steam_uid=ALICE_UID)
    accounts.create_user("bob", PASSWORD, role="moderator", steam_uid=BOB_UID)
    privacy.set_mode("alice", "player")
    privacy.set_mode("bob", "off")
    viewcache.clear()

    session = sign_in(client, "bob")
    names = {p["name"] for p in roster(client, session)["players"]}
    assert "Alice" in names


def test_staff_still_see_a_hidden_player(client, owner):
    """`hidden ⟺ viewer_rank <= hider_rank` — nobody hides from staff."""
    accounts.create_user("alice", PASSWORD, role="player", steam_uid=ALICE_UID)
    privacy.set_mode("alice", "player")
    viewcache.clear()
    names = {p["name"] for p in roster(client, owner)["players"]}
    assert "Alice" in names
