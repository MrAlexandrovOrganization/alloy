#!/usr/bin/env python3
"""Verify delivery without exposing log bodies or secrets."""

import json
import os
import time
import urllib.parse

from receiver_http import read_url


def query(base, expression, **params):
    url = base + "?" + urllib.parse.urlencode({"query": expression, **params})
    return json.loads(read_url(url))["data"]["result"]


host = os.environ["ALLOY_HOST"]
prometheus = os.environ.get("PROMETHEUS_QUERY_URL") or os.environ["PROMETHEUS_REMOTE_WRITE_URL"].rsplit("/api/v1/write", 1)[0]
loki = os.environ.get("LOKI_QUERY_URL") or os.environ["LOKI_PUSH_URL"].rsplit(
    "/loki/api/v1/push", 1
)[0]
selector = "host=" + json.dumps(host)
started = time.time()
for attempt in range(12):
    try:
        metrics = query(prometheus + "/api/v1/query", "up{" + selector + "}")
        healthy = {m["metric"]["job"] for m in metrics if float(m["value"][1]) == 1}
        fresh = query(
            prometheus + "/api/v1/query", "timestamp(up{" + selector + "} == 1)",
        )
        current = {m["metric"]["job"] for m in fresh if float(m["value"][1]) >= started}
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
        if {"node", "cadvisor", "alloy", "vmagent"} <= healthy & current and containers and logs:
            print(f"Fresh delivery verified for {host}: node, cadvisor, alloy, vmagent and Docker logs")
            break
    except (OSError, ValueError, KeyError):
        pass
    time.sleep(5)
else:
    raise SystemExit(f"Delivery verification failed for {host}")
