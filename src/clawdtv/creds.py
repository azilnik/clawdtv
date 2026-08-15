"""Read-only access to Claude Code's stored OAuth credentials.

This module never writes and never refreshes. Refresh tokens are single-use, so
refreshing here would invalidate the copy Claude Code holds and force a re-login
on the next session. An expired token is reported as expired and nothing else;
opening Claude Code on that account fixes it.

The Keychain service name is derived the way Claude Code derives it: the base
name alone when no config dir is set, otherwise the base name plus the first
eight hex characters of the SHA-256 of the config dir string. The hash is keyed
on the exact spelling, so the literal path from config is hashed as-is.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import Account

SERVICE_BASE = "Claude Code-credentials"
KEYCHAIN_TIMEOUT_S = 20


def service_name(config_dir: str) -> str:
    if not config_dir:
        return SERVICE_BASE
    digest = hashlib.sha256(config_dir.encode()).hexdigest()[:8]
    return f"{SERVICE_BASE}-{digest}"


@dataclass
class Credentials:
    access_token: str
    expires_at: datetime | None
    subscription_type: str | None
    rate_limit_tier: str | None
    scopes: list[str]

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.now(UTC)

    @property
    def can_read_usage(self) -> bool:
        """The usage endpoint requires user:profile; user:inference alone is not enough."""
        return "user:profile" in self.scopes

    @property
    def plan_label(self) -> str | None:
        """'default_claude_max_20x' -> 'max 20x'."""
        tier = self.rate_limit_tier
        if not tier:
            return self.subscription_type
        return tier.removeprefix("default_claude_").replace("_", " ")


class CredentialsError(Exception):
    """Raised when credentials are absent or unusable. Message is display-safe."""


def _read_keychain(service: str) -> str | None:
    command = ["security", "find-generic-password", "-s", service]
    # launchd agents have USER set, but do not depend on it: without it, the
    # service name alone still finds the item.
    if user := os.environ.get("USER"):
        command += ["-a", user]
    command.append("-w")
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=KEYCHAIN_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        raise CredentialsError("keychain timeout") from None
    except OSError:
        raise CredentialsError("keychain unavailable") from None
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None


def _read_file(account: Account) -> str | None:
    path = account.path / ".credentials.json"
    try:
        return path.read_text()
    except OSError:
        return None


def load(account: Account) -> Credentials:
    """Load credentials for one account, or raise CredentialsError.

    Never falls back to the unscoped Keychain entry when the account is scoped —
    that would silently report a different account's numbers, which is worse than
    showing nothing.
    """
    raw = _read_keychain(service_name(account.config_dir)) or _read_file(account)
    if not raw:
        raise CredentialsError("not logged in")

    try:
        blob = json.loads(raw)
    except json.JSONDecodeError:
        raise CredentialsError("unreadable credentials") from None

    # Some installs carry only mcpOAuth in this item, with no subscription token.
    oauth = blob.get("claudeAiOauth")
    if not oauth or not oauth.get("accessToken"):
        raise CredentialsError("not logged in")

    expires_raw = oauth.get("expiresAt")
    expires_at = (
        datetime.fromtimestamp(expires_raw / 1000, tz=UTC)
        if isinstance(expires_raw, (int, float))
        else None
    )

    return Credentials(
        access_token=oauth["accessToken"],
        expires_at=expires_at,
        subscription_type=oauth.get("subscriptionType"),
        rate_limit_tier=oauth.get("rateLimitTier"),
        scopes=list(oauth.get("scopes") or []),
    )


def state_files(account: Account) -> list[Path]:
    """State files that may carry account identity and cached usage, best first.

    A scoped config dir keeps its state inside itself. The default install keeps
    .claude.json beside ~/.claude rather than within it.
    """
    paths = [account.path / ".config.json", account.path / ".claude.json"]
    if account.is_default:
        paths.append(Path.home() / ".claude.json")
    return paths


def read_state(account: Account) -> dict:
    for path in state_files(account):
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def account_email(account: Account) -> str | None:
    """Which account is actually logged into this config dir, per its own state."""
    return (read_state(account).get("oauthAccount") or {}).get("emailAddress")
