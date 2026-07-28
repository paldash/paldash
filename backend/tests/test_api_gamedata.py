"""
API-level wiring for friendly names.

The resolver is tested directly in test_gamedata.py; these check that the
endpoints actually apply it, which is a separate thing to get wrong.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
import savecache


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def fake_parse(monkeypatch):
    """Stand in for a parsed world so no save file is needed."""
    data = {
        "items": [
            {"itemId": "AIcore", "count": 12},
            {"itemId": "Wood", "count": 8000},
            {"itemId": "TotallyUnknownThing", "count": 1},
        ],
        "containers": {"c1": []},
        "pals": [
            {
                "instanceId": "i1",
                "speciesId": "Sheepball",
                "level": 12,
                "passiveSkills": ["PAL_ALLAttack_up2"],
                "ownerUid": "abc",
            },
            {
                "instanceId": "i2",
                "speciesId": "BOSS_Anubis",
                "level": 40,
                "passiveSkills": [],
                "ownerUid": "abc",
            },
        ],
        "mapObjects": [{"objectId": "PalBoxV2", "x": 1, "y": 2, "category": "palbox"}],
    }
    monkeypatch.setattr(savecache, "get_data", lambda: data)
    monkeypatch.setattr(savecache, "get_section", lambda name: data.get(name, []))
    return data


# ─── Items ───────────────────────────────────────────────────────


def test_items_endpoint_resolves_names(client, fake_parse):
    body = client.get("/api/items").json()
    by_id = {row["itemId"]: row for row in body["items"]}

    assert by_id["AIcore"]["name"] == "AI Core"
    assert by_id["AIcore"]["known"] is True
    assert by_id["AIcore"]["maxStack"] > 0
    assert body["namesResolved"] is True


def test_items_endpoint_falls_back_for_unknown_ids(client, fake_parse):
    body = client.get("/api/items").json()
    row = next(r for r in body["items"] if r["itemId"] == "TotallyUnknownThing")
    assert row["name"] == "Totally Unknown Thing"
    assert row["known"] is False


def test_items_endpoint_keeps_counts_and_totals(client, fake_parse):
    body = client.get("/api/items").json()
    assert body["totalCount"] == 8013
    assert body["itemTypes"] == 3
    assert next(r for r in body["items"] if r["itemId"] == "Wood")["count"] == 8000


# ─── Pals ────────────────────────────────────────────────────────


def test_pals_endpoint_resolves_species_and_passives(client, fake_parse):
    rows = client.get("/api/pals").json()
    by_instance = {r["instanceId"]: r for r in rows}

    lamball = by_instance["i1"]
    assert lamball["speciesName"] == "Lamball"
    assert lamball["elements"]
    assert lamball["passiveSkillNames"] == ["Ferocious"]

    # The alpha keeps its prefix in the raw ID but resolves to the species.
    assert by_instance["i2"]["speciesName"] == "Anubis"


def test_pals_endpoint_preserves_raw_fields(client, fake_parse):
    rows = client.get("/api/pals").json()
    assert {r["speciesId"] for r in rows} == {"Sheepball", "BOSS_Anubis"}
    assert next(r for r in rows if r["instanceId"] == "i2")["level"] == 40


# ─── Map objects ─────────────────────────────────────────────────


def test_mapobjects_endpoint_adds_structure_names(client, fake_parse):
    rows = client.get("/api/mapobjects").json()
    assert rows[0]["name"] == "Palbox"
    assert rows[0]["x"] == 1, "original fields must survive enrichment"


# ─── Static world data ───────────────────────────────────────────


def test_fast_travel_endpoint(client):
    body = client.get("/api/world/fasttravel").json()
    assert len(body["points"]) == 174
    names = {p["name"] for p in body["points"]}
    assert "Hill of Beginnings" in names
    assert all("x" in p and "y" in p for p in body["points"])


def test_reference_endpoint_exposes_exact_totals(client):
    body = client.get("/api/world/reference").json()
    assert body["totals"]["technologyPoints"] == 1413
    assert body["totals"]["ancientTechnologyPoints"] == 185
    assert body["workSuitability"]


def test_health_reports_game_data_availability(client, monkeypatch):
    monkeypatch.setattr(main.savecache, "status", lambda: {})
    monkeypatch.setattr(main.lifecycle, "status", lambda: {})
    monkeypatch.setattr(main, "find_world_dirs", lambda: [])
    monkeypatch.setattr(main, "get_default_world_dir", lambda: None)
    body = client.get("/api/health").json()
    assert body["gameData"] is True


def test_world_endpoints_fail_gracefully_without_data(client, monkeypatch, tmp_path):
    import gamedata

    monkeypatch.setattr(gamedata, "DATA_PATH", str(tmp_path / "gone.json.gz"))
    gamedata._reset_cache()
    try:
        assert client.get("/api/world/fasttravel").status_code == 503
    finally:
        gamedata._reset_cache()
