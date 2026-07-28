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
