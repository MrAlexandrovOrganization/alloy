# Alloy

## Log Levels

The Docker pipeline sets both the indexed `level` label and the
`detected_level` structured metadata to the same normalized severity. Explicit
metadata prevents Loki's independent severity inference from disagreeing with
stream selectors. Severity normalization does not rewrite source log bodies;
the existing ANSI decolorization still applies.

Severity is read in order from JSON `severity_text`, JSON `level`, or logfmt
`level`. Logfmt parsing applies to all containers whose lines start with a
key-value pair, including Go slog's `time=... level=...` output.

Canonical values are `trace`, `debug`, `info`, `warn`, `error`, `fatal`, and
`unknown`. Values are lowercased and trimmed. Aliases are mapped as follows:

| Input | Canonical level |
| --- | --- |
| `warning` | `warn` |
| `err` | `error` |
| `notice`, `information`, `informational` | `info` |
| `critical`, `crit`, `panic`, `emerg`, `emergency`, `alert` | `fatal` |

The following text formats are also parsed, scoped to the deployed container
names (update the selectors if containers are renamed):

| Container | Format / policy |
| --- | --- |
| `egress-router` | sing-box timestamp and explicit severity; INFO `outbound/...: outbound connection to ...` and `inbound/...: inbound connection from/to ...` events become `debug`, with or without a `[connection-id elapsed]` prefix |
| `kafka-kafka-ui-1` | Java timestamp followed by explicit severity, including `DEBUG` scheduler events |
| `kafka` | Bracketed timestamp and explicit severity; INFO periodic `QuorumController` summaries from `EventPerformanceMonitor` become `debug`; `PeriodicTaskControlManager` reports for `electUnclean`/`electPreferred` become `debug` only when they generated zero records |
| `ollama-ollama-1` | GIN access logs: HTTP 5xx = `error`, 4xx = `warn`, other valid statuses = `info`; successful 2xx loopback `HEAD /` and `GET /api/tags` probes = `debug` |
| `stash-postgres-1` | PostgreSQL timestamp/PID/severity prefix; `LOG` = `info`, `DEBUG1`-`DEBUG5` = `debug`; LOG timed checkpoint starts and completion summaries = `debug` |
| `loki-loki-1` | INFO logfmt stats/metric/limited requests from `metrics.go` become `debug` only with `status=200` and `latency=fast`; existing-table lookups and explicitly listed maintenance/query-start events become `debug` |
| `telemt` | Rust tracing timestamp, severity and `telemt::...` target; native levels are preserved, including INFO/WARN TLS/SNI rejection events |

For `stash-stash-1`, JSON `body` (or `msg` if body is absent/empty) equal to
`embedding backfill: processing` is downgraded from `info` to `debug`. Other
messages and warning/error levels are unchanged. This is a collector-side rule
because the deployed backfill implementation is absent from the local checkout.

These rules do not drop records. Known routine events become filterable debug
logs, while warnings and errors retain their severity. Other INFO events are
not downgraded. GIN severity is a status-based policy, not a native log level.
Ollama's `ollama list` healthcheck requests `/api/tags`. The loopback rule also
covers manual local calls to that endpoint: access logs cannot distinguish them
from probes. Non-loopback requests retain their status-based severity.

Loki rules require a line starting with `level=` and match parsed fields, not
query contents or source line numbers. Slow/failed requests retain their original
level. The maintenance allowlist matches exact caller/message pairs for table
listing, compaction, source-index cleanup, file downloads, empty retention marks,
WAL checkpoints, stream ownership recalculation and table synchronization. Query
start messages from `engine.go`/`roundtrip.go` are debug regardless of eventual
outcome; slow/failed completion records are not downgraded. Unknown messages and
warning/error variants are preserved. Loki and PostgreSQL checkpoint timing
details remain available at debug; no slow
checkpoint duration threshold is inferred by this pipeline.

Grafana's `ngalert.state.manager` / `Detected stale state entry` remains at INFO,
even with `state=Normal reason=`. It indicates an alert instance missing from
evaluations, not just cache housekeeping. The logged state is the previous state,
before Grafana assigns `MissingSeries`; suppressing this can hide lost coverage.
See [Grafana v12.0.0 stale-state handling](https://github.com/grafana/grafana/blob/v12.0.0/pkg/services/ngalert/state/manager.go#L519-L567).

Missing or unsupported levels (including other unstructured text) become `unknown`,
not `info`. Severity words inside message text are not used to guess a level.
Numeric severity fields are not interpreted.

Use lowercase selectors such as `{job="docker", level="error"}`. For volume
queries, group by `level` or `detected_level`, not a reparsed raw JSON field.
With `| json`, a body field named `level` can appear as `level_extracted`;
it remains the original application value, not the normalized label.

Only newly ingested logs use these rules. Historical labels and metadata are
not rewritten, so time ranges spanning deployment can still show mixed values.
This normalizes severity, not the applications' different JSON message schemas.

## Checks and Formatting

Formatting requires Docker Compose and a running Docker daemon. The Alloy image
is pinned only in `docker-compose.yml`. Formatting commands resolve it with
`docker compose config --images alloy`, so runtime, local checks, and CI use the
same image. To upgrade Alloy, change `services.alloy.image` in that file.

```sh
make fmt           # Format config.alloy in place
make fmt-check     # Check without modifying config.alloy
make config-check  # Validate Docker Compose configuration (read-only)
make check         # All read-only checks; also used by CI
make install-hooks # Install the pre-commit hook once per clone
```

Both formatting commands pass the configuration to Docker through stdin and
capture output in a local temporary file, without bind-mounting temporary paths
(including on Colima). `fmt` only overwrites the source after the formatter
succeeds. A Docker or syntax error leaves the source unchanged. `CONFIG=path`
can select another file for manual formatting. Alloy v1.7.1 has no standalone
`validate` command: `fmt-check` checks Alloy syntax and formatting, while
`config-check` validates Compose, not Alloy component semantics. No services
are started by `make check`.

## Commit Hook

Install the pre-commit framework separately from the project/runtime dependencies:

```sh
uv tool install pre-commit
make install-hooks
```

The executable must be on PATH, or supply a command override, for example
`make install-hooks PRE_COMMIT=/path/to/pre-commit`. Docker must be running
when committing Alloy changes. CI uses `make check` directly and does not
require the framework.

The local hook in `.pre-commit-config.yaml` autoformats only staged
`config.alloy` changes. Pre-commit temporarily hides unstaged edits, runs the
formatter, then restores them. Fixes change the working tree, never automatically
stage changes, and fail that attempt so you can review, stage the desired fixes,
and retry. For partial staging, use `git add -p config.alloy`, not an unconditional
`git add`: if fixes conflict with unstaged edits, pre-commit rolls back the fixes
and restores those edits. Resolve/review the formatting and staging before retrying.
Unrelated files and deletions do not invoke the formatter.

Installation refuses any configured `core.hooksPath` (including inherited or
empty values) and any existing hook, including an existing framework hook or
`pre-commit.legacy` that the framework would otherwise silently execute.
Inspect unknown hooks/configuration manually; the installer never changes Git
configuration or silently chains hooks.

For clones with the original custom Alloy hook, explicitly run:

```sh
make migrate-hooks
```

Migration accepts only the exact known original hook content, rejects symlinks
and existing backups, and preserves the old hook as
`.git/hooks/pre-commit.alloy-legacy` (not executed by the framework). If installation
fails, the original hook is restored. `.githooks/pre-commit` is retained unchanged
as a legacy reference, not the active hook definition. No migration is automatic.
