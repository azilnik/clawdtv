# Design notes

The decisions behind clawdtv, and the findings that cost time to establish.
The [README](../README.md) covers using it; this covers *why it is like this*,
for whoever touches it next.

## The display

**Bars share one scale.** Every bar spans the same x-range, across rows and
accounts, so their lengths are directly comparable — that comparison is the
whole point of the display, and it is the first thing lost if each bar is sized
by whatever text happens to sit beside it. Reset times ride in the panel header
rather than beside their bars, which is what makes the full-width bars possible.

**One layout per account count.** Two accounts stack in half-height panels,
each row a tag, number, and bar side by side. A single account is not "a pair
with an empty half": it switches to stacked rows — a 56px numeral right-aligned
on its own line, with the bar running the full frame width beneath it. Side by
side, the number column caps how long a bar can be; stacking gives the bar
nearly double the pixels, which is the whole point of having the space. Both
layouts live in `render.Geometry`, and the bars still share one x-range within
a frame.

**Each bar is a pressure gauge, not a progress bar.** While usage is within
pace for the window — the rate that would just reach the reset — the bar is an
ordinary solid fill. Once it runs over pace it splits: the on-pace portion
mutes and the excess stays at full intensity, so the part burning too fast is
the part that catches your eye. There is deliberately no indicator for being
*under* pace; "you are fine" does not need a graphic.

**The pace split needs a real margin.** At the start of a window, elapsed time
is near zero, so pace is near zero, and a bare "over pace" test would flag any
usage at all — ten minutes into a five-hour window, spending 3% would light up.
Usage has to clear pace by five points (`PACE_DEADBAND`) before the bar splits,
so the split means "genuinely ahead of a sustainable rate" rather than "used
something recently".

**Resets appear under pressure, not under closeness.** A reset time joins the
header only once its own window reaches the warn threshold — the same point the
bar turns amber, so it is one rule rather than two. Time remaining does not
decide it: a reset five minutes away on a barely-touched window is not worth
saying, while one three days out on a nearly-spent week is.

It reads as a duration (`5h in 48m`, `7d in 3 days`), not a wall-clock time,
since "in 40m" is directly actionable while "4:30p" makes you do the
subtraction. Durations round **up**: being told relief is two hours away and
getting it in ninety minutes is a pleasant surprise, whereas the reverse is the
display having lied to you. When both windows are under pressure and only one
fits, the fuller one wins — that is the limit you are closer to actually
hitting. A rolled-over window always says `5h reset` regardless of its usage,
because that is the explanation for the `--` the row is about to draw.

**Header text measures itself.** Account labels come from config and can be any
length, so the reset text picks the widest form that actually fits and
degrades: both resets, then both without tags, then the five-hour alone, then
the weekly alone, then nothing. It never overprints the bars.

**Unknown is not zero.** A window whose reset time has passed has rolled over,
so the number we hold describes a window that no longer exists. It renders as
`--`, not as a reassuring low percentage. Staleness is likewise spelled out in
words ("52m old") rather than implied by a dimmer gray.

**Rendering depends on the time it is given, never the time it runs.** Passing
`now` through and then reading the wall clock in one helper made a frame's
meaning drift — once real time passed a fixed state's reset timestamp, that
state rendered as "reset" with no value. Tests passed in the morning and failed
in the afternoon. `now` is threaded through the whole render path, with
regression tests pinning both the cause and the visible symptom.

**The frame clock is quantized to 5 minutes.** An exact clock would make every
frame unique, forcing a flash write every tick to tell you nothing new. At
5-minute resolution it still answers "is this still updating?" while letting
unchanged frames skip the write.

## Color and contrast

The panel is 240×240 in a 1.54" square — roughly 220 pixels per inch. A 16px
glyph is about 1.8mm tall there, so `theme.py` enforces an 18px floor: when
something does not fit, content gets cut rather than shrunk.

**Contrast is a solved constraint, not a taste call.** Something has to clear
3:1 against the background or you cannot see how long a bar could be, and every
fill has to clear 3:1 against whatever sits behind it or you cannot see how
full it is. A single flat mid-gray track satisfies both only barely, and its
brightness makes every empty bar shout. Putting the requirement on a one-pixel
*edge* instead frees the interior to go nearly black: the edge carries 4.07:1
against the background, and fills clear 7:1 or better against the interior
rather than scraping 3:1.

**The alert color is a light coral rather than a saturated red.** That began as
a hard constraint under the old flat track, where a properly alarming red
managed only 2.2:1; against today's dark interior a saturated red would pass
comfortably, so it is now a deliberate choice — the extra blue keeps it
separable from the amber warn color for a red-green colorblind viewer, who
would otherwise see both collapse to the same pale yellow.

**Color never carries meaning alone.** Threshold state is also in the number
and the bar length; staleness is in words. `tests/test_contrast.py` enforces
all of it, and measures on the compressed JPEG rather than the palette
constants, because chroma subsampling is exactly what degrades thin
light-on-dark strokes — including that one-pixel edge. `tools/contactsheet.py`
also renders every state under simulated deuteranopia, protanopia, and
tritanopia.

## Where the numbers come from

Three tiers per account, freshest observation wins, and every number carries
the time it was observed so the display can be honest about age:

1. **A statusline hook file**, written by Claude Code itself while a session
   runs (`tools/statusline-tee.sh`). Free, first-party, exactly current — but
   only exists while you are working.
2. **`cachedUsageUtilization`** in the account's state file, written whenever
   Claude Code fetches usage for its own purposes. Free, but its freshness is
   uncontrolled and it has been observed hours stale.
3. **The OAuth usage endpoint** — always available, but rate-limited per
   account and the only tier that makes a network request. 429s are sticky, so
   the poller keeps per-account fetch times and cooldowns on disk; without
   that, every tick (a fresh process) would look like a first run and hammer
   the endpoint. The endpoint's response shape has already drifted once (a
   `limits[]` array superseding the `five_hour`/`seven_day` objects);
   `sources.py` parses both.

**Tokens are read, never written.** Refresh tokens are single-use, so
refreshing one here would invalidate the copy Claude Code holds and force a
re-login. What clawdtv does instead is start the smallest possible session on
the account in the last half hour before its token would lapse, which makes
Claude Code refresh its own copy — the same cure as opening it by hand, just
early. (`claude auth status` is not enough; it reads the stored token without
exercising it.)

**The keepalive keys off expiry and nothing else.** Each tick is a separate
process, so any memory of past attempts would have to live on disk. Reading the
trigger straight off the token avoids that entirely: a success moves expiry
hours out and silences it, a failure retries on the next tick, and once the
token has actually lapsed it stops trying — renewing there would work, but it
would then retry every five minutes for as long as the account stayed logged
out. Past that point the panel says expired and waits. Those keepalive sessions
are one-word Haiku turns, and they do land in the usage numbers on screen — at
roughly three a day per account that sits inside the rounding, but it is not
zero.

**Cost is opt-in, and validated rather than trusted.** The footer's dollar
figures ship off (`[cost] enabled = false`) — they need Node and are the
slowest thing in a tick, so they exist only for people who want them. When
enabled: ccusage prices from a table it fetches at
runtime; when that fetch fails it falls back to a bundled table that lags new
model releases and prices anything missing at $0 — while still exiting 0 and
returning a well-formed, believable total. A day that really cost $527 came
back as $45 because the model responsible for $482 of it had no entry. Nothing
in the output announces this, so `cost.py` checks every model breakdown and
discards the total if any model burned tokens and was priced at nothing.

The launchd agent also deliberately avoids `ProcessType=Background` and
`LowPriorityIO`: that throttling is what made the pricing fetch fail in the
first place.

## Thresholds are guesses, measured

Amber at 60% and coral at 85% are round numbers picked by hand. Nothing derived
them from how any account is actually used, and observed daily volume has
swung by two orders of magnitude inside a fortnight — the shape fixed cutoffs
fit worst. Pace is the one principled signal on the display: usage against
elapsed time self-adjusts per window with no magic number.

So every reading gets logged to `~/.local/state/clawdtv/history.csv` — plain
CSV, readable without this project, and still readable if it is deleted.
`clawdtv history` prints the observed distribution per account and window
(median, p90, max, how often 60% and 85% are actually reached). After a couple
of weeks that is enough to replace the guesses with measured values in
`config.toml`. Nothing reads the file automatically: grounding a threshold is a
decision to make from the data, not one to let the display quietly make for
itself.

## When something breaks

Run `./.venv/bin/clawdtv check` — it tests each layer and names the broken one.

| Symptom | Likely fix |
|---|---|
| `unreachable at …` | Wrong IP, or the screen lost Wi-Fi. Give it a DHCP reservation in your router so the address stops moving. |
| Uploads succeed, screen never changes | Wrong `theme` number — `4` on the Pro, `3` on the Ultra. `check` compares it to the detected model. |
| `not logged in` / `token expired` on screen | Open Claude Code on that account once (`/login` if asked); it refreshes its own token. |
| Cost shows nothing | It's opt-in — enable it under `[cost]`. If enabled: Node/`npx` missing, or ccusage's price table fetch failed (bad totals are discarded rather than shown wrong). |
| Frame replaced by something else | Something else on your network pushes to the device too (a Home Assistant integration, say). `tools/watch_device.sh <ip>` catches it in the act. |

The tick pipeline, for orientation:

```
launchd, every 5 minutes (config: tick_interval_s)
  └─ per account:  freshest of ─ statusline file (free, live during sessions)
                               ─ Claude Code's own cached usage
                               ─ the OAuth usage endpoint (rate-limited, cached)
     plus today's cost via ccusage (cached 15 min)
  └─ render one 240×240 JPEG → push over your LAN, skipped when unchanged
```

Pushes are skipped when the frame is unchanged, floored at one per two
minutes, and paused during quiet hours — at this cadence the flash-wear math
on the device is decades.

## Device notes

Findings that cost time to establish, verified on a SmallTV-PRO running
firmware `V3.3.76EN`:

- The Picture theme is **4** on the Pro and **3** on the Ultra. The wrong
  number uploads successfully and never displays.
- `/set?img=` is the **GIF** selector. It returns `FAIL` for a JPEG. Picture
  mode is an autoplay album, so the way to control the screen is to keep
  exactly one file in `/image/`; `prune_album` enforces that.
- State JSON lives under `/.sys/` on the Pro (`/.sys/app.json`,
  `/.sys/album.json`), at the root on the Ultra. `current_theme()` tries both.
- Brightness reads back inverted: `/.sys/brt.json` reports `185` for an actual
  70 (255 − 185). Writes are not inverted.
- The HTTP server is loose about the spec (duplicate Content-Length headers,
  trailing bytes after `Connection: close`), so `device.py` speaks HTTP over a
  raw socket and parses forgivingly.
- A reboot can drop the device onto another app, where uploads succeed
  silently while the screen never changes — so every push is followed by one
  cheap state read, and Picture mode is re-asserted only when it drifted.
- The daemon survives the device dropping off the network: it records
  `failing_since`, retries each tick, optionally notifies (`[notify]` in
  config) after two hours, and recovers on its own.

## Teardown philosophy

`tools/kill.sh` is pure bash and curl on purpose: the moment you most want to
pull the plug is when something is broken, so a kill switch that imports the
project it is killing is useless. It waits for launchd to *actually* drop the
job rather than trusting `bootout` to be synchronous, is idempotent, finishes
local teardown even when the device is offline, returns the device to its
clock rather than an empty album — and refuses to edit
`~/.claude/settings.json`, ever. If a statusline entry references clawdtv, it
tells you to remove it by hand rather than rewriting your Claude config behind
your back.

## Maintenance

Everything about the credential store and the usage endpoint is
reverse-engineered and perishable — last verified against Claude Code 2.1.x.
`clawdtv check` exists to tell you which part broke after an update:

- **Keychain**: service name is `Claude Code-credentials`, plus `-` and the
  first eight hex chars of SHA-256 of the config-dir string for non-default
  dirs. Derived in `creds.py`.
- **Usage endpoint**: `GET /api/oauth/usage` with the stored bearer token, an
  `anthropic-beta: oauth-2025-04-20` header, and a `claude-code/<version>`
  User-Agent — without that UA the endpoint drops callers into an aggressively
  rate-limited bucket.
- **Statusline stdin shape**: `rate_limits` with `used_percentage` and
  epoch-second `resets_at`, which differs from both endpoint shapes.
- **ccusage** is pinned (`cost.CCUSAGE_PIN`) so a tool that runs unattended
  does not float to untested versions; bump it deliberately.

Consumer OAuth tokens in third-party tools sit outside the letter of
Anthropic's consumer terms, which were clarified in February 2026 to target
running inference through non-first-party harnesses. clawdtv reads limits and
consumes no inference through the token (the keepalive runs through Claude
Code itself). The statusline and cache tiers are entirely first-party if that
posture ever matters.
