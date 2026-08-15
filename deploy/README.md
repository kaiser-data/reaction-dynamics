# Keeping the capture daemon running

Two layers, and they catch different failures. Installing one and assuming it
covers the other is the mistake this directory exists to prevent.

| Layer | Catches | Blind to |
|---|---|---|
| launchd / systemd (here) | the process dying, and the machine rebooting | a process that is alive and no longer receiving |
| the watchdog, in `capture.py` | a socket that went silent or reported itself down | the process being killed, OOM, a reboot |

The watchdog is the one that catches the failure that motivated this project — a
listener up but not listening, which looks exactly like a quiet room. Supervision
is the cruder layer underneath it. Neither is redundant.

Whatever happens, the window shows up in the `capture_gaps` ledger: a supervised
restart records the time it was down as a `cold_start` (or `crash`) gap, so
recovery never quietly erases the outage.

## Install (macOS, launchd)

Runs as a **user agent**, so it needs no root and reads your `.env` as you. It
only runs while you are logged in — for an always-on box, use a `LaunchDaemon`
under `/Library/LaunchDaemons` instead and make sure `.env` is readable by the
user you set, without making it world-readable.

```bash
cd /path/to/reaction-dynamics
mkdir -p logs                       # launchd will not create it, and will fail silently-ish

PY=$(which python3.12)              # must be an interpreter that has slack_sdk
REPO=$(pwd)
CHANNEL=C0XXXXXXXXX

sed -e "s|__PYTHON__|$PY|g" -e "s|__REPO__|$REPO|g" -e "s|__CHANNEL__|$CHANNEL|g" \
    deploy/com.reaction-dynamics.capture.plist \
    > ~/Library/LaunchAgents/com.reaction-dynamics.capture.plist

plutil -lint ~/Library/LaunchAgents/com.reaction-dynamics.capture.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.reaction-dynamics.capture.plist
```

Check, then stop:

```bash
launchctl print gui/$(id -u)/com.reaction-dynamics.capture | head -20
tail -f logs/capture.log

launchctl bootout gui/$(id -u)/com.reaction-dynamics.capture
```

If your macOS predates `bootstrap`, the equivalents are `launchctl load -w` and
`launchctl unload -w`.

## Install (Linux, systemd)

Shown as a **user service**, which needs no root. For a server, drop the unit in
`/etc/systemd/system/`, add `User=` and `Group=`, and drop `--user` throughout.

```bash
cd /path/to/reaction-dynamics
mkdir -p ~/.config/systemd/user

PY=$(which python3.12)
REPO=$(pwd)
CHANNEL=C0XXXXXXXXX

sed -e "s|__PYTHON__|$PY|g" -e "s|__REPO__|$REPO|g" -e "s|__CHANNEL__|$CHANNEL|g" \
    deploy/reaction-capture.service \
    > ~/.config/systemd/user/reaction-capture.service

systemd-analyze verify ~/.config/systemd/user/reaction-capture.service
systemctl --user daemon-reload
systemctl --user enable --now reaction-capture
```

Check, then stop:

```bash
systemctl --user status reaction-capture
journalctl --user -u reaction-capture -f

systemctl --user stop reaction-capture
```

To survive logout on a server, `loginctl enable-linger $USER`. Without it the
user manager exits when you log out and takes the daemon with it — a silent way
to stop capturing.

## Two settings that are not defaults for a reason

**`StartLimitIntervalSec=0`** (systemd). By default systemd stops trying after 5
starts in 10 seconds and leaves the unit dead. For this daemon that is the worst
available outcome: it stops capturing *and* stops recording that it stopped.
Disabling the limiter means it retries forever; `RestartSec` is what keeps that
from being a hot loop.

**`RestartSec` / `ThrottleInterval` = 15s.** Each restart writes one ledger row
covering the window since the previous start. During a sustained outage this
controls how many rows you get, not whether the total is right — a day down is
~5,800 rows at 15s. Lower recovers faster after a blip; raise it if you would
rather have fewer, coarser rows.

## What a restart does to the ledger

Each start records the dark window since the previous start, and nothing else.
Windows tile; they do not overlap. Ten crash-restarts 30 seconds apart report
five minutes dark, not fifty.

This is worth stating because it was not true until supervision was written.
`reconcile_startup` measured from the last *captured event*, and a run that died
before capturing anything left that mark untouched — so every restart re-measured
the same window and the ledger summed them. Ninety seconds of real downtime
reported as one hundred and eighty. See `test_a_supervised_crash_loop_reports_
real_downtime` in `tests/test_watchdog.py`.

The residual, stated so nobody discovers it as a surprise: the window between
process start and a successful connect (well under a second, normally) is not
recorded on a successful start. That is a bounded undercount accepted to remove
an unbounded overcount. When the connect *fails*, the next start measures from
that point, so the window is recorded after all.

## Verifying supervision actually works

Killing the process is the whole point, so test it:

```bash
pkill -f 'capture.py --channel'          # SIGTERM: clean, gap closed and stamped
sleep 20 && tail -3 logs/capture.log     # should show a fresh start

pkill -9 -f 'capture.py --channel'       # SIGKILL: no chance to clean up
sleep 20
.venv/bin/python -c "
import sys; sys.path.insert(0,'seed'); import store
c = store.connect(None)
for g in c.execute('SELECT reason, started_at, ended_at FROM capture_gaps ORDER BY id DESC LIMIT 3'):
    print(dict(g))"
```

The `SIGKILL` case is the one worth watching: it should produce a `crash` gap on
the next start. If it produces nothing, supervision restarted the daemon but the
ledger is not recording — which is the failure mode this whole component exists
to make impossible.
