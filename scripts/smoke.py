#!/usr/bin/env python3
"""Verify delivery without exposing log bodies or secrets."""

import json
import os
import time
import urllib.parse
import urllib.request


def query(base, expression, **params):
    url = base + "?" + urllib.parse.urlencode({"query": expression, **params})
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)["data"]["result"]


host = os.environ["ALLOY_HOST"]
prometheus = os.environ.get("PROMETHEUS_QUERY_URL") or os.environ["PROMETHEUS_REMOTE_WRITE_URL"].rsplit("/api/v1/write", 1)[0]
loki = os.environ.get("LOKI_QUERY_URL") or os.environ["LOKI_PUSH_URL"].rsplit(
    "/loki/api/v1/push", 1
)[0]
selector = "host=" + json.dumps(host)
for attempt in range(12):
    try:
        metrics = query(prometheus + "/api/v1/query", "up{" + selector + "}")
        healthy = {m["metric"]["job"] for m in metrics if float(m["value"][1]) == 1}
        containers = query(
            prometheus + "/api/v1/query",
            'container_memory_usage_bytes{job="cadvisor",name!="",' + selector + "}",
        )
        logs = query(
            loki + "/loki/api/v1/query_range",
            "{job=\"docker\"," + selector + "}",
            start=int((time.time() - 900) * 1e9),
            limit=1,
        )
        if {"node", "cadvisor", "alloy"} <= healthy and containers and logs:
            print(f"Delivery verified for {host}: node, cadvisor, alloy and Docker logs")
            break
    except (OSError, ValueError, KeyError):
        pass
    time.sleep(5)
else:
    raise SystemExit(f"Delivery verification failed for {host}")
