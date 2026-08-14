"""Configuration loading and validation.

Every value the daemon runs on comes from config.toml, and every mistake that
file can hold is cheaper to reject here — with a message naming the key — than
to let it surface later as a silent misbehavior on a screen nobody is watching.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config.toml"

# Everything remembered between ticks lives here: poller state, the cost cache,
# the usage history, and the last frame pushed. Deleting it is always safe.
STATE_DIR = Path.home() / ".local" / "state" / "clawdtv"

# Labels double as state filenames and screen headings, so they are held to
# characters that are safe as both. Filenames land on the device's filesystem.
# \Z, not $: $ would accept a trailing newline, which would end up inside a
# state filename and a hand-built HTTP request line.
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,19}\Z")
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,40}\.jpe?g\Z", re.IGNORECASE)

ACCOUNT_KEYS = {"label", "config_dir"}


class ConfigError(ValueError):
    """A config.toml problem, worded for the person who has to fix it."""


@dataclass(frozen=True)
class Account:
    label: str
    config_dir: str  # "" means the default ~/.claude

    @property
    def is_default(self) -> bool:
        return not self.config_dir

    @property
    def path(self) -> Path:
        return Path(self.config_dir) if self.config_dir else Path.home() / ".claude"

    @property
    def slug(self) -> str:
        """The label as a state-file fragment: lowercase, spaces dashed.

        tools/statusline-tee.sh derives the same slug in shell; a test pins
        the two derivations together.
        """
        return self.label.lower().replace(" ", "-")


@dataclass(frozen=True)
class Config:
    host: str
    filename: str
    theme: int
    min_push_interval_s: int
    tick_interval_s: int
    endpoint_interval_s: int
    rate_limit_cooldown_s: int
    warn_at: float
    alert_at: float
    quiet_start_hour: int
    quiet_end_hour: int
    stale_after_s: int
    keepalive: bool
    cost: bool
    notify_command: str
    accounts: list[Account] = field(default_factory=list)


def _int_between(section: dict, key: str, lo: int, hi: int, what: str) -> int:
    value = section.get(key)
    # bool is an int subclass, so `theme = true` would otherwise pass as 1.
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise ConfigError(f"{key} must be {what}, got {value!r}")
    return value


def _number_between(section: dict, key: str, lo: float, hi: float, what: str) -> float:
    value = section.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not lo <= value <= hi:
        raise ConfigError(f"{key} must be {what}, got {value!r}")
    return float(value)


def _table(raw: dict, name: str, required: bool = False) -> dict:
    """A [section] table. A scalar of the same name is a placement mistake that
    would otherwise crash (or worse, be silently ignored inside another table)."""
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(
            f"{name} must be a [{name}] section, not a bare value — "
            f"write it as:\n[{name}]\n..."
        )
    if required and not value:
        raise ConfigError(f"config is missing its [{name}] section")
    return value


def _flag(raw: dict, name: str, default: bool) -> bool:
    value = _table(raw, name).get("enabled", default)
    if not isinstance(value, bool):
        raise ConfigError(f"[{name}] enabled must be true or false, got {value!r}")
    return value


def _parse_accounts(raw: list) -> list[Account]:
    if not 1 <= len(raw) <= 2:
        raise ConfigError(
            f"the display fits one or two [[accounts]] blocks, got {len(raw)}"
        )

    accounts = []
    for entry in raw:
        # An unknown key here is usually a top-level setting that drifted below
        # the [[accounts]] tables, where TOML files it under the account and it
        # would otherwise be ignored without a word.
        if unknown := set(entry) - ACCOUNT_KEYS:
            raise ConfigError(
                f"unknown key {sorted(unknown)[0]!r} under [[accounts]] — "
                "settings belong above the account blocks"
            )
        label = entry.get("label", "")
        if not isinstance(label, str) or not LABEL_RE.match(label):
            raise ConfigError(
                f"account label {label!r} must be 1-20 letters, digits, spaces, dots or dashes"
            )
        config_dir = entry.get("config_dir", "")
        # A relative CLAUDE_CONFIG_DIR silently splits a session: config state goes
        # to a cwd-relative dir while credentials come from the DEFAULT Keychain
        # entry, so the account would be misattributed. Refuse it outright.
        if config_dir and not config_dir.startswith("/"):
            raise ConfigError(
                f"account {label}: config_dir must be an absolute path, got {config_dir!r}"
            )
        accounts.append(Account(label=label, config_dir=config_dir))

    for describe, derive in (
        ("label", lambda a: a.label),
        ("slug", lambda a: a.slug),
        ("config_dir", lambda a: a.config_dir),
        # The footer tags each cost with the label's first letter.
        ("first letter (the footer tells costs apart by it)", lambda a: a.label[0]),
    ):
        if len({derive(account) for account in accounts}) != len(accounts):
            raise ConfigError(f"accounts must not share a {describe}")
    return accounts


def load(path: Path | None = None) -> Config:
    source = path or DEFAULT_CONFIG
    try:
        raw = tomllib.loads(source.read_text())
    except FileNotFoundError:
        raise ConfigError(f"no config file at {source}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{source} is not valid TOML: {exc}") from None

    dev = _table(raw, "device", required=True)
    poll = _table(raw, "poll", required=True)
    disp = _table(raw, "display", required=True)

    host = dev.get("host", "")
    if not isinstance(host, str):
        raise ConfigError(f"device host must be a string, got {host!r}")

    filename = dev.get("filename", "usage.jpg")
    if not isinstance(filename, str) or not FILENAME_RE.match(filename):
        raise ConfigError(
            f"device filename {filename!r} must be a plain name ending in .jpg"
        )

    warn_at = _number_between(disp, "warn_at", 0, 100, "a percentage from 0 to 100")
    alert_at = _number_between(disp, "alert_at", 0, 100, "a percentage from 0 to 100")
    if warn_at >= alert_at:
        raise ConfigError(f"warn_at ({warn_at}) must be below alert_at ({alert_at})")

    notify_command = _table(raw, "notify").get("command", "")
    if not isinstance(notify_command, str):
        raise ConfigError(f"notify command must be a string, got {notify_command!r}")

    second = "a positive whole number of seconds"
    return Config(
        host=host,
        filename=filename,
        theme=_int_between(dev, "theme", 0, 9, "a small whole number"),
        min_push_interval_s=_int_between(dev, "min_push_interval_s", 1, 10**9, second),
        tick_interval_s=_int_between(poll, "tick_interval_s", 1, 10**9, second),
        endpoint_interval_s=_int_between(poll, "endpoint_interval_s", 1, 10**9, second),
        rate_limit_cooldown_s=_int_between(poll, "rate_limit_cooldown_s", 1, 10**9, second),
        warn_at=warn_at,
        alert_at=alert_at,
        quiet_start_hour=_int_between(disp, "quiet_start_hour", 0, 23, "an hour from 0 to 23"),
        quiet_end_hour=_int_between(disp, "quiet_end_hour", 0, 23, "an hour from 0 to 23"),
        stale_after_s=_int_between(disp, "stale_after_s", 1, 10**9, second),
        keepalive=_flag(raw, "keepalive", True),
        cost=_flag(raw, "cost", True),
        notify_command=notify_command,
        accounts=_parse_accounts(raw.get("accounts") or []),
    )
