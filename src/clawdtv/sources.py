"""Where usage numbers come from.

Three tiers, cheapest first:

1. A statusline hook file, written by Claude Code itself while a session runs.
   Free, first-party, and freshest — but only exists while you are working.
2. `cachedUsageUtilization` in the config dir's state file, which Claude Code
   writes whenever it fetches usage for its own purposes. Free, but its
   freshness is uncontrolled and it has been observed hours stale.
3. A direct read of the OAuth usage endpoint. Always available, but rate
   limited per account and the only tier that makes a network request.

Whichever tier has the most recent observation wins. Every number carries the
time it was observed so the display can be honest about age.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache

from .config import STATE_DIR, Account, Config
from .creds import Credentials, CredentialsError, read_state
from . import creds as creds_mod
from . import keepalive

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"


@dataclass
class Window:
    """One usage window. `percent is None` means unknown, which is not zero."""

    percent: float | None = None
    resets_at: datetime | None = None

    @property
    def known(self) -> bool:
        return self.percent is not None

    def reset_passed(self, now: datetime) -> bool:
        """Whether this window has rolled over as of `now`.

        Takes the time rather than reading the clock so that a frame rendered
        for a given instant is the same frame every time. Reading the clock here
        made rendering depend on when it ran rather than on what it was given,
        which is wrong for a fixed-timestamp test and quietly wrong in the
        renderer too, where the rest of the frame uses the passed-in time.
        """
        return self.resets_at is not None and self.resets_at <= now


@dataclass
class AccountUsage:
    label: str
    email: str | None = None
    plan: str | None = None
    five_hour: Window = field(default_factory=Window)
    seven_day: Window = field(default_factory=Window)
    cost_today: float | None = None
    observed_at: datetime | None = None
    source: str = "none"
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and (self.five_hour.known or self.seven_day.known)

    def age_s(self, now: datetime | None = None) -> float | None:
        if self.observed_at is None:
            return None
        return ((now or datetime.now(UTC)) - self.observed_at).total_seconds()


class RateLimited(Exception):
    pass


@cache
def claude_version() -> str:
    """Used in the User-Agent. Without a claude-code UA the endpoint drops the
    caller into an aggressively rate-limited bucket and 429s persistently."""
    try:
        out = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
        return out.split()[0] if out else "2.1.0"
    except (OSError, subprocess.SubprocessError):
        return "2.1.0"


def parse_reset(value) -> datetime | None:
    """resets_at is an RFC3339 string in some shapes and epoch seconds in others."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _legacy_window(payload: dict, key: str) -> Window:
    node = payload.get(key)
    if not isinstance(node, dict):
        return Window()
    pct = node.get("utilization")
    return Window(
        percent=float(pct) if isinstance(pct, (int, float)) else None,
        resets_at=parse_reset(node.get("resets_at")),
    )


def parse_usage(payload: dict) -> tuple[Window, Window]:
    """Return (five_hour, seven_day).

    The newer `limits` array is authoritative where present; it reports `percent`
    while the older top-level objects report `utilization`. Both are 0-100.
    """
    five, seven = Window(), Window()

    for entry in payload.get("limits") or []:
        if not isinstance(entry, dict):
            continue
        pct = entry.get("percent")
        if not isinstance(pct, (int, float)):
            continue
        window = Window(percent=float(pct), resets_at=parse_reset(entry.get("resets_at")))
        kind = entry.get("kind")
        if kind == "session":
            five = window
        elif kind == "weekly_all":
            seven = window

    if not five.known:
        five = _legacy_window(payload, "five_hour")
    if not seven.known:
        seven = _legacy_window(payload, "seven_day")
    return five, seven


def fetch_endpoint(credentials: Credentials) -> dict:
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {credentials.access_token}",
            "anthropic-beta": OAUTH_BETA,
            "User-Agent": f"claude-code/{claude_version()}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RateLimited() from None
        raise


def from_cache(account: Account) -> tuple[Window, Window, datetime] | None:
    """Claude Code's own last fetch, shape-identical to the endpoint body."""
    cached = read_state(account).get("cachedUsageUtilization")
    if not isinstance(cached, dict):
        return None
    fetched_ms = cached.get("fetchedAtMs")
    payload = cached.get("utilization")
    if not isinstance(payload, dict) or not isinstance(fetched_ms, (int, float)):
        return None
    five, seven = parse_usage(payload)
    if not (five.known or seven.known):
        return None
    return five, seven, datetime.fromtimestamp(fetched_ms / 1000, tz=UTC)


def from_statusline(account: Account) -> tuple[Window, Window, datetime] | None:
    """State file written by the statusline hook while a session is running.

    Claude Code passes rate_limits on stdin using `used_percentage` and epoch
    seconds, which differs from both API shapes.
    """
    path = STATE_DIR / f"{account.slug}.json"
    try:
        blob = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    limits = blob.get("rate_limits")
    if not isinstance(limits, dict):
        return None

    def window(key: str) -> Window:
        node = limits.get(key)
        if not isinstance(node, dict):
            return Window()
        pct = node.get("used_percentage")
        return Window(
            percent=float(pct) if isinstance(pct, (int, float)) else None,
            resets_at=parse_reset(node.get("resets_at")),
        )

    five, seven = window("five_hour"), window("seven_day")
    if not (five.known or seven.known):
        return None
    written = blob.get("written_at")
    if not isinstance(written, (int, float)):
        return None
    return five, seven, datetime.fromtimestamp(written, tz=UTC)


class Poller:
    """Collects usage for each account, respecting the endpoint's rate limit.

    Holds the last good endpoint body per account so a quiet period or a 429
    never makes the display go backwards to something older than it already had.

    State lives on disk because each tick is a separate process: without it,
    every run would look like a first run and hammer the endpoint into a
    sticky 429.
    """

    STATE_PATH = STATE_DIR / "poller.json"

    def __init__(self, config: Config):
        self.config = config
        self._last_fetch: dict[str, datetime] = {}
        self._cooldown_until: dict[str, datetime] = {}
        self._last_good: dict[str, tuple[Window, Window, datetime]] = {}
        self._load_state()

    def _load_state(self) -> None:
        try:
            blob = json.loads(self.STATE_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return
        for label, entry in (blob.get("accounts") or {}).items():
            if stamp := entry.get("last_fetch"):
                self._last_fetch[label] = datetime.fromtimestamp(stamp, tz=UTC)
            if stamp := entry.get("cooldown_until"):
                self._cooldown_until[label] = datetime.fromtimestamp(stamp, tz=UTC)
            good = entry.get("last_good")
            if isinstance(good, dict) and good.get("observed_at"):
                self._last_good[label] = (
                    Window(good["five"].get("percent"), parse_reset(good["five"].get("resets_at"))),
                    Window(good["seven"].get("percent"), parse_reset(good["seven"].get("resets_at"))),
                    datetime.fromtimestamp(good["observed_at"], tz=UTC),
                )

    def save_state(self) -> None:
        def window(w: Window) -> dict:
            return {
                "percent": w.percent,
                "resets_at": w.resets_at.timestamp() if w.resets_at else None,
            }

        accounts = {}
        for label in set(self._last_fetch) | set(self._cooldown_until) | set(self._last_good):
            entry: dict = {}
            if stamp := self._last_fetch.get(label):
                entry["last_fetch"] = stamp.timestamp()
            if stamp := self._cooldown_until.get(label):
                entry["cooldown_until"] = stamp.timestamp()
            if good := self._last_good.get(label):
                five, seven, observed = good
                entry["last_good"] = {
                    "five": window(five),
                    "seven": window(seven),
                    "observed_at": observed.timestamp(),
                }
            accounts[label] = entry

        self.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.STATE_PATH.write_text(json.dumps({"accounts": accounts}))

    def _may_fetch(self, label: str, now: datetime) -> bool:
        if (until := self._cooldown_until.get(label)) and now < until:
            return False
        last = self._last_fetch.get(label)
        return last is None or (now - last).total_seconds() >= self.config.endpoint_interval_s

    def collect(self, account: Account) -> AccountUsage:
        now = datetime.now(UTC)
        usage = AccountUsage(label=account.label)
        usage.email = creds_mod.account_email(account)

        try:
            credentials = creds_mod.load(account)
        except CredentialsError as exc:
            usage.error = str(exc)
            return usage

        if self.config.keepalive and keepalive.due(credentials, now) and keepalive.renew(account):
            try:
                credentials = creds_mod.load(account)
            except CredentialsError:
                pass  # Keep the still-valid token we already hold.

        usage.plan = credentials.plan_label

        candidates: list[tuple[Window, Window, datetime, str]] = []
        if (cached := from_cache(account)) is not None:
            candidates.append((*cached, "cache"))
        if (line := from_statusline(account)) is not None:
            candidates.append((*line, "statusline"))
        if (previous := self._last_good.get(account.label)) is not None:
            candidates.append((*previous, "endpoint"))

        if credentials.expired:
            usage.error = "token expired"
        elif not credentials.can_read_usage:
            usage.error = "no usage scope"
        elif self._may_fetch(account.label, now):
            try:
                payload = fetch_endpoint(credentials)
                five, seven = parse_usage(payload)
                self._last_fetch[account.label] = now
                if five.known or seven.known:
                    self._last_good[account.label] = (five, seven, now)
                    candidates.append((five, seven, now, "endpoint"))
            except RateLimited:
                self._last_fetch[account.label] = now
                self._cooldown_until[account.label] = now + timedelta(
                    seconds=self.config.rate_limit_cooldown_s
                )
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
                self._last_fetch[account.label] = now

        if candidates:
            five, seven, observed, source = max(candidates, key=lambda c: c[2])
            usage.five_hour, usage.seven_day = five, seven
            usage.observed_at, usage.source = observed, source
            if usage.error is None and not (five.known or seven.known):
                usage.error = "no data"
        elif usage.error is None:
            usage.error = "no data"

        return usage
