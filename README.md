# Alloy

## Image version

The Alloy image version lives in `makefiles/versions.mk`. `make versions` prints it.
Compose, formatting checks and CI all use this value through Make. Use
`make up` or `make recreate` to activate configuration/image changes.

## Host identity and persistent state

`make` exports `ALLOY_HOST`, defaulting to the machine's hostname. Set it in the
local `.env` (for example `ALLOY_HOST=server`, `ALLOY_HOST=hostkey-russia`, or
`ALLOY_HOST=wsl`) to use stable inventory names. Direct Compose commands must
provide `ALLOY_HOST` as well as the image version.

New Docker log streams include `host` and `service`. Service selection is:

1. Explicit container label `observability.service`, when non-empty.
2. Compose `<project>/<service>`, such as `notes-bot/core`.
3. Container name for containers outside Compose.

Existing `container`, `compose_service`, `compose_project`, `job`, and `level`
labels remain available. Historical streams are not relabeled. For example,
query `{host="server", service="notes-bot/core"}` for newly collected logs.
No `environment` label is added.

Alloy explicitly uses `--storage.path=/var/lib/alloy/data`, matching the
`observability_alloy_data` volume. Docker log positions therefore survive
container recreation. This is collector state, not a durable queue of unsent
logs: the current `loki.write` pipeline still uses its existing retry policy.
Records older than one hour are still dropped by the existing processing rule.

Before the first production recreation, `make up`/`make recreate` run the
legacy state migration described below. Keep its private backup until ingestion
and position persistence have been verified. Do not run `down -v`.

## Log Levels

Caddy uses the existing Docker stdout → Alloy → Loki pipeline; it does not need
to join `loki-net`. Its access logs are JSON with `service=caddy` and a bounded
`site` field. The existing JSON `level` normalization applies automatically.
Request objects, response headers and user IDs are removed in Caddy before
collection, including subscription URL credentials. Docker's event timestamp
remains the ingestion timestamp source; the body also preserves Caddy's UTC Unix
`ts`. No Caddy trace IDs are synthesized. Query `{container="caddy"}` in Grafana.
Delivery and redaction were verified using an invalid synthetic subscription
request and querying the resulting record in Loki and through Grafana.

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
| `redis` | Redis RDB background-save messages (`Saving...`, save start/completion, CoW statistics and `DB saved on disk`) become `debug` |
| `ollama-ollama-1` | GIN access logs: HTTP 5xx = `error`, 4xx = `warn`, other valid statuses = `info`; successful 2xx loopback `HEAD /` and `GET /api/tags` probes = `debug` |
| `stash-postgres-1`, `subscriptions-bot-postgres-1`, `subscription-publisher-postgres-1`, `subscription-catalog-postgres-1` | PostgreSQL timestamp/PID/severity prefix; `LOG` = `info`, `DEBUG1`-`DEBUG5` = `debug`; LOG timed checkpoint starts and completion summaries = `debug` |
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

The exact INFO message `adjusting end timerange from future to now` from
`composite_store_entry.go` is also debug. The distinct message `whole timerange
in future, yield empty resultset` remains INFO: it can explain missing results
and warrants checking the requested time range and clock alignment.

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
version is pinned in `makefiles/versions.mk`. Formatting commands resolve it with
`docker compose config --images alloy`, so runtime, local checks, and CI use the
same image. To upgrade Alloy, change `ALLOY_VERSION` in `makefiles/versions.mk`.

```sh
make format           # Format config.alloy in place
make format-check     # Check without modifying config.alloy
make config-check  # Validate Docker Compose configuration (read-only)
make check         # All read-only checks; also used by CI
make install-hooks # Install the pre-commit hook once per clone
```

Both formatting commands pass the configuration to Docker through stdin and
capture output in a local temporary file, without bind-mounting temporary paths
(including on Colima). `format` only overwrites the source after the formatter
succeeds. A Docker or syntax error leaves the source unchanged. `CONFIG=path`
can select another file for manual formatting. `format-check` checks Alloy syntax and formatting, while
`config-check` validates Compose and Alloy component definitions without starting
the collector. Runtime delivery checks are performed separately. No services
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

## Multi-host metrics and migration

Python 3.11+, Make, Docker and Compose are prerequisites. `make install` is not
needed: runtime/tool images use the pinned Alloy release. Each host uses its own
`.env` based on `.env.example`; on WSL set the receiver URLs to the local Docker
aliases, on the VMs use WSL's tailnet endpoints. The host name is passed explicitly
into the container. No Windows exporter or environment label is configured.

Alloy scrapes cAdvisor (embedded), Linux (embedded node exporter), itself and
`$MONITORING_DATA_DIR/prometheus-targets/*.{yml,json}` every 15 seconds. It joins
`prometheus-net` and `caddy-metrics-net`, preserving existing application/Caddy
Docker aliases. Caddy's target must be registered in the server's local file SD.
Container metrics use the existing `cadvisor` job; node metrics use `node`.
All metrics carry `host`, and node/container/service targets include `service`.
The Docker Desktop deployment on WSL reports the Linux Docker runtime; Windows
metrics are intentionally outside this configuration.

`prometheus.remote_write.central` stores its WAL under the same persistent
storage path as log positions. Unsent samples expire after 24h; acknowledged
samples are cleaned by segment compaction (hourly, minimum age 5m). This is a
bounded retry buffer, not a guarantee for outages longer than a day or a full disk.
Logs retain the existing pipeline, retries and one-hour age filter.

`make up` / `make recreate` first run `migrate-state`. If the existing container
uses the legacy implicit `/data-alloy` path, migration refuses a non-empty target
volume, stops the collector, writes a private tar backup, imports its complete
state, then lets Compose recreate the collector with explicit persistent storage.
On import failure the old collector is restarted; backups and the source container
are retained. Review a partial target volume manually before retrying, never
blindly overwrite it. `make test` verifies this using isolated Docker containers,
synthetic positions and private temporary backups. It never touches production.

`make smoke` explicitly verifies node/cAdvisor/self metrics and Docker log arrival
in the configured backends. If the Loki Docker alias is not resolvable from the
host shell, set `LOKI_QUERY_URL` to its tailnet or loopback URL for this check.

Automatic SSH delivery through Hostkey Russia targets all five collectors after
CI validation. It uses separate repository keys, pinned SSH host keys and a
forced receiver that activates the exact checked commit. `DEPLOY_ENABLED`
controls activation; [DEPLOY.md](DEPLOY.md) documents bootstrap and recovery.
Host secrets, state and migration backups never enter Git.

`systemd/observability-forward@.{socket,service}` are optional native Linux TCP
forwarders, bound only to loopback. Install the templates under
`/etc/systemd/system`, set `OBSERVABILITY_HUB` in `/etc/observability-forward.env`,
reload systemd and enable only the required socket instances. On the old server,
ports 3000/3100/9090 forward to WSL so existing local tools keep working.
Grafana on Docker Desktop uses its own persistent tailnet sidecar to access
remote datasources; native Ubuntu loopback proxies are not used for that path.
No Windows metrics, new public listeners or changes to host exit-node routes
are needed.

Alloy is pinned to v1.20.1. This release includes a WAL record decoding correction
([upstream #7180](https://github.com/grafana/alloy/pull/7180)) and cAdvisor with
support for Docker's containerd image store. The former v1.7.1 failed the
outage/restart replay test and could not read container metrics on new hosts.
The outage plus collector restart test also failed on v1.20.1: do not assume
that retained WAL samples will be resent after the Alloy process restarts.
The 24h buffer covers receiver/network outages while the collector keeps running.
Keep a cold snapshot of the complete Alloy volume before an upgrade; do not
change component IDs or clear the volume. Explicit relabeling keeps the `node`
and `cadvisor` jobs stable instead of exporter-generated `integrations/*` names.
Only Compose project/service and `observability.service` container labels are
exported, avoiding unbounded deployment/configuration labels.
The read-only `/run` mount supplies the containerd socket needed by Docker's
overlayfs image store and udev metadata. The runtime smoke check requires metrics
for named containers, not only a healthy cAdvisor scrape of the root cgroup.
`cgroup: host` exposes the runtime's cgroup tree through the container's native
`/sys`; do not bind-mount Ubuntu's `/sys` over it on Docker Desktop. On Docker
Desktop set `ALLOY_PROCFS_PATH=/proc` to use the runtime's procfs. These metrics
describe the Linux runtime, rather than the Windows host.
Also set `ALLOY_DOCKER_DESKTOP=true`: the startup wrapper finds the current
host-PID `dockerd`, detaches the collector's private Ubuntu bind mounts and
reads the runtime root/image metadata through `/proc/PID/root` symlinks.
It does not write host files or modify the daemon's mount namespace.
This avoids depending on a hardcoded daemon PID or
mistaking Ubuntu's empty `/var/lib/docker` for Docker Desktop storage.
