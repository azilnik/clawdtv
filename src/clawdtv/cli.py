"""Command line entry points.

`run` is one tick, invoked by launchd on an interval rather than looping in a
long-lived process. A fresh process per tick means nothing to wedge, and waking
from sleep produces a tick for free.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from . import config as config_mod
from . import cost as cost_mod
from . import creds as creds_mod
from . import history as history_mod
from . import render as render_mod
from . import theme
from .config import STATE_DIR
from .device import Device, DeviceError
from .sources import Poller

DEVICE_STATE = STATE_DIR / "device.json"
FRAME_PATH = STATE_DIR / "frame.jpg"
FAILURE_ALERT_AFTER_S = 2 * 3600
FAILURE_ALERT_REPEAT_S = 12 * 3600

NO_HOST_HELP = (
    "no screen configured: set host under [device] in config.toml to your "
    "device's IP address (it shows one during Wi-Fi setup)"
)


def _notify(cfg: config_mod.Config, title: str, body: str) -> None:
    """Run the user's notify command with the title and body appended as two
    arguments. The command is a shell line (paths with spaces need quotes, per
    config.toml). Best effort — a display that cannot phone home is not worth a
    crash — but a failure is logged, because this only runs when the screen has
    been unreachable for hours and a silently dead notifier would bury that."""
    if not cfg.notify_command:
        return
    try:
        proc = subprocess.run(
            ["/bin/sh", "-c", f'{cfg.notify_command} "$@"', "clawdtv-notify", title, body],
            timeout=20,
            capture_output=True,
        )
        if proc.returncode != 0:
            print(f"notify command failed (exit {proc.returncode})", file=sys.stderr)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"notify command failed: {exc}", file=sys.stderr)


def _load_device_state() -> dict:
    try:
        return json.loads(DEVICE_STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_device_state(state: dict) -> None:
    DEVICE_STATE.parent.mkdir(parents=True, exist_ok=True)
    DEVICE_STATE.write_text(json.dumps(state))


def in_quiet_hours(cfg: config_mod.Config, now: datetime) -> bool:
    hour = now.astimezone().hour
    start, end = cfg.quiet_start_hour, cfg.quiet_end_hour
    return start <= hour or hour < end if start > end else start <= hour < end


def collect(cfg: config_mod.Config, with_cost: bool = True):
    poller = Poller(cfg)
    usages = []
    for account in cfg.accounts:
        usage = poller.collect(account)
        if with_cost and cfg.cost and usage.error is None:
            usage.cost_today = cost_mod.today(account)
        usages.append(usage)
    poller.save_state()
    history_mod.record(usages)
    return usages


def cmd_run(args) -> int:
    cfg = config_mod.load(args.config)
    if not cfg.host:
        print(NO_HOST_HELP, file=sys.stderr)
        return 2
    now = datetime.now(UTC)

    usages = collect(cfg)
    image = render_mod.render(usages, cfg, now)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data = render_mod.save_jpeg(image, FRAME_PATH)

    digest = hashlib.sha256(data).hexdigest()
    state = _load_device_state()
    since_push = time.time() - state.get("pushed_at", 0)

    if not args.force:
        if in_quiet_hours(cfg, now):
            print(f"quiet hours; frame not pushed ({len(data)} bytes rendered)")
            return 0
        if digest == state.get("digest"):
            print(f"unchanged; skipped push ({int(since_push)}s since last)")
            return 0
        if since_push < cfg.min_push_interval_s:
            print(f"throttled; {int(cfg.min_push_interval_s - since_push)}s until next push allowed")
            return 0

    device = Device(cfg.host)
    try:
        device.upload(cfg.filename, data)

        # A reboot can drop the device back to another app, and uploading into an
        # album nobody is looking at fails completely silently — the push succeeds
        # and the screen never changes. One GET per push catches that; the writes
        # underneath only happen when something actually drifted.
        if device.current_theme() != cfg.theme:
            device.prune_album(cfg.filename)
            device.set_theme(cfg.theme)
            device.set_album(interval_s=60, autoplay=1)
            print(f"re-asserted picture mode (theme {cfg.theme})")
        elif since_push > 3600 or state.get("digest") is None:
            # Autoplay cycles whatever is in /image/, so a stray file would
            # rotate over our frame. Rare on its own schedule: this writes flash.
            if removed := device.prune_album(cfg.filename):
                print(f"pruned from album: {', '.join(removed)}")
    except DeviceError as exc:
        print(f"push failed: {exc}", file=sys.stderr)
        failing_since = state.get("failing_since") or time.time()
        notified_at = state.get("notified_at", 0)
        # A single failure is usually a wifi blip and resolves on the next tick.
        # Only a sustained one is worth interrupting anyone about, and only once.
        if (
            time.time() - failing_since > FAILURE_ALERT_AFTER_S
            and time.time() - notified_at > FAILURE_ALERT_REPEAT_S
            and not in_quiet_hours(cfg, now)
        ):
            _notify(
                cfg,
                "clawdtv can't reach the screen",
                f"No luck for {int((time.time() - failing_since) / 60)} minutes. Last error: {exc}",
            )
            notified_at = time.time()
        _save_device_state(
            {**state, "failing_since": failing_since, "notified_at": notified_at, "error": str(exc)}
        )
        return 1

    if state.get("failing_since"):
        print(f"recovered after {int((time.time() - state['failing_since']) / 60)}m")
    _save_device_state({"digest": digest, "pushed_at": time.time()})
    summary = "  ".join(
        f"{u.label[:1]}:{'--' if u.five_hour.percent is None else round(u.five_hour.percent)}%"
        f"/{'--' if u.seven_day.percent is None else round(u.seven_day.percent)}%"
        for u in usages
    )
    print(f"pushed {len(data)} bytes  {summary}")
    return 0


def cmd_render(args) -> int:
    cfg = config_mod.load(args.config)
    usages = collect(cfg, with_cost=not args.no_cost)
    image = render_mod.render(usages, cfg)
    path = Path(args.output)
    data = render_mod.save_jpeg(image, path)
    for usage in usages:
        detail = usage.error or f"5h {usage.five_hour.percent}%  7d {usage.seven_day.percent}%"
        age = usage.age_s()
        print(
            f"  {usage.label:9s} {detail}"
            + (f"  [{usage.source}, {int(age)}s old]" if age is not None else "")
        )
    print(f"{path} ({len(data)} bytes)")
    return 0


def cmd_check(args) -> int:
    """Self-test. Every reverse-engineered fact here can break on a Claude Code
    or firmware update, so this exists to say which one broke."""
    cfg = config_mod.load(args.config)
    failures = 0

    print("device")
    device = Device(cfg.host) if cfg.host else None
    if device is None:
        print(f"  FAIL {NO_HOST_HELP}")
        failures += 1
    elif (model := device.model()) is None:
        print(f"  FAIL unreachable at {cfg.host}")
        failures += 1
    else:
        print(f"  ok   {model} at {cfg.host}")
        expected_theme = 4 if "PRO" in model.upper() else 3
        if expected_theme != cfg.theme:
            print(f"  FAIL config theme={cfg.theme} but {model} expects {expected_theme}")
            failures += 1
        else:
            print(f"  ok   picture theme {cfg.theme} matches model")
        free = device.free_bytes()
        if free is not None:
            print(f"  ok   {free:,} bytes free")
        images = device.list_images()
        extra = [name for name in images if name != cfg.filename]
        print(f"  {'warn' if extra else 'ok  '} album: {images or 'empty'}")

    print("accounts")
    for account in cfg.accounts:
        label = account.label
        service = creds_mod.service_name(account.config_dir)
        try:
            credentials = creds_mod.load(account)
        except creds_mod.CredentialsError as exc:
            print(f"  warn {label}: {exc} (keychain service {service!r})")
            continue
        email = creds_mod.account_email(account) or "unknown"
        scope = "ok  " if credentials.can_read_usage else "FAIL"
        if not credentials.can_read_usage:
            failures += 1
        print(f"  {scope} {label}: {email}, plan {credentials.plan_label}")
        if credentials.expired:
            print(f"  warn {label}: access token expired; open Claude Code to refresh")

    print("usage data")
    for usage in collect(cfg, with_cost=False):
        if usage.error:
            print(f"  warn {usage.label}: {usage.error}")
            continue
        print(
            f"  ok   {usage.label}: 5h {usage.five_hour.percent}%  "
            f"7d {usage.seven_day.percent}%  via {usage.source}"
        )

    print("rendering")
    print(
        f"  ok   type floor {theme.MIN_SIZE}px; hero "
        f"{render_mod.TWO_UP.hero_size}px two-up, {render_mod.ONE_UP.hero_size}px one-up"
    )
    for name, pair in (
        ("track edge/background", (theme.TRACK_EDGE, theme.BG)),
        ("alert/track interior", (theme.ALERT, theme.TRACK_FILL)),
        ("muted text/background", (theme.MUTED, theme.BG)),
    ):
        ratio = theme.contrast(*pair)
        floor = 4.5 if "text" in name else 3.0
        ok = ratio >= floor
        failures += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} {name}: {ratio:.2f}:1 (needs {floor})")

    print("failed" if failures else "all good")
    return 1 if failures else 0


def cmd_history(args) -> int:
    """What the thresholds should have been derived from."""
    cfg = config_mod.load(args.config)
    summary = history_mod.summarize()
    if not summary:
        print(f"no history yet at {history_mod.HISTORY_PATH}")
        print("it accumulates as the daemon runs; check back in a few days")
        return 0

    print(f"{history_mod.HISTORY_PATH}\n")
    for account, windows in sorted(summary.items()):
        print(account)
        for field, stats in windows.items():
            if not stats["samples"]:
                continue
            print(
                f"  {field:10s} n={stats['samples']:<6d} median {stats['median']:5.1f}%  "
                f"p90 {stats['p90']:5.1f}%  max {stats['max']:5.1f}%   "
                f"at/over 60%: {stats['over_60']:.0%}   at/over 85%: {stats['over_85']:.0%}"
            )
    print(
        f"\ncurrent thresholds: warn {cfg.warn_at:.0f}%, alert {cfg.alert_at:.0f}% "
        "(hand-picked; set them from the p90/max above once there is enough data)"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="clawdtv", description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="one tick: collect, render, push if warranted")
    run.add_argument("--force", action="store_true", help="ignore throttle, quiet hours and change detection")
    run.set_defaults(func=cmd_run)

    render_cmd = sub.add_parser("render", help="render a frame locally without pushing")
    render_cmd.add_argument("output", nargs="?", default="out/frame.jpg")
    render_cmd.add_argument("--no-cost", action="store_true", help="skip the ccusage call")
    render_cmd.set_defaults(func=cmd_render)

    check = sub.add_parser("check", help="verify device, credentials, data and palette")
    check.set_defaults(func=cmd_check)

    hist = sub.add_parser("history", help="distribution of observed usage, for grounding thresholds")
    hist.set_defaults(func=cmd_history)

    args = parser.parse_args()
    try:
        return args.func(args)
    except config_mod.ConfigError as exc:
        # The message is written for the person fixing config.toml; a traceback
        # would bury it, and launchd would relog the traceback every tick.
        print(f"config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
