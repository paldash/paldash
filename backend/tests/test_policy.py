"""
Access policy.

The security property under test: the environment is a *ceiling*. An operator who
sets SECURITY_LEVEL=readonly in their compose file must not be able to have that
raised from the web UI, even by an admin session — which is what makes a
compromised admin cookie survivable.
"""

from __future__ import annotations

import json
import os

import pytest

import policy


@pytest.fixture
def policy_file(tmp_path, monkeypatch):
    path = tmp_path / "policy.json"
    monkeypatch.setattr(policy, "POLICY_FILE", str(path))
    monkeypatch.delenv("SECURITY_LEVEL", raising=False)
    policy._cache = None
    return path


# ─── Defaults ────────────────────────────────────────────────────


def test_default_level_is_safe(policy_file):
    assert policy.load_policy()["securityLevel"] == "safe"


def test_default_hides_loot_from_guests(policy_file):
    visibility = policy.load_policy()["guestVisibility"]
    assert visibility["chests"] is False
    assert visibility["items"] is False
    assert visibility["serverStatus"] is True


def test_unknown_env_level_falls_back_to_safe(policy_file, monkeypatch):
    monkeypatch.setenv("SECURITY_LEVEL", "banana")
    policy._cache = None
    assert policy.load_policy()["securityLevel"] == "safe"


# ─── Capability ladder ───────────────────────────────────────────


@pytest.mark.parametrize(
    "level,expected",
    [
        ("readonly", set()),
        ("safe", {"backup.manage", "settings.write", "save.sort.stackables"}),
        (
            "full",
            {
                "backup.manage",
                "settings.write",
                "save.sort.stackables",
                "save.sort.all",
                "save.edit.full",
            },
        ),
    ],
)
def test_capability_ladder(policy_file, monkeypatch, level, expected):
    monkeypatch.setenv("SECURITY_LEVEL", level)
    policy._cache = None
    assert set(policy.allowed_capabilities()) == expected


def test_readonly_blocks_every_write(policy_file, monkeypatch):
    monkeypatch.setenv("SECURITY_LEVEL", "readonly")
    policy._cache = None
    for capability in ("backup.manage", "settings.write", "save.sort.stackables",
                       "save.sort.all", "save.edit.full"):
        with pytest.raises(PermissionError):
            policy.require_capability(capability)


def test_safe_level_blocks_full_editor_and_equipment_sort(policy_file, monkeypatch):
    monkeypatch.setenv("SECURITY_LEVEL", "safe")
    policy._cache = None
    policy.require_capability("save.sort.stackables")  # allowed
    with pytest.raises(PermissionError, match="save.sort.all"):
        policy.require_capability("save.sort.all")
    with pytest.raises(PermissionError, match="save.edit.full"):
        policy.require_capability("save.edit.full")


# ─── The ceiling ─────────────────────────────────────────────────


def test_stored_level_above_ceiling_is_clamped(policy_file, monkeypatch):
    """Someone edits policy.json by hand to 'full'; the env still wins."""
    policy_file.write_text(json.dumps({"securityLevel": "full"}))
    monkeypatch.setenv("SECURITY_LEVEL", "readonly")
    policy._cache = None

    assert policy.load_policy()["securityLevel"] == "readonly"
    assert policy.allowed_capabilities() == []


def test_cannot_raise_level_above_ceiling_from_the_ui(policy_file, monkeypatch):
    monkeypatch.setenv("SECURITY_LEVEL", "safe")
    policy._cache = None

    with pytest.raises(ValueError, match="cannot be enabled from the web UI"):
        policy.save_policy({"securityLevel": "full"})

    assert policy.load_policy()["securityLevel"] == "safe"


def test_can_lower_level_from_the_ui(policy_file, monkeypatch):
    """Tightening is always allowed — only loosening is capped."""
    monkeypatch.setenv("SECURITY_LEVEL", "full")
    policy._cache = None

    policy.save_policy({"securityLevel": "readonly"})
    assert policy.load_policy()["securityLevel"] == "readonly"


def test_unknown_level_is_rejected(policy_file):
    with pytest.raises(ValueError, match="Unknown security level"):
        policy.save_policy({"securityLevel": "root"})


# ─── Persistence ─────────────────────────────────────────────────


def test_visibility_changes_persist(policy_file):
    policy.save_policy({"guestVisibility": {"chests": True}})
    policy._cache = None
    assert policy.load_policy()["guestVisibility"]["chests"] is True


def test_unknown_visibility_keys_are_ignored(policy_file):
    policy.save_policy({"guestVisibility": {"chests": True, "bogusKey": True}})
    policy._cache = None
    stored = policy.load_policy()["guestVisibility"]
    assert "bogusKey" not in stored
    assert set(stored) == set(policy.GUEST_VISIBILITY_KEYS)


def test_non_boolean_visibility_values_are_ignored(policy_file):
    policy.save_policy({"guestVisibility": {"chests": "yes please"}})
    policy._cache = None
    assert policy.load_policy()["guestVisibility"]["chests"] is False


def test_corrupt_policy_file_falls_back_to_defaults(policy_file):
    policy_file.write_text("{not json at all")
    policy._cache = None
    assert policy.load_policy()["securityLevel"] == "safe"


def test_policy_write_is_atomic(policy_file):
    policy.save_policy({"securityLevel": "readonly"})
    leftovers = list(policy_file.parent.glob("*.tmp"))
    assert leftovers == []


def test_load_policy_returns_a_copy(policy_file):
    """Callers must not be able to mutate the cached policy in place."""
    first = policy.load_policy()
    first["securityLevel"] = "full"
    assert policy.load_policy()["securityLevel"] == "safe"


def test_describe_exposes_the_ceiling(policy_file, monkeypatch):
    monkeypatch.setenv("SECURITY_LEVEL", "safe")
    policy._cache = None
    described = policy.describe()
    assert described["envCeiling"] == "safe"
    assert {lvl["id"] for lvl in described["levels"]} == set(policy.SECURITY_LEVELS)
    assert described["visibilityKeys"] == list(policy.GUEST_VISIBILITY_KEYS)


# ─── Discovery visibility ────────────────────────────────────────
#
# Whether a Player sees the effigies they have NOT found is a taste question,
# not a security one — which is exactly why it is the operator's choice rather
# than a constant. What must not be configurable is *where* the filtering
# happens: server-side, or the answers leak in the network tab.


def test_discovery_visibility_defaults_to_trusted_and_above(monkeypatch):
    monkeypatch.delenv("DISCOVERY_VISIBILITY", raising=False)
    assert policy.default_policy()["discoveryVisibility"] == "trusted"


def test_the_threshold_can_be_any_role_not_just_a_capability():
    """
    Finer-grained than a capability check on purpose: a casual server may want
    Players to see everything, a competitive one may want Moderator only, and
    neither maps onto an existing capability.
    """
    assert policy.discovery_choices() == (
        "everyone", "readonly", "player", "trusted", "moderator", "admin", "owner",
        "nobody",
    )


@pytest.mark.parametrize("role,level,expected", [
    # everyone / nobody ignore rank entirely
    ("guest", "everyone", True), ("owner", "nobody", False),
    # a role threshold means "this rank and above"
    ("player", "trusted", False), ("trusted", "trusted", True),
    ("moderator", "trusted", True), ("readonly", "player", False),
    ("player", "player", True), ("guest", "readonly", False),
    ("admin", "moderator", True), ("trusted", "moderator", False),
])
def test_role_thresholds(role, level, expected):
    assert policy.may_see_undiscovered(role, level) is expected


def test_an_unknown_role_never_clears_the_threshold():
    """Fail closed: an unrecognised role must not be handed the map."""
    assert policy.may_see_undiscovered("wizard", "readonly") is False


@pytest.mark.parametrize("level", ("everyone", "player", "trusted", "owner", "nobody"))
def test_every_level_round_trips(level, tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "POLICY_FILE", str(tmp_path / "policy.json"))
    policy._cache = None

    saved = policy.save_policy({"discoveryVisibility": level})
    assert saved["discoveryVisibility"] == level

    policy._cache = None
    assert policy.load_policy()["discoveryVisibility"] == level


def test_an_unknown_level_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, "POLICY_FILE", str(tmp_path / "policy.json"))
    policy._cache = None
    with pytest.raises(ValueError, match="Unknown discovery visibility"):
        policy.save_policy({"discoveryVisibility": "sometimes"})


def test_a_bad_environment_value_falls_back_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("DISCOVERY_VISIBILITY", "yes-please")
    assert policy.default_policy()["discoveryVisibility"] == policy.DEFAULT_DISCOVERY


def test_the_levels_are_described_for_the_ui():
    described = policy.describe()["discoveryLevels"]
    assert [d["id"] for d in described] == list(policy.discovery_choices())
    assert all(d["label"] and d["description"] for d in described)
