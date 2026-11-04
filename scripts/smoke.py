#!/usr/bin/env python3
"""Verify delivery without exposing log bodies or secrets."""

import gzip
import http.client
import ipaddress
import json
import os
import socket
import time
import urllib.parse
import urllib.request


def decode(response):
    body = response.read()
    if response.headers.get("Content-Encoding", "").lower() == "gzip":
        body = gzip.decompress(body)
    return json.loads(body)["data"]["result"]


def query(base, expression, **params):
    url = base + "?" + urllib.parse.urlencode({"query": expression, **params})
    headers = {"Accept-Encoding": "gzip"}
    mss = int(os.environ.get("READINESS_TCP_MSS", "0"))
    if mss:
        # Native VM→WSL HTTP needs smaller SYN MSS on the measured path.
        # Scope this to the private IPv4 tailnet; no host routes/sysctls change.
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "http" or parsed.username is not None or parsed.password is not None
                or ipaddress.ip_address(parsed.hostname) not in ipaddress.ip_network("100.64.0.0/10")
                or not 256 <= mss <= 1460):
            raise ValueError("Readiness MSS requires a private IPv4 tailnet HTTP endpoint")
        port = parsed.port or 80
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection_socket:
            connection_socket.settimeout(10)
            connection_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG, mss)
            connection_socket.connect((parsed.hostname, port))
            connection = http.client.HTTPConnection(parsed.hostname, port, timeout=10)
            connection.sock = connection_socket
            try:
                connection.request("GET", parsed.path + "?" + parsed.query, headers=headers)
                with connection.getresponse() as response:
                    if response.status != 200:
                        raise OSError("Readiness endpoint returned non-success status")
                    return decode(response)
            finally:
                connection.close()
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        return decode(response)


host = os.environ["ALLOY_HOST"]
prometheus = os.environ.get("PROMETHEUS_QUERY_URL") or os.environ["PROMETHEUS_REMOTE_WRITE_URL"].rsplit("/api/v1/write", 1)[0]
loki = os.environ.get("LOKI_QUERY_URL") or os.environ["LOKI_PUSH_URL"].rsplit(
    "/loki/api/v1/push", 1
)[0]
selector = "host=" + json.dumps(host)
core = selector + ',job=~"node|cadvisor|alloy|vmagent"'
started = time.time()
for attempt in range(12):
    try:
        metrics = query(prometheus + "/api/v1/query", "count(count by(job) (up{" + core + "} == 1))")
        healthy = any(float(m["value"][1]) == 4 for m in metrics)
        fresh = query(
            prometheus + "/api/v1/query", "min(timestamp(up{" + core + "} == 1))",
        )
        current = bool(fresh) and all(float(m["value"][1]) >= started for m in fresh)
        containers = query(
            prometheus + "/api/v1/query",
            'count(container_memory_usage_bytes{job="cadvisor",name!="",' + selector + "})",
        )
        logs = query(
            loki + "/loki/api/v1/query",
            'sum(count_over_time({job="docker",' + selector + '}[15m]))',
        )
        network_ready = True
        if os.environ.get("HOST_NETWORK_METRICS_ADDRESS"):
            network = query(
                prometheus + "/api/v1/query",
                'timestamp(up{service="host-network",' + selector + '} == 1)',
            )
            failures = query(
                prometheus + "/api/v1/query",
                'count(node_scrape_collector_success{service="host-network",' + selector + '} == 0)',
            )
            network_ready = bool(network) and all(float(m["value"][1]) >= started for m in network) and not failures
        container_ready = any(float(m["value"][1]) > 0 for m in containers)
        logs_ready = any(float(m["value"][1]) > 0 for m in logs)
        if healthy and current and container_ready and logs_ready and network_ready:
            print(f"Fresh delivery verified for {host}: node, cadvisor, alloy, vmagent and Docker logs")
            break
        print(f"Delivery pending: core={healthy}, fresh={current}, containers={container_ready}, logs={logs_ready}, network={network_ready}")
    except (OSError, ValueError, KeyError) as error:
        print("Readiness query failed: " + type(error).__name__)
    time.sleep(5)
else:
    raise SystemExit(f"Delivery verification failed for {host}")
