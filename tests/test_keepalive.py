"""The keepalive starts a session; the interesting part is when it declines to.

Every fire costs real quota, and `open` is asynchronous — it returns long before
the session it started has finished. So the debounce is not a nicety: without
it, every tick inside the twenty-minute margin starts another session, and a
lapsed account would spend four of them before the first one landed.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clawdtv import keepalive as ka  # noqa: E402
from clawdtv.config import Account  # noqa: E402
from clawdtv.creds import Credentials  # noqa: E402

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def credentials(
    expires_in_s: float | None = 3600,
    has_refresh: bool = True,
    refresh_in_s: float | None = 20 * 24 * 3600,
) -> Credentials:
    return Credentials(
        access_token="tok",
        expires_at=None if expires_in_s is None else NOW + timedelta(seconds=expires_in_s),
        subscription_type="max",
        rate_limit_tier="default_claude_max_20x",
        scopes=["user:profile"],
        has_refresh_token=has_refresh,
        refresh_expires_at=None if refresh_in_s is None else NOW + timedelta(seconds=refresh_in_s),
    )


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ka, "STATE_PATH", tmp_path / "keepalive.json")
    monkeypatch.setattr(ka, "LOG_PATH", tmp_path / "keepalive.log")
    return tmp_path


@pytest.fixture
def account() -> Account:
    return Account(label="JOIN", config_dir="/Users/arizilnik/.claude-join")


class FakeOpen:
    """Stands in for /usr/bin/open, recording each invocation."""

    def __init__(self, returncode: int = 0, stderr: str = ""):
        self.calls: list[list[str]] = []
        self.returncode, self.stderr = returncode, stderr

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        return type("P", (), {"returncode": self.returncode, "stderr": self.stderr})()


# --- due() ---------------------------------------------------------------


def test_due_inside_the_margin() -> None:
    assert ka.due(credentials(expires_in_s=ka.MARGIN_S - 1), NOW)


def test_not_due_while_the_token_has_time_left() -> None:
    assert not ka.due(credentials(expires_in_s=ka.MARGIN_S + 1), NOW)


def test_due_long_after_expiry() -> None:
    """A machine back from three days away recovers on its first tick."""
    assert ka.due(credentials(expires_in_s=-3 * 86400), NOW)


def test_not_due_without_a_refresh_token() -> None:
    assert not ka.due(credentials(expires_in_s=-60, has_refresh=False), NOW)


def test_not_due_once_the_refresh_token_has_lapsed() -> None:
    """Claude Code could not refresh either, so the session would burn quota."""
    assert not ka.due(credentials(expires_in_s=-60, refresh_in_s=-1), NOW)


def test_not_due_when_expiry_is_unknown() -> None:
    assert not ka.due(credentials(expires_in_s=None), NOW)


# --- fire() --------------------------------------------------------------


def test_fire_opens_the_app_with_the_accounts_config_dir(account, monkeypatch) -> None:
    opener = FakeOpen()
    monkeypatch.setattr(ka.subprocess, "run", opener)

    status = ka.renew(account)

    assert "started a session for JOIN" in status
    argv = opener.calls[0]
    assert argv[0] == "/usr/bin/open"
    assert "-n" in argv, "each account needs its own instance, not a reused one"
    assert argv[-2:] == ["/Users/arizilnik/.claude-join", "JOIN"]


def test_the_default_account_passes_an_empty_config_dir(monkeypatch) -> None:
    opener = FakeOpen()
    monkeypatch.setattr(ka.subprocess, "run", opener)

    ka.renew(Account(label="PERSONAL", config_dir=""))

    assert opener.calls[0][-2:] == ["", "PERSONAL"]


def test_a_second_tick_does_not_start_a_second_session(account, monkeypatch) -> None:
    """`open` returns before the session finishes, so the token is still stale
    on the next tick. Firing again would spend quota racing itself."""
    opener = FakeOpen()
    monkeypatch.setattr(ka.subprocess, "run", opener)

    first = ka.renew(account)
    rest = [ka.renew(account) for _ in range(5)]

    assert "started" in first
    assert rest == [None] * 5, "a debounced tick has nothing to say"
    assert len(opener.calls) == 1


def test_the_debounce_expires(account, monkeypatch) -> None:
    opener = FakeOpen()
    monkeypatch.setattr(ka.subprocess, "run", opener)
    ka.renew(account)
    ka.STATE_PATH.write_text(json.dumps({"JOIN": {"fired_at": time.time() - ka.DEBOUNCE_S - 1}}))

    assert "started" in ka.renew(account)
    assert len(opener.calls) == 2


def test_the_debounce_is_per_account(account, monkeypatch) -> None:
    opener = FakeOpen()
    monkeypatch.setattr(ka.subprocess, "run", opener)

    ka.renew(account)
    ka.renew(Account(label="PERSONAL", config_dir=""))

    assert len(opener.calls) == 2, "one account's session must not silence another's"


def test_a_failed_open_is_reported_and_not_debounced(account, monkeypatch) -> None:
    """Nothing was started, so the next tick should be free to try again."""
    monkeypatch.setattr(ka.subprocess, "run", FakeOpen(returncode=1, stderr="no such app"))

    assert "open failed for JOIN" in ka.renew(account)

    monkeypatch.setattr(ka.subprocess, "run", (retry := FakeOpen()))
    assert "started" in ka.renew(account)
    assert len(retry.calls) == 1


def test_a_missing_app_bundle_says_so(account, monkeypatch) -> None:
    monkeypatch.setattr(ka, "APP", Path("/nonexistent/Keepalive.app"))
    monkeypatch.setattr(ka.subprocess, "run", lambda *a, **k: pytest.fail("should not open"))

    assert "app bundle missing" in ka.renew(account)


def test_the_shipped_app_bundle_is_present_and_executable() -> None:
    """The path is computed from the package, so a move breaks it silently."""
    assert ka.APP.is_dir(), f"{ka.APP} is missing"
    binary = ka.APP / "Contents" / "MacOS" / "keepalive"
    assert binary.exists() and binary.stat().st_mode & 0o111, "not executable"


# --- last_result() -------------------------------------------------------


def test_last_result_returns_the_newest_line_for_that_account(account) -> None:
    ka.LOG_PATH.write_text(
        "2026-08-28 11:00:00 JOIN exit=0 4s older\n"
        "2026-08-28 11:30:00 PERSONAL exit=0 3s other account\n"
        "2026-08-28 12:00:00 JOIN exit=0 5s newest\n"
    )
    assert "newest" in ka.last_result("JOIN")
    assert "other account" in ka.last_result("PERSONAL")


def test_last_result_is_none_before_anything_has_run(account) -> None:
    assert ka.last_result("JOIN") is None
