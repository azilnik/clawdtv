"""Keep an account's access token from lapsing, without ever writing it.

creds.py refuses to refresh because refresh tokens are single-use: minting one
here would invalidate the copy Claude Code holds. So this does what the README
already prescribes as the cure — it opens Claude Code on that account — just
before the token would lapse rather than after. The smallest session that still
talks to the API is enough; `claude auth status` is not, because it reads the
stored token without exercising it.

**Why this goes through an app bundle.** The obvious implementation runs
`claude` straight from the tick, and on the development machine it never once
worked. A `claude` session is Node, and Node's outbound `connect()` fails with
`EBADF` in the launchd-agent exec context: every attempt hung until timeout — 0
successes across 46 tries — while the same command ran in ~3.7s from a terminal.
It is not the fd limit, the session type, stdio, or the environment; all were
ruled out with one-shot launchd repros. What works is not running the session
from the agent at all. `open` hands the request to LaunchServices, which spawns
the app in the Aqua session regardless of who asked, and Node's network works
there. Verified end to end: launchd agent, `open`, a real authenticated response
in ten seconds, in the exact context that produced those 46 timeouts.

That failure may well be specific to one machine's macOS build. If yours runs
`claude` from a launchd agent happily, this indirection costs one extra process
and is otherwise harmless. Turn the whole thing off with
`[keepalive] enabled = false`.
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from .config import Account
from .creds import Credentials

APP = Path(__file__).resolve().parents[2] / "tools" / "ClawdtvKeepalive.app"
LOG_PATH = Path.home() / ".local" / "state" / "clawdtv" / "keepalive.log"
STATE_PATH = Path.home() / ".local" / "state" / "clawdtv" / "keepalive.json"

# Long enough to survive a sleeping laptop and a few failed attempts, short
# enough that the extra sessions stay rare — one per token, so ~3 a day.
MARGIN_S = 1800
# `open` returns as soon as LaunchServices accepts, long before the session it
# started has finished, so the token is still stale on the next tick. Without
# this, every tick inside MARGIN_S starts another session and a lapsed account
# spends several of them doing one job.
DEBOUNCE_S = 600


def due(credentials: Credentials, now: datetime | None = None) -> bool:
    """True when the token is close enough to expiry to be worth renewing.

    Fires after expiry as well as before it. An earlier version stopped at the
    moment of lapse, reasoning that retrying forever was the worse failure — but
    that left a machine which slept through its own margin stranded until
    somebody noticed, once for 65 hours. Two things make the later window safe:
    the debounce bounds the retry rate, and a dead refresh token is excluded
    outright, since Claude Code could not refresh either and the session would
    burn quota to change nothing.
    """
    if not credentials.has_refresh_token:
        return False
    now = now or datetime.now(UTC)
    if credentials.refresh_expires_at is not None and credentials.refresh_expires_at <= now:
        return False
    if credentials.expires_at is None:
        return False
    return (credentials.expires_at - now).total_seconds() <= MARGIN_S


def _read_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _record_fire(label: str) -> None:
    state = _read_state()
    state[label] = {"fired_at": time.time()}
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


def _debouncing(label: str) -> float:
    fired = (_read_state().get(label) or {}).get("fired_at")
    if not isinstance(fired, (int, float)):
        return 0.0
    return max(0.0, DEBOUNCE_S - (time.time() - fired))


def last_result(label: str) -> str | None:
    """The most recent keepalive outcome for this account, for `check` to show.

    The session runs elsewhere and finishes after the tick that started it, so
    this log is the only place its outcome is visible.
    """
    try:
        lines = LOG_PATH.read_text().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if f" {label} " in line:
            return line
    return None


def renew(account: Account) -> str | None:
    """Start a keepalive session for one account. Returns a line for the log.

    Returns None while debouncing, because that case repeats every tick and
    logging it would bury the attempts worth reading.

    Asynchronous by design: this returns as soon as LaunchServices has accepted
    the request, and the session's own outcome lands in keepalive.log seconds
    later. Waiting would put the tick back to blocking on Node, which is what
    this arrangement exists to avoid — so the tick that starts a session still
    reports the old token, and the next one sees the refreshed one.
    """
    if _debouncing(account.label):
        return None
    if not APP.exists():
        return f"keepalive: app bundle missing at {APP}"

    try:
        proc = subprocess.run(
            ["/usr/bin/open", "-n", "-a", str(APP), "--args", account.config_dir, account.label],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"keepalive: could not start a session for {account.label} ({exc})"

    if proc.returncode != 0:
        return f"keepalive: open failed for {account.label} ({proc.stderr.strip()[:80]})"

    _record_fire(account.label)
    return f"keepalive: started a session for {account.label}"
