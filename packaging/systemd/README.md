# rpm-mcp systemd units

User-scope units that run the ingestion worker on an hourly schedule. The MCP
server itself is **not** a daemon — every client launches its own stdio
process — so only the worker needs a timer.

## Install

```
mkdir -p ~/.config/systemd/user ~/.config/rpm-mcp
cp packaging/systemd/rpm-mcp-worker.{service,timer} ~/.config/systemd/user/

# Optional env overrides (DATABASE_URL, WORKER_CONCURRENCY, LOG_FORMAT=json):
$EDITOR ~/.config/rpm-mcp/worker.env

systemctl --user daemon-reload
systemctl --user enable --now rpm-mcp-worker.timer
```

The `.service` expects the repo checkout at `~/projects/rpm-mcp` and the `uv`
binary at `~/.local/bin/uv`. Override either by editing the unit file or
dropping a `[Service]` override with `systemctl --user edit rpm-mcp-worker`.

## Observe

```
systemctl --user list-timers rpm-mcp-worker.timer
journalctl --user -u rpm-mcp-worker.service -f
systemctl --user status rpm-mcp-worker.service
```

Set `LOG_FORMAT=json` in `worker.env` for structured journal entries that
`journalctl -o json` can parse.

## Schedule

- `OnCalendar=hourly` -- fires every hour on the hour
- `RandomizedDelaySec=15min` -- spreads load across the hour
- `Persistent=true` -- catches up if the machine was asleep at the scheduled time

Adjust the cadence by editing the `.timer` and re-running `systemctl --user
daemon-reload`.
