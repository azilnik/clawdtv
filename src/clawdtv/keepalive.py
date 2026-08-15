"""Keep an account's access token from lapsing, without ever writing it.

creds.py refuses to refresh because refresh tokens are single-use: minting one
here would invalidate the copy Claude Code holds. So this does what the README
already prescribes as the cure — it opens Claude Code on that account — just
before the token would lapse rather than after. The smallest session that still
talks to the API is enough; `claude auth status` is not, because it reads the
stored token without exercising it.

The trigger is the token's own expiry and nothing else, which is what keeps this
free of state. Each tick is a separate process, so anything remembered between
attempts would have to live on disk; instead, a success moves expiry hours out
and silences the trigger, and a failure retries on the next tick until the token
lapses, at which point the trigger goes quiet on its own. Worst case is a
handful of attempts across the final MARGIN, then the old behavior: the panel
says expired and waits.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime

from .config import Account
from .creds import Credentials

# Long enough to survive a sleeping laptop and a few failed attempts, short
# enough that the extra sessions stay rare — one per token, so ~3 a day.
MARGIN_S = 1800
TIMEOUT_S = 90

# Cheapest model, no MCP servers to boot, one word in and one word out.
MODEL = "claude-haiku-4-5-20251001"
PROMPT = "ok"


def due(credentials: Credentials, now: datetime | None = None) -> bool:
    """True when the token is close enough to expiry to be worth renewing.

    Already-expired tokens are excluded: renewing one would work, but retrying
    every tick forever is a worse failure than the panel saying so.
    """
    if credentials.expires_at is None:
        return False
    now = now or datetime.now(UTC)
    remaining = (credentials.expires_at - now).total_seconds()
    return 0 < remaining <= MARGIN_S


def renew(account: Account) -> bool:
    """Run a throwaway session on this account so Claude Code refreshes it.

    Returns whether the session succeeded. Never raises: a keepalive that fails
    should cost the caller a stale panel, not a crashed tick.
    """
    # Set or clear rather than merge: an inherited CLAUDE_CONFIG_DIR would
    # otherwise send the default account's keepalive to the wrong account.
    env = os.environ.copy()
    if account.config_dir:
        env["CLAUDE_CONFIG_DIR"] = account.config_dir
    else:
        env.pop("CLAUDE_CONFIG_DIR", None)

    try:
        proc = subprocess.run(
            [
                "claude",
                "-p",
                PROMPT,
                "--model",
                MODEL,
                # Without these, a keepalive boots every MCP server the account
                # has configured — seconds of npx startup for a one-word turn.
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
            ],
            cwd=account.path,
            env=env,
            capture_output=True,
            timeout=TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0
