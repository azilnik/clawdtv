"""Config validation: every mistake config.toml can hold should fail loudly at
load, with a message naming the key, rather than surfacing later as a silent
misbehavior on a screen nobody is watching."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clawdtv import config  # noqa: E402

GOOD = """
[device]
host = "192.168.1.50"
filename = "usage.jpg"
theme = 4
min_push_interval_s = 120

[poll]
tick_interval_s = 300
endpoint_interval_s = 240
rate_limit_cooldown_s = 300

[display]
warn_at = 60
alert_at = 85
quiet_start_hour = 23
quiet_end_hour = 7
stale_after_s = 900

[[accounts]]
label = "PERSONAL"
config_dir = ""
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_a_complete_config_loads(tmp_path) -> None:
    cfg = config.load(write(tmp_path, GOOD))
    assert cfg.host == "192.168.1.50"
    assert len(cfg.accounts) == 1
    # Sections not present fall back to their defaults.
    assert cfg.keepalive is True
    assert cfg.cost is False  # dollars are opt-in
    assert cfg.notify_command == ""


def test_cost_display_is_opt_in(tmp_path) -> None:
    text = GOOD.replace("[device]", "[cost]\nenabled = true\n\n[device]")
    assert config.load(write(tmp_path, text)).cost is True


def test_shipped_config_is_valid() -> None:
    cfg = config.load()
    assert cfg.host == ""  # ships unconfigured on purpose
    assert 1 <= len(cfg.accounts) <= 2


@pytest.mark.parametrize("count", [0, 3])
def test_only_one_or_two_accounts(tmp_path, count: int) -> None:
    base = GOOD.split("[[accounts]]")[0]
    blocks = "".join(
        f'[[accounts]]\nlabel = "A{i}"\nconfig_dir = ""\n' for i in range(count)
    )
    with pytest.raises(config.ConfigError, match="one or two"):
        config.load(write(tmp_path, base + blocks))


def test_relative_config_dir_is_refused(tmp_path) -> None:
    """A relative CLAUDE_CONFIG_DIR splits a session between a cwd-relative state
    dir and the default Keychain entry, silently reporting the wrong account."""
    text = GOOD.replace('config_dir = ""', 'config_dir = "claude-work"')
    with pytest.raises(config.ConfigError, match="absolute"):
        config.load(write(tmp_path, text))


@pytest.mark.parametrize("label", ["", "a/b", "x" * 21, "semi;colon"])
def test_hostile_labels_are_refused(tmp_path, label: str) -> None:
    """Labels become state filenames, so they are held to safe characters."""
    text = GOOD.replace('label = "PERSONAL"', f'label = "{label}"')
    with pytest.raises(config.ConfigError, match="label"):
        config.load(write(tmp_path, text))


def test_duplicate_labels_are_refused(tmp_path) -> None:
    text = GOOD + '\n[[accounts]]\nlabel = "PERSONAL"\nconfig_dir = "/tmp/x"\n'
    with pytest.raises(config.ConfigError, match="share"):
        config.load(write(tmp_path, text))


def test_labels_differing_only_by_case_collide(tmp_path) -> None:
    """State filenames derive from the lowercased label, so Personal and PERSONAL
    would silently share caches."""
    text = GOOD + '\n[[accounts]]\nlabel = "Personal"\nconfig_dir = "/tmp/x"\n'
    with pytest.raises(config.ConfigError, match="share"):
        config.load(write(tmp_path, text))


@pytest.mark.parametrize(
    "bad", ['filename = "../../etc/x.jpg"', 'filename = "no-extension"', 'filename = "a b.jpg"']
)
def test_hostile_filenames_are_refused(tmp_path, bad: str) -> None:
    text = GOOD.replace('filename = "usage.jpg"', bad)
    with pytest.raises(config.ConfigError, match="filename"):
        config.load(write(tmp_path, text))


def test_inverted_thresholds_are_refused(tmp_path) -> None:
    text = GOOD.replace("warn_at = 60", "warn_at = 90")
    with pytest.raises(config.ConfigError, match="below"):
        config.load(write(tmp_path, text))


def test_boolean_thresholds_are_refused(tmp_path) -> None:
    """bool is an int subclass, so `warn_at = true` would otherwise sail
    through as 1.0 and turn the display amber at 1% used."""
    text = GOOD.replace("warn_at = 60", "warn_at = true")
    with pytest.raises(config.ConfigError, match="warn_at"):
        config.load(write(tmp_path, text))


def test_scalar_where_a_section_belongs_is_refused(tmp_path) -> None:
    """`keepalive = false` above the tables is a natural way to write the flag;
    it must be a clear error, not an AttributeError."""
    text = GOOD.replace("[device]", "keepalive = false\n\n[device]")
    with pytest.raises(config.ConfigError, match="keepalive"):
        config.load(write(tmp_path, text))


def test_non_boolean_enabled_is_refused(tmp_path) -> None:
    """`enabled = "no"` is truthy; silently meaning enabled would be a lie."""
    text = GOOD.replace("[device]", '[keepalive]\nenabled = "no"\n\n[device]')
    with pytest.raises(config.ConfigError, match="true or false"):
        config.load(write(tmp_path, text))


def test_settings_drifted_under_accounts_are_refused(tmp_path) -> None:
    """TOML files anything after [[accounts]] under that account, where it
    would be silently ignored — the classic misplaced-flag footgun."""
    with pytest.raises(config.ConfigError, match="unknown key"):
        config.load(write(tmp_path, GOOD + "\nkeepalive = false\n"))


def test_trailing_newline_in_label_is_refused(tmp_path) -> None:
    r"""$ matches before a trailing \n; \Z is the anchor that does not."""
    text = GOOD.replace('label = "PERSONAL"', 'label = "PERSONAL\\n"')
    with pytest.raises(config.ConfigError, match="label"):
        config.load(write(tmp_path, text))


def test_labels_sharing_a_first_letter_are_refused(tmp_path) -> None:
    """The footer tags each account's cost by its label's first letter, so
    PERSONAL and PRO would both render as `P $…`."""
    text = GOOD + '\n[[accounts]]\nlabel = "PRO"\nconfig_dir = "/tmp/x"\n'
    with pytest.raises(config.ConfigError, match="first letter"):
        config.load(write(tmp_path, text))


def test_nonsense_hours_are_refused(tmp_path) -> None:
    text = GOOD.replace("quiet_start_hour = 23", "quiet_start_hour = 24")
    with pytest.raises(config.ConfigError, match="hour"):
        config.load(write(tmp_path, text))


def test_missing_file_names_the_path(tmp_path) -> None:
    with pytest.raises(config.ConfigError, match="no config file"):
        config.load(tmp_path / "nope.toml")


def test_slug_makes_labels_filename_safe() -> None:
    assert config.Account(label="My Max", config_dir="").slug == "my-max"


@pytest.mark.parametrize("label", ["PERSONAL", "My Max", "work 2", "A.B-c_d", "x" * 20])
def test_shell_tee_derives_the_same_slug(label: str) -> None:
    """tools/statusline-tee.sh re-derives Account.slug in tr; this is the test
    that keeps the two derivations from drifting apart, because the failure
    mode is silent — the tee writes a file the poller never reads."""
    import subprocess

    shell = subprocess.run(
        ["/bin/sh", "-c", "printf '%s' \"$1\" | tr '[:upper:] ' '[:lower:]-'", "slug", label],
        capture_output=True,
        text=True,
    )
    assert shell.stdout == config.Account(label=label, config_dir="").slug
