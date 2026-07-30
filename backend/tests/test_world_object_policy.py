"""
Who may see which static world-object categories.

The operator's dial, per category, using the same role-threshold vocabulary as
`discoveryVisibility` — because it is the same kind of question: whether handing
players a complete ore map is a convenience or the removal of the game depends on
how the server is run.

Two properties matter more than the filtering itself:

  * **A restricted category is not listed.** A name and a count in a legend has
    already said what is out there and roughly how much of it.
  * **The count is right for the viewer.** `inView` drives a "showing 500 of
    3,000" message, so it has to be computed *after* the policy filter or the UI
    promises points that zooming in never reveals.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import accounts
import main
import policy as policy_module
import worldobjects

PASSWORD = "correct-horse-battery-staple"

GROUPS = {
    "ore": {
        "label": "Ore & mineral nodes", "count": 2, "byClass": {"Rock": 2},
        "objects": [
            {"cls": "Rock", "x": 0.0, "y": 0.0, "z": 0.0, "landmass": "palpagos"},
            {"cls": "Rock", "x": 10.0, "y": 10.0, "z": 0.0, "landmass": "palpagos"},
        ],
    },
    "treasure": {
        "label": "Treasure chests", "count": 3, "byClass": {"Box": 3},
        "objects": [
            {"cls": "Box", "x": 1.0, "y": 1.0, "z": 0.0, "landmass": "palpagos"},
            {"cls": "Box", "x": 2.0, "y": 2.0, "z": 0.0, "landmass": "palpagos"},
            {"cls": "Box", "x": 3.0, "y": 3.0, "z": 0.0, "landmass": "palpagos"},
        ],
    },
}


@pytest.fixture
def client(fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(policy_module, "POLICY_FILE", str(tmp_path / "policy.json"))
    monkeypatch.setenv("SECURITY_LEVEL", "full")
    monkeypatch.delenv("WORLD_OBJECT_VISIBILITY", raising=False)
    policy_module._cache = None
    monkeypatch.setattr(worldobjects, "_data",
                        {"groups": GROUPS, "cellsParsed": 1, "skipped": {}})
    monkeypatch.setattr(worldobjects, "_index", None)
    yield TestClient(main.app)
    worldobjects.reset_for_tests()
    policy_module._cache = None


def sign_in(client, username):
    res = client.post("/api/auth/login",
                      json={"username": username, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return {"X-Session-Token": res.json()["token"]}


@pytest.fixture
def cast(client):
    accounts.create_user("plainplayer", PASSWORD, role="player")
    accounts.create_user("trustedone", PASSWORD, role="trusted")
    accounts.create_user("owner1", PASSWORD, role="owner")
    return {
        "player": sign_in(client, "plainplayer"),
        "trusted": sign_in(client, "trustedone"),
        "owner": sign_in(client, "owner1"),
    }


def set_level(client, cast, category, level):
    res = client.post(
        "/api/policy",
        json={"worldObjectVisibility": {category: level}},
        headers=cast["owner"],
    )
    assert res.status_code == 200, res.text
    return res.json()


# ─── The default ─────────────────────────────────────────


def test_chests_default_tighter_than_terrain(client, cast):
    """
    Chests are the game's exploration reward, so a full map of them is the closest
    thing here to a spoiler. Ore is terrain. The defaults reflect that rather than
    treating every category the same.
    """
    body = client.get("/api/world/objects/categories", headers=cast["player"]).json()
    listed = {c["id"] for c in body["categories"]}
    assert listed == {"ore"}
    assert body["restrictedCategories"] == ["treasure"]

    body = client.get("/api/world/objects/categories", headers=cast["trusted"]).json()
    assert {c["id"] for c in body["categories"]} == {"ore", "treasure"}


# ─── Filtering ───────────────────────────────────────────


def test_a_restricted_category_is_absent_from_the_legend(client, cast):
    set_level(client, cast, "ore", "moderator")
    body = client.get("/api/world/objects/categories", headers=cast["player"]).json()
    assert [c["id"] for c in body["categories"]] == []
    assert body["restrictedCategories"] == ["ore", "treasure"]


def test_the_total_is_recomputed_for_the_viewer(client, cast):
    """
    Otherwise the legend says "of 5 in the world" while listing categories that add
    up to 2, which reads as a bug and is really a leak of the hidden count.
    """
    player = client.get("/api/world/objects/categories", headers=cast["player"]).json()
    assert player["objects"] == 2          # ore only

    trusted = client.get("/api/world/objects/categories", headers=cast["trusted"]).json()
    assert trusted["objects"] == 5         # both


def test_points_from_a_restricted_category_are_not_returned(client, cast):
    body = client.get("/api/world/objects", headers=cast["player"]).json()
    assert {p["category"] for p in body["points"]} == {"ore"}
    assert body["inView"] == 2


def test_in_view_counts_only_what_the_viewer_may_see(client, cast):
    """
    The count is taken after the policy filter, not before. A pre-filter count
    would drive a "showing 2 of 5 — zoom in" message about points that do not
    exist for this viewer.
    """
    assert client.get("/api/world/objects", headers=cast["player"]).json()["inView"] == 2
    assert client.get("/api/world/objects", headers=cast["trusted"]).json()["inView"] == 5


def test_asking_for_a_restricted_category_by_name_returns_empty_not_403(client, cast):
    """
    A refusal would confirm the category exists and is populated, which is most of
    what restricting it was for. Empty is the same answer as "nothing here".
    """
    res = client.get("/api/world/objects?category=treasure", headers=cast["player"])
    assert res.status_code == 200
    assert res.json()["points"] == []
    assert res.json()["restricted"] is True


def test_a_bounding_box_does_not_bypass_the_policy(client, cast):
    res = client.get(
        "/api/world/objects?minX=-100&minY=-100&maxX=100&maxY=100",
        headers=cast["player"],
    )
    assert {p["category"] for p in res.json()["points"]} == {"ore"}


def test_a_kind_filter_does_not_bypass_the_policy(client, cast):
    res = client.get("/api/world/objects?kinds=Box", headers=cast["player"]).json()
    assert res["points"] == []


# ─── Setting it ──────────────────────────────────────────


def test_everyone_opens_a_category_to_guests(client, cast):
    set_level(client, cast, "treasure", "everyone")
    body = client.get("/api/world/objects/categories").json()
    assert {c["id"] for c in body["categories"]} == {"ore", "treasure"}


def test_nobody_hides_a_category_from_every_role_including_owners(client, cast):
    """
    `nobody` means nobody. An Owner exemption would make the setting unable to
    express "this server does not use this data at all".
    """
    set_level(client, cast, "ore", "nobody")
    for who in ("player", "trusted", "owner"):
        body = client.get("/api/world/objects/categories", headers=cast[who]).json()
        assert "ore" not in {c["id"] for c in body["categories"]}, who


def test_only_a_policy_manager_can_change_it(client, cast):
    res = client.post(
        "/api/policy", json={"worldObjectVisibility": {"ore": "nobody"}},
        headers=cast["player"],
    )
    assert res.status_code == 403


def test_an_unknown_level_is_refused(client, cast):
    res = client.post(
        "/api/policy", json={"worldObjectVisibility": {"ore": "sometimes"}},
        headers=cast["owner"],
    )
    assert res.status_code == 400
    assert "sometimes" in res.json()["detail"]


def test_a_change_is_audited(client, cast):
    import db

    set_level(client, cast, "ore", "moderator")
    rows = db.connect().execute(
        "SELECT * FROM audit_log WHERE action = 'policy.update'"
    ).fetchall()
    assert any("worldObjectVisibility" in (r["detail"] or "") for r in rows)


def test_setting_one_category_leaves_the_others_alone(client, cast):
    body = set_level(client, cast, "ore", "moderator")
    assert body["worldObjectVisibility"]["ore"] == "moderator"
    # The chest default survived a write that never mentioned it.
    assert body["worldObjectVisibility"]["treasure"] == "trusted"


# ─── Configuration ───────────────────────────────────────


def test_the_environment_can_set_levels(monkeypatch, tmp_path):
    monkeypatch.setattr(policy_module, "POLICY_FILE", str(tmp_path / "policy.json"))
    monkeypatch.setenv("WORLD_OBJECT_VISIBILITY", "ore:moderator,treasure:everyone")
    policy_module._cache = None
    try:
        levels = policy_module.load_policy()["worldObjectVisibility"]
        assert levels["ore"] == "moderator"
        assert levels["treasure"] == "everyone"
    finally:
        policy_module._cache = None


def test_a_malformed_environment_entry_is_ignored_not_fatal(monkeypatch, tmp_path):
    monkeypatch.setattr(policy_module, "POLICY_FILE", str(tmp_path / "policy.json"))
    monkeypatch.setenv("WORLD_OBJECT_VISIBILITY", "ore:nonsense,,treasure:nobody")
    policy_module._cache = None
    try:
        levels = policy_module.load_policy()["worldObjectVisibility"]
        assert levels["treasure"] == "nobody"
        # The bad entry falls back to the default rather than taking the server down.
        assert levels.get("ore", "everyone") == "everyone"
    finally:
        policy_module._cache = None


def test_an_unconfigured_category_defaults_to_everyone():
    assert policy_module.world_object_level("something_new", {}) == "everyone"


def test_a_stored_policy_does_not_erase_a_new_categorys_default(monkeypatch, tmp_path):
    """
    Stored settings merge over the defaults. A policy.json written before a
    category existed must not make that category unconfigured-and-missing.
    """
    import json

    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"worldObjectVisibility": {"ore": "moderator"}}))
    monkeypatch.setattr(policy_module, "POLICY_FILE", str(path))
    policy_module._cache = None
    try:
        levels = policy_module.load_policy()["worldObjectVisibility"]
        assert levels["ore"] == "moderator"     # stored
        assert levels["treasure"] == "trusted"  # default survived
    finally:
        policy_module._cache = None


# ─── The gap this closed ─────────────────────────────────


def test_discovery_visibility_is_settable_through_the_api(client, cast):
    """
    It was not. `save_policy` handled `discoveryVisibility` and the endpoint's
    request model omitted the field, so Pydantic dropped it silently — the dial was
    documented as an Owner setting and could only be changed by hand-editing
    policy.json.
    """
    res = client.post(
        "/api/policy", json={"discoveryVisibility": "moderator"}, headers=cast["owner"]
    )
    assert res.status_code == 200
    assert res.json()["discoveryVisibility"] == "moderator"

    fresh = client.get("/api/policy", headers=cast["owner"]).json()
    assert fresh["discoveryVisibility"] == "moderator"
