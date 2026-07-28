"""
Shared fixtures.

Two tiers of test live here:

  * Unit tests, which run anywhere and never touch a real save. These are the
    ones that must never be allowed to rot — they cover the corruption guard,
    the path handling and the settings parser.
  * Integration tests, which need `refworld/` (a real 1.0 world, gitignored
    because it contains real Steam IDs and player names). They skip cleanly when
    it is absent, so CI stays green without shipping anyone's save file.
"""

from __future__ import annotations

import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


@pytest.fixture
def refworld() -> str:
    """The real reference world, or skip."""
    path = os.path.join(PROJECT_ROOT, "refworld")
    if not os.path.exists(os.path.join(path, "Level.sav")):
        pytest.skip("refworld/ not present — integration test skipped")
    return path


@pytest.fixture
def level_sav(refworld: str) -> str:
    return os.path.join(refworld, "Level.sav")


@pytest.fixture
def palsav_available() -> None:
    """Skip unless the Oodle-capable parser is installed."""
    try:
        import palsav  # noqa: F401
    except ImportError:
        pytest.skip("palsav not installed — see backend/requirements.txt")


@pytest.fixture(autouse=True)
def _reset_policy_cache():
    """
    policy.py memoises the loaded policy in a module global. Without clearing it
    between tests, the first test to load a policy pins it for the whole session.
    """
    try:
        import policy
    except ImportError:
        yield
        return

    policy._cache = None
    yield
    policy._cache = None


@pytest.fixture
def stopped_server(monkeypatch):
    """
    Force the safety module to report a provably-stopped server.

    Note these are module-level constants captured at import time, so patching
    os.environ would do nothing — the attributes themselves have to be patched.
    """
    import safety

    monkeypatch.setattr(safety, "SAVE_READ_ONLY", False)
    monkeypatch.setattr(safety, "ALLOW_UNVERIFIED_EDITS", False)
    monkeypatch.setattr(
        safety, "_probe_rest_api", lambda: safety.Signal("rest_api", "stopped", "test")
    )
    monkeypatch.setattr(
        safety, "_probe_tcp", lambda: safety.Signal("tcp_port", "stopped", "test")
    )
    monkeypatch.setattr(
        safety,
        "_probe_save_activity",
        lambda: safety.Signal("save_activity", "stopped", "test"),
    )
    monkeypatch.setattr(
        safety, "_probe_process", lambda: safety.Signal("process", "unknown", "test")
    )
    return safety
