"""The keepalive fires in one narrow band, and carries no memory.

Each tick is a separate process, so the trigger has to be recoverable from the
token alone. That makes the whole contract a window: renew inside the last
MARGIN before expiry, and nowhere else. The upper edge keeps healthy tokens from
spawning sessions; the lower edge — already expired — is the part worth pinning
down, because renewing there would work and would then retry every tick for as
long as the account stayed logged out.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clawdtv import keepalive  # noqa: E402
from clawdtv.config import Account  # noqa: E402
from clawdtv.creds import Credentials  # noqa: E402

NOW = datetime(2026, 8, 10, 16, 0, tzinfo=UTC)


def creds(expires_at: datetime | None) -> Credentials:
    return Credentials(
        access_token="t",
        expires_at=expires_at,
        subscription_type="max",
        rate_limit_tier=None,
        scopes=["user:profile"],
    )


@pytest.mark.parametrize(
    "remaining_s,expected",
    [
        (8 * 3600, False),                  # fresh; nothing to do
        (keepalive.MARGIN_S + 60, False),   # outside the band
        (keepalive.MARGIN_S, True),         # the band is inclusive at the top
        (300, True),
        (1, True),
        (0, False),                         # expired: hand it back to the panel
        (-3600, False),                     # long expired; never a retry loop
    ],
)
def test_due_only_inside_the_final_margin(remaining_s: int, expected: bool) -> None:
    assert keepalive.due(creds(NOW + timedelta(seconds=remaining_s)), NOW) is expected


def test_no_expiry_is_never_due() -> None:
    """A token with no expiry can't be reasoned about, so leave it alone."""
    assert keepalive.due(creds(None), NOW) is False


def test_renew_reports_failure_instead_of_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing `claude` on a launchd PATH must cost a stale panel, not the tick."""

    def boom(*args, **kwargs):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(keepalive.subprocess, "run", boom)
    assert keepalive.renew(Account(label="WORK", config_dir="/tmp")) is False


def test_renew_targets_the_accounts_own_config_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs["env"]
        return type("P", (), {"returncode": 0})()

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/leaked/from/the/parent")
    monkeypatch.setattr(keepalive.subprocess, "run", fake_run)

    assert keepalive.renew(Account(label="WORK", config_dir="/tmp")) is True
    assert seen["env"]["CLAUDE_CONFIG_DIR"] == "/tmp"
    assert seen["env"].get("PATH"), "the real environment has to survive"
    assert "--strict-mcp-config" in seen["cmd"]

    # The default account must not inherit a scoped dir from the caller.
    assert keepalive.renew(Account(label="PERSONAL", config_dir="")) is True
    assert "CLAUDE_CONFIG_DIR" not in seen["env"]
