#!/usr/bin/env python3
"""Drain Alloy's first hop before switching/restarting; retain vmagent's backlog."""

import json
import os
import shlex
import subprocess
import time


def read_metrics(docker, container):
    # Alloy's pinned image has no wget. Use the already-pinned queue image in
    # the collector's network namespace; no published port or host route needed.
    return subprocess.check_output(
        docker + ["run", "--rm", "--network", "container:" + container,
                  "--read-only", "--user", "65534:65534", "--cap-drop", "ALL",
                  "--security-opt", "no-new-privileges", "--entrypoint", "wget",
                  "victoriametrics/vmagent:" + os.environ["VMAGENT_VERSION"],
                  "-q", "-T", "5", "-O", "-", "http://127.0.0.1:12345/metrics"],
        text=True,
    )


def main():
    docker = shlex.split(os.environ.get("DOCKER", "docker"))
    compose = shlex.split(os.environ.get("DOCKER_COMPOSE", "docker compose"))
    ids = subprocess.check_output(compose + ["ps", "-a", "-q", "alloy"], text=True).split()
    if not ids:
        return
    if len(ids) != 1:
        raise SystemExit("Expected exactly one Alloy container")
    info = json.loads(subprocess.check_output(docker + ["inspect", ids[0]], text=True))[0]
    environment = dict(entry.split("=", 1) for entry in info["Config"]["Env"] if "=" in entry)
    local = environment.get("PROMETHEUS_REMOTE_WRITE_URL") in {
        "http://vmagent:8429/api/v1/write", "http://alloy-queue-sink:8429/api/v1/write",
    }
    if local and not info["State"]["Running"]:
        print("Restoring stopped local collector; preserving both state volumes")
        return
    if not info["State"]["Running"]:
        raise SystemExit("Old direct writer is stopped; restore and drain it before switching")
    for _ in range(60):
        metrics = read_metrics(docker, ids[0])
        pending = [float(line.split()[-1]) for line in metrics.splitlines()
                   if line.startswith(tuple("prometheus_remote_storage_" + kind + "_pending{"
                                            for kind in ("samples", "histograms", "exemplars")))]
        if pending and sum(pending) == 0:
            print("Alloy first hop drained; existing WAL, log positions and vmagent backlog are retained")
            return
        time.sleep(2)
    raise SystemExit("Alloy first hop has pending samples; refusing to restart it or change destination")


if __name__ == "__main__":
    main()
