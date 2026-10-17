#!/usr/bin/env python3
"""Explicit live fault test: disconnect only vmagent egress, restart, verify replay."""

import json
import os
import shlex
import subprocess
import time
import urllib.parse
import urllib.request


def main():
    docker = shlex.split(os.environ.get("DOCKER", "docker"))

    def run(*args):
        return subprocess.check_output(docker + list(args), text=True)

    def singleton(service):
        ids = run("ps", "-q", "--filter", "label=com.docker.compose.project=alloy",
                  "--filter", "label=com.docker.compose.service=" + service).split()
        if len(ids) != 1:
            raise SystemExit("Expected one running Alloy project service: " + service)
        return ids[0]

    host = os.environ["ALLOY_HOST"]
    central = os.environ.get("PROMETHEUS_QUERY_URL") or os.environ["PROMETHEUS_REMOTE_WRITE_URL"].rsplit("/api/v1/write", 1)[0]
    collector, agent = singleton("alloy"), singleton("vmagent")
    info = json.loads(run("inspect", agent))[0]
    network = "prometheus-net"
    original = info["NetworkSettings"]["Networks"].get(network)
    if not original or len(info["NetworkSettings"]["Networks"]) != 2:
        raise SystemExit("Expected vmagent's private collector network and prometheus-net")
    with urllib.request.urlopen(central + "/-/ready", timeout=10):
        pass

    def source_timestamp(at):
        query = 'timestamp(up{job="alloy",host=' + json.dumps(host) + "})"
        url = central + "/api/v1/query?" + urllib.parse.urlencode({"query": query, "time": at})
        with urllib.request.urlopen(url, timeout=10) as response:
            result = json.load(response)["data"]["result"]
        return float(result[0]["value"][1]) if result else None

    def pending():
        metrics = run("exec", agent, "wget", "-q", "-O", "-", "http://127.0.0.1:8429/metrics")
        return sum(float(line.rsplit(" ", 1)[1]) for line in metrics.splitlines()
                   if line.startswith("vm_persistentqueue_bytes_pending{"))

    disconnected = False
    try:
        run("network", "disconnect", network, agent)
        disconnected = True
        offline_start = time.time()
        print(f"{host}: vmagent egress disconnected; collecting offline samples", flush=True)
        time.sleep(35)
        probe_time = offline_start + 25
        before = source_timestamp(probe_time)
        if before is not None and before >= offline_start + 5:
            raise SystemExit("Receiver still got offline samples; fault isolation was ineffective")
        queued = pending()
        if queued <= 0:
            raise SystemExit("No file-backed backlog; refusing an inconclusive restart test")
        for container in (collector, agent):
            run("restart", "-t", "60", container)
        print(f"{host}: both processes restarted with {int(queued)} queued bytes; restoring egress", flush=True)
    finally:
        if disconnected:
            aliases = [arg for alias in original.get("Aliases", []) or [] for arg in ("--alias", alias)]
            run("network", "connect", *aliases, network, agent)

    for _ in range(60):
        after = source_timestamp(probe_time)
        if after is not None and offline_start + 5 <= after <= probe_time:
            print(json.dumps({"host": host, "replay_verified": True, "queued_bytes": int(queued),
                              "probe_time": probe_time, "source_timestamp_before_recovery": before,
                              "original_offline_sample_timestamp": after}), flush=True)
            return
        time.sleep(2)
    raise SystemExit("Offline sample was not delivered after restart and network recovery")


if __name__ == "__main__":
    main()
