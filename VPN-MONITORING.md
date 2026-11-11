# VPN monitoring architecture

Each host keeps its existing Alloy collector and vmagent disk queue. Alloy's
embedded node_exporter supplies CPU/memory/filesystem metrics; embedded cAdvisor
supplies container resources, packet/byte counters and optional TCP/UDP gauges.
Application metrics come from vpn-manager's existing `/metrics`. Prometheus and
Grafana remain on the configured central receiver. No collector sidecar is added to each VPN container.

For native Linux network statistics an optional **standard node_exporter** container
joins the host network namespace. This is intentional: mounting host `/proc` into a
bridged collector does not by itself make netlink or `/proc/net` describe the host
network. The existing embedded unix exporter keeps its identity for other metrics.
The network exporter has no Docker socket, host filesystem mount or added
capabilities, runs as nobody, and is limited to 64 MiB/0.2 CPU. Its image is pinned
in `makefiles/versions.mk`. It binds only an explicitly configured tailnet address.

In the host's private `.env`, opt in with:

```dotenv
# Synthetic example; select this host’s private bind address.
HOST_NETWORK_METRICS_ADDRESS=100.64.0.99:19100
ALLOY_CADVISOR_NETWORK_DISABLED=[]
```

`make init/up/recreate` registers a file-SD target, labelled `service=host-network`
and `scope=host`, and selects `compose/compose.network-metrics.yml`. Other hosts
keep their existing services and cAdvisor defaults. `make check` validates both
Compose combinations without starting production services. Changing these two
private settings takes effect on the next normal Git/CI deployment. Rollback:
clear the address, restore the default disabled list from `.env.example`, and
recreate Alloy; explicitly stop/remove only the `node-network` service from its
overlay if it is no longer desired. Existing storage and VM routes are untouched.

The network endpoint is scraped by local Alloy every 15 seconds; vmagent forwards
the samples to central Prometheus. Select `service="host-network"` when querying
host TCP/netstat/conntrack metrics. Network bytes exclude loopback, Docker/veth,
tailnet and WireGuard interfaces to avoid adding multiple copies of each packet.
They still include other applications using the host's physical interfaces.

Conntrack occupancy uses node_exporter's standard sysctl collector for
`net.netfilter.nf_conntrack_count` / `nf_conntrack_max`. These hosts do not expose
`/proc/net/stat/nf_conntrack`; the dedicated conntrack collector otherwise reports
an incomplete collection despite successfully reading count/limit. No sysctl is
modified and no additional capabilities are needed.

Delivery smoke checks use aggregate PromQL and LogQL: fresh core jobs, a positive
named-container count, a positive recent Docker-log count and successful network
collectors. They do not fetch log bodies or all service series. This also keeps
readiness responses bounded on constrained network paths. Optional
`READINESS_TCP_MSS=1000` affects only readiness HTTP sockets to IPv4 tailnet
addresses. The default is the OS policy. TCP_MAXSEG is set before connect;
unsupported endpoint/setting combinations fail rather than bypassing checks.
Host routes, sysctls and application sockets remain unchanged.

TCP/UDP container metrics are gauges despite cAdvisor's `_total` suffix; never
apply `rate` to connection counts. IPv4/IPv6 are distinct metric names. They count
all sockets of the container network namespace, including internal connections,
not users or logical VLESS streams. The opt-in is per host because these collectors
are disabled by default and add work proportional to the number of sockets.

An optional external watchdog can test an actual authenticated tunnel connection.
Its bounded `metrics.prom` is read through Alloy's standard textfile collector.
Freshness is separate from success: a stopped timer cannot leave a permanent
green status. Service healthchecks and `/metrics` availability alone do not
establish end-to-end connectivity. Application dashboards belong to their
service packages; namespace counters are not a durable usage ledger.
The kernel TCP queue values from node_exporter mix LISTEN backlog
counts with other sockets' queue bytes; the diagnostic dashboard instead uses
socket memory, SYN states and ListenOverflows/ListenDrops as saturation indicators.

Sources: [node_exporter Docker deployment](https://github.com/prometheus/node_exporter#docker),
[Alloy cAdvisor](https://grafana.com/docs/alloy/latest/reference/components/prometheus/prometheus.exporter.cadvisor/),
[Alloy textfile collector](https://grafana.com/docs/alloy/latest/reference/components/prometheus/prometheus.exporter.unix/#textfile).
