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
def client(fresh_db):
    return TestClient(main.app)


@pytest.fixture
def staff(client):
    """
    A signed-in Owner.

    `/api/items` and `/api/pals` moved from open-to-any-caller to `VIEW_SELF`
    when Pal and item views gained per-caller scoping — a plain Player must be
    able to read their own palbox, and nobody unauthenticated should read either.
    These tests are about naming and enrichment, so they use the widest role and
    let `test_api_scoping.py` cover who sees what.
    """
    import accounts

    accounts.create_user("owner", "correct-horse-battery-staple", role="owner")
    res = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "correct-horse-battery-staple"},
    )
    assert res.status_code == 200, res.text
    return {"X-Session-Token": res.json()["token"]}


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


def test_items_endpoint_resolves_names(client, fake_parse, staff):
    body = client.get("/api/items", headers=staff).json()
    by_id = {row["itemId"]: row for row in body["items"]}

    assert by_id["AIcore"]["name"] == "AI Core"
    assert by_id["AIcore"]["known"] is True
    assert by_id["AIcore"]["maxStack"] > 0
    assert body["namesResolved"] is True


def test_items_endpoint_falls_back_for_unknown_ids(client, fake_parse, staff):
    body = client.get("/api/items", headers=staff).json()
    row = next(r for r in body["items"] if r["itemId"] == "TotallyUnknownThing")
    assert row["name"] == "Totally Unknown Thing"
    assert row["known"] is False


def test_items_endpoint_keeps_counts_and_totals(client, fake_parse, staff):
    body = client.get("/api/items", headers=staff).json()
    assert body["totalCount"] == 8013
    assert body["itemTypes"] == 3
    assert next(r for r in body["items"] if r["itemId"] == "Wood")["count"] == 8000


# ─── Pals ────────────────────────────────────────────────────────


def test_pals_endpoint_resolves_species_and_passives(client, fake_parse, staff):
    rows = client.get("/api/pals", headers=staff).json()
    by_instance = {r["instanceId"]: r for r in rows}

    lamball = by_instance["i1"]
    assert lamball["speciesName"] == "Lamball"
    assert lamball["elements"]
    assert lamball["passiveSkillNames"] == ["Ferocious"]

    # The alpha keeps its prefix in the raw ID but resolves to the species.
    assert by_instance["i2"]["speciesName"] == "Anubis"


def test_pals_endpoint_preserves_raw_fields(client, fake_parse, staff):
    rows = client.get("/api/pals", headers=staff).json()
    assert {r["speciesId"] for r in rows} == {"Sheepball", "BOSS_Anubis"}
    assert next(r for r in rows if r["instanceId"] == "i2")["level"] == 40


# ─── Map objects ─────────────────────────────────────────────────


def test_mapobjects_endpoint_adds_structure_names(client, fake_parse, staff):
    rows = client.get("/api/mapobjects").json()
    assert rows[0]["name"] == "Palbox"
    assert rows[0]["x"] == 1, "original fields must survive enrichment"


# ─── Static world data ───────────────────────────────────────────


def test_fast_travel_endpoint(client, staff):
    body = client.get("/api/world/fasttravel", headers=staff).json()
    assert len(body["points"]) == 174
    assert body["filtered"] is False
    names = {p["name"] for p in body["points"]}
    assert "Hill of Beginnings" in names
    assert all("x" in p and "y" in p for p in body["points"])


def test_fast_travel_respects_discovery_visibility(client):
    """
    The other half of `discoveryVisibility`, and the half that was missing.

    `/api/world/discoveries` dropped undiscovered points server-side while this
    endpoint returned all 174 to anyone — and the map reads this one whenever
    the discovery call is unavailable. Filtering in one of two endpoints serving
    the same data filters nothing.

    A guest has no character, so nothing is discovered and nothing comes back.
    `filtered` says so rather than leaving an empty list to read as missing data.
    """
    body = client.get("/api/world/fasttravel").json()
    assert body["filtered"] is True
    assert body["points"] == []


def test_reference_endpoint_exposes_exact_totals(client, staff):
    body = client.get("/api/world/reference", headers=staff).json()
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


# ─── Paldeck ──────────────────────────────────────────────


def _paldeck_client():
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app)


def test_every_paldeck_entry_opens_after_the_listing_is_loaded(fresh_db):
    """
    The listing must be requested *first*, because that is what broke.

    `_paldeck_entries` and `_paldeck_siblings` are both derived from the same two
    bundled files, and an earlier `viewcache.per_files` keyed on the paths alone
    — so they shared a cache entry and the second caller was handed the first's
    value. Every detail request returned 500, but only once the listing had been
    loaded, so testing a single entry in isolation passed.
    """
    import accounts
    client = _paldeck_client()
    accounts.create_user("owner", "correct-horse-battery-staple", role="owner")
    token = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "correct-horse-battery-staple"},
    ).json()["token"]
    headers = {"X-Session-Token": token}

    listing = client.get("/api/world/paldeck", headers=headers)
    assert listing.status_code == 200, listing.text
    entries = listing.json()["pals"]
    assert entries, "no Paldeck entries; the bundled game data is missing"

    failures = []
    for entry in entries:
        res = client.get(f"/api/world/paldeck/{entry['id']}", headers=headers)
        if res.status_code != 200:
            failures.append((entry["id"], res.status_code))
    assert not failures, f"{len(failures)} of {len(entries)} entries failed: {failures[:5]}"
