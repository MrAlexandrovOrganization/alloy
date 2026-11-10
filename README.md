# Alloy

VPN network diagnostics use the existing collector plus an optional standard
node_exporter in the host network namespace. See [VPN-MONITORING.md](VPN-MONITORING.md).

## Image version

Alloy, vmagent and the isolated test receiver versions live in
`makefiles/versions.mk`. `make versions` prints them.
Compose, formatting checks and CI use these values through Make. Use
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

The following text formats are also parsed, scoped to stable Compose project/service identities (container renames do not
change the selectors):

| Compose service identity | Format / policy |
| --- | --- |
| `egress-router/egress-router` | sing-box timestamp and explicit severity; INFO `outbound/...: outbound connection to ...` and `inbound/...: inbound connection from/to ...` events become `debug`, with or without a `[connection-id elapsed]` prefix |
| `kafka/kafka-ui` | Java timestamp followed by explicit severity, including `DEBUG` scheduler events |
| `kafka/kafka` | Bracketed timestamp and explicit severity; INFO periodic `QuorumController` summaries from `EventPerformanceMonitor` become `debug`; `PeriodicTaskControlManager` reports for `electUnclean`/`electPreferred` become `debug` only when they generated zero records |
| `redis/redis` | Redis RDB background-save messages (`Saving...`, save start/completion, CoW statistics and `DB saved on disk`) become `debug` |
| `ollama/ollama`, `stash-workers/ollama` | GIN access logs: HTTP 5xx = `error`, 4xx = `warn`, other valid statuses = `info`; successful 2xx loopback `HEAD /` and `GET /api/tags` probes = `debug` |
| All containers with an exact PostgreSQL timestamp/PID/severity prefix | PostgreSQL timestamp/PID/severity prefix; `LOG` = `info`, `DEBUG1`-`DEBUG5` = `debug`; LOG timed checkpoint starts and completion summaries = `debug` |
| `loki/loki` | INFO logfmt stats/metric/limited requests from `metrics.go` become `debug` only with `status=200` and `latency=fast`; existing-table lookups and explicitly listed maintenance/query-start events become `debug` |
| `telemt/telemt` | Rust tracing timestamp, severity and `telemt::...` target; native levels are preserved, including INFO/WARN TLS/SNI rejection events |

For `service="stash/stash"`, JSON `body` (or `msg` if body is absent/empty) equal to
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
`docker compose --project-directory . -f compose/docker-compose.yml config --images alloy`, so runtime, local checks, and CI use the
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

Metrics follow `Alloy -> local vmagent -> central Prometheus on WSL`.
`PROMETHEUS_REMOTE_WRITE_URL` in the host's `.env` is the **central** destination
for vmagent. Compose directs Alloy to `http://alloy-queue-sink:8429/api/v1/write`.
That alias exists only on the private collector network: removing vmagent's
egress network must not invalidate Alloy's cached first-hop connection.
vmagent has no published host port; it uses a private collector network and the
existing trusted `prometheus-net` for outbound access. HTTP remote write is used
because both collectors and the central Prometheus implement that protocol.

`prometheus.remote_write.central`, its WAL and Docker log positions retain their
existing component IDs and `observability_alloy_data` volume. Updates first
start a healthy local queue and wait for a running Alloy's first-hop samples to
drain; vmagent's persistent backlog does not need to drain. `migrate-queue.py`
refuses to switch an undrained/stopped direct writer. Existing state is never
cleared or converted. vmagent uses a separate `observability_vmagent_data` volume
mounted at `/queue`, running as UID/GID 65534 with a read-only root filesystem.
`queue-init` gives that UID ownership of the volume root without rewriting files.

vmagent retries its file-backed backlog after a graceful restart or container
recreation. A 30s send deadline and 60s stop timeout leave room for its in-memory
tail to flush during shutdown. This does not
promise zero loss on SIGKILL, OOM, power loss or disk failure. Alloy's own WAL
still cannot replay reliably after its restart: samples not yet accepted by a
running local vmagent are outside the queue's restart guarantee. Logs retain
their existing pipeline, retries and one-hour age filter.

`VMAGENT_MAX_DISK_USAGE` bounds pending bytes **per central URL**, rather than setting a
24h TTL. At capacity the oldest queued blocks are dropped. Initial host budgets
are 6GiB on server, 3GiB on WSL, 1GiB on Russia/Finland/ASUS and 600MiB on
private-test-server. The latter has little free disk and needs extra care when
its workload increases. The default is 1GiB; set the host override in `.env`.
vmagent's memory limit defaults to 128m. Physical files can additionally retain
part of a consumed chunk (about 512MiB); leave that and OS headroom beyond the
pending-byte budget, especially on private-test-server. Preserve both volumes.

`ALLOY_GOMEMLIMIT` sets Go's soft memory limit independently of
`ALLOY_MEMORY_LIMIT`. The default is 384MiB inside the 512m container; it leaves
room for page cache and non-Go memory. Hostkey Russia uses 192MiB: during the
28 October incident Alloy retained about 480 MiB of Go heap address space while
the heap profile contained only about 59 MiB of live objects. Together with the
other services this caused sustained swapping on the 2 GiB VM. Size this setting
from the live heap and runtime overhead, then check CPU/GC pressure after
activation; setting it below the live heap can increase GC work. Keep both state
volumes and vmagent's disk queue when recreating the collector.

Alloy scrapes vmagent as `job="vmagent"`, with the same `host` and
`service="vmagent"`. Recalculate a day of traffic with
`sum by(host)(rate(vmagent_remotewrite_bytes_sent_total[1h])) * 86400`, allow
headroom, and compare it to the byte limit and real free disk. Track
`vm_persistentqueue_bytes_pending`, `vm_persistentqueue_free_disk_space_bytes`,
`vm_persistentqueue_bytes_dropped_total` and remote-write failures. A full disk,
growing workload or outage beyond the budget can cause loss. Docker Desktop's
virtual filesystem free space is not the Windows drive's physical free space.

Prometheus must accept delayed samples with `storage.tsdb.out_of_order_time_window:
26h` while retaining its 15d history. It may reject older backlog. Queue identity
depends on the destination URL and its ordering: keep them stable. The
`keepDanglingQueues` option preserves old queue directories on URL changes but
does not automatically send them to a different destination; drain before moving
the receiver, or restore the previous URL to deliver that queue.

`make up` / `make recreate` first run `migrate-state`. If the existing container
uses the legacy implicit `/data-alloy` path, migration refuses a non-empty target
volume, stops the collector, writes a private tar backup, imports its complete
state, then lets Compose recreate the collector with explicit persistent storage.
On import failure the old collector is restarted; backups and the source container
are retained. Review a partial target volume manually before retrying, never
blindly overwrite it. `make test` verifies this using isolated Docker containers,
synthetic positions and private temporary backups. It never touches production.

`make test` also exercises a real isolated Alloy/vmagent/Prometheus chain: it
disconnects vmagent's egress while retaining its private first hop, waits for
a file-backed backlog, gracefully recreates both
collector processes with their volumes, restores the receiver and checks original
offline scrape timestamps plus a synthetic 23h-old sample. This is not a 24h soak
test or a power-failure test. Fixtures publish no ports and delete only their own
uniquely named containers, volumes and internal network.

`make replay-check` is an explicit **live fault test**, excluded from check/CI/CD.
It disconnects only vmagent from `prometheus-net` for at least 35s, waits for
file-backed bytes and a drained Alloy first hop, checks missing offline samples
at the receiver, restarts Alloy and vmagent,
restores network aliases in a `finally` block, then verifies arrival of an
original offline scrape timestamp. It does not modify host routes or SSH.
Run it with the host's Make environment and keep an administrative connection
available for recovery if the test process itself is killed while disconnected.

`make smoke` verifies fresh node/cAdvisor/self/vmagent metrics and Docker log arrival
in the configured backends. If the Loki Docker alias is not resolvable from the
host shell, set `LOKI_QUERY_URL` to its tailnet or loopback URL for this check.

Automatic SSH delivery through Hostkey Russia targets all six collectors after
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
The local vmagent queue provides restart replay for samples it has accepted;
Alloy's WAL is only the first hop to that local queue.
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
builds temporary tmpfs views with `/proc/PID/root` links to runtime directories.
The underlying mountpoint directories remain intact, so Docker can mount them
again after graceful restart or SIGKILL. Unix filesystem metrics use a full
runtime-root link inside that view, rather than reporting its 1MiB tmpfs size.
It does not write host files or modify the daemon's mount namespace.
This avoids depending on a hardcoded daemon PID or
mistaking Ubuntu's empty `/var/lib/docker` for Docker Desktop storage.

To check this platform-specific path explicitly, run `make check
ALLOY_DOCKER_DESKTOP=true` against the WSL Docker daemon (from this administrative
Mac, add `DOCKER_HOST=ssh://wsl`). The isolated exporter fixture must still report
named container memory and the real runtime root filesystem after both Docker
restart and SIGKILL/start. It does not collect logs or write to central backends.
Native Linux CI explicitly skips this Docker Desktop-only test.

## Container names

Compose uses explicit service names without replica numbers. Set
`CONTAINER_NAME_PREFIX` (for example `dev-`) for an additional local or CI stack
on the same Docker host. Keep Compose project/service labels and volume names
unchanged; clients address services or explicit network aliases.

## Структура Compose

Файлы Docker Compose находятся в `compose/`; основной файл — `compose/docker-compose.yml`.
Команды запускайте из корня проекта через Makefile: он задаёт `--project-directory`
на корень checkout. Build contexts, bind mounts, `env_file` и секреты остаются
относительными к этому корню; имена Compose project, сервисов, сетей и томов сохранены.
При явном вызове Docker используйте `docker compose --project-directory . -f compose/docker-compose.yml ...`.
Если в приватной `.env` или окружении задан `COMPOSE_FILE`, добавьте `compose/`
к прежним именам файлов, сохранив прежний набор overlays и порядок. Для плавного
перехода Make также распознаёт прежние короткие имена, если файлы уже перенесены.
Не меняйте project name и не удаляйте прежние тома для переноса файлов.

## Log audit, 11 November 2026

The 24-hour Loki snapshot ending at 01:01:45 Moscow time contained 416,512
records across 65 host/service pairs on six hosts (Russia, Finland, WSL, ASUS,
Mac and private-test). This is observed logging activity, not an inventory of
all expected containers. Counts use a fixed-time instant query,
`sum by (host,service,level) (count_over_time({job="docker"}[24h]))`;
they do not reuse the last nonempty value of historical range series.
`server` had no records in this window. Direct SSH to
`server` and `private-test-server` timed out; private-test history was available
in Loki. Running-container inventories were read on the other five hosts.
No production requests, database writes, restarts or deployments were performed.

| Indexed level | Records |
| --- | ---: |
| unknown | 106,709 (25.6%) |
| error | 74,341 |
| warn | 36,074 |
| fatal | 4 |
| info | 149,235 |
| debug | 50,149 |

92,515 unknown records came from Tailscale sidecars, 8,604 from worker Ollama
access logs. PostgreSQL from additional Compose projects and standalone fixtures
accounted for another substantial group. Counts include retries, client
cancellations, operator diagnostics and test containers; they are not counts of
independent incidents or failed user actions. Only samples of each affected
service/level were inspected, not every body in the entire window.

### Prepared changes and validation

* The exact PostgreSQL prefix now works across projects and standalone containers.
  Worker Ollama GIN status parsing uses the existing status policy.
* Native RabbitMQ, Headscale, Xray, GitHub runner and legacy telego prefixes are
  parsed. Notes' Go OTel export failures are explicitly error events.
* A narrow allowlist marks routine Tailscale peer-discovery/netcheck messages
  debug. UDP-probe fallback does not prove that application traffic succeeds.
  Unfamiliar sidecar messages remain unknown.
* The optional watchdog textfile directory is selected by `scripts/start.sh`
  only when present. An explicitly supplied `ALLOY_TEXTFILE_DIRECTORY`, including
  an empty string, overrides discovery. A missing explicit path still reports
  `node_textfile_scrape_error=1`. Finland's existing watchdog remains collected.
  Removing a directory after startup remains an error; adding one requires a
  restart. Watchdog success/freshness must still be checked independently.
* `make check` passed against the pinned Alloy image through Colima: 31 tests,
  with the explicit Docker Desktop regression skipped. Tests run the actual
  production processing stages with synthetic HTTP input; normalized labels,
  detected-level metadata and unchanged bodies are asserted. Empty/present/bad
  explicit textfile paths are exercised through the real exporter and entrypoint.

The other prepared fixes also passed their project `make check`: CLIP ran 14
synthetic tests; Transcriber ran 127 tests with 62 fixture-dependent tests skipped;
Caddy ran its 88 existing HTTP ingress checks and the new real proxy failover
fixtures, plus configuration/static checks. PostgreSQL/RabbitMQ integration and
production rollout were not validated by those local results.

These are local source changes, not a production rollout. Existing history is
unchanged. There is no blanket unknown-to-info or error-to-warning conversion.
Remaining examples include Python traceback fragments (addressed at their HTTP
handler sources), PostgreSQL HINT/STATEMENT context lines, Kafka multiline
continuations and Bot API PID/time output without a native severity. These need
source-specific handling; assigning all of them info would hide failures.

### Significant findings

| Finding and applicability | Evidence | Risk / next step |
| --- | --- | --- |
| Caddy / Grafana access from Russia | 49,378 Caddy error records; samples contain access 502 and separate dial errors to `100.64.0.10:3000` | Intermittent user-facing failure. Both `.10:3000` and `.3:3000` returned healthy Grafana during this audit. Caddy source now supports ordered paths with bounded connection retry; its private upstream list still needs activation. Both paths lead to the same WSL service, not independent HA. |
| Transcriber ingress trace delivery | 7,196 errors and 8,163 warnings; runtime exporter points at `.1:4317`, TCP times out while `.3:4317` accepts connections | Trace loss. Russia overlay corrected to the current Jaeger endpoint in the Transcriber checkout; end-to-end span delivery still needs checking after rollout. |
| Optional textfile input | About 5,767 Russia / 5,760 ASUS Alloy errors; repeated missing `/rootfs/var/lib/vless-relay-watchdog` | Collector configuration fault, fixed locally as above, without changing application severity. |
| Finland cAdvisor filesystem collection | 4,485 Alloy errors; nonexistent Docker overlayfs/container paths in samples | Partial filesystem telemetry. Sampled container IDs were no longer present when inspected; current Docker overlayfs and containerd snapshot directories exist. Check stale-container lifecycle and driver/mount compatibility before changing anything; do not suppress errors or migrate Docker storage casually. |
| CLIP SQLite contention | 89 coordinator errors, `database is locked`; 23,982 combined Mac/WSL worker warnings | Read-side status polling took `BEGIN IMMEDIATE`. The isolated CLIP checkout now uses a read-only connection for status. BUSY/LOCKED become retryable UNAVAILABLE; other DB failures remain ERROR/INTERNAL. Tests verify reads during a held writer transaction. This removes one contention source, not proof of zero runtime lock errors. |
| Transcriber worker diagnostics | 366 combined worker iteration errors; messages previously omitted causes | Worker logs now include safe exception type/RPC code/attempt/delay, and unexpected repeated failures back off up to 60 seconds. No exception message, credential or user content is copied into these fields. |
| Notes search synchronization | 294 warning summaries with `errors=1`, but no cause in sampled summary | Partial sync may persist. Inspect the linked trace and per-item error before changing level or adding retry. |
| Stash processing | 799 errors; sampled failures include unavailable/locked CLIP | Indexing can lag. Validate backlog recovery after CLIP rollout; preserve existing idempotent backfill and stored originals. |
| VPN edge | 1,993 warnings / 476 errors; samples show matcher timeouts and broken/reset connections | Distinguish external client disconnects from tunnel failures with the real watchdog and availability metrics. Do not trigger a restart on every log line. |
| Prometheus overlapping blocks | Five warnings, multiple overlapping TSDB block ranges | Investigate compaction/remote-write history without deleting blocks or clearing storage. |
| Grafana error alert | `provisioning/alerting/rules.yml`: `service-log-errors` is paused, selects only `error`, threshold >5/5m, for 2m | No active notification from this provisioned rule; fatal is omitted. Live alert state was not inspected. Review per-service signals before enabling notifications. |

For the CLIP fix the original local checkout was behind the deployed source and
contained unrelated edits. It was preserved. The clean checkout containing the
prepared fix is `backends/clip-embedder-log-recovery`, branch
`codex/log-recovery`, based on deployed commit `c069101`. Transcriber and Caddy
also had pre-existing edits; only the logging/recovery changes belong to this audit.

### Severity and recovery policy

An ERROR means an operation failed, a terminal job failed, data could not be
persisted, or a dependency remained unavailable beyond that operation's retry
budget. WARN means actionable degradation or retry in progress. A successful
fallback/recovery should produce INFO with the outcome; routine probes and
peer discovery are DEBUG. Recoverable does not mean recovered: retain the error
if the operation ultimately fails, and use health/backlog/freshness metrics to
check that recovery happened. PostgreSQL FATAL may close a session rather than
crash the server; preserve the native level and correlate with availability.

Use bounded backoff with jitter, cancellation, idempotent request IDs and durable
queues for transient failures. Alternate paths must preserve authentication and
reach the same data; they cannot provide HA for a stopped WSL backend. Never
replay an already accepted non-idempotent write just to get rid of an error.
When Mac/WSL inference is unavailable, leave jobs queued; no model fallback on
Russia or activation of retired stacks is part of this work.

Alloy's current CI deployment matrix covers six remote hosts and excludes Mac.
The prepared source is in the separate `log-recovery/alloy`
checkout, branch `codex/log-severity-audit`, based on deployed `d15facb`.
The original Mac checkout was restored to `codex/macbook-container-metrics`,
with its existing private Colima configuration intact. Current origin/main does
not contain that Colima overlay. Mac activation requires integrating the
collector-only fix with its existing configuration; do not replace it with the
remote main deployment unchanged.

After an authorized CI/CD rollout, compare fresh 15-minute/one-hour counts by
host/service/level, check unknown samples and actual queue recovery, and find a
new span in Jaeger. Inventory and collector freshness must be checked separately:
zero errors with missing telemetry is not success. Notifications were not enabled.
