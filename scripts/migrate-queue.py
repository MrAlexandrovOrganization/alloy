#!/usr/bin/env python3
"""Drain the old direct-to-central writer before its first switch to vmagent."""

import json
import os
import shlex
import subprocess
import time


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
    if environment.get("PROMETHEUS_REMOTE_WRITE_URL") == "http://vmagent:8429/api/v1/write":
        print("Local queue already configured; preserving both state volumes")
        return
    if not info["State"]["Running"]:
        raise SystemExit("Old direct writer is stopped; restore and drain it before switching")
    for _ in range(60):
        metrics = subprocess.check_output(
            docker + ["exec", ids[0], "wget", "-q", "-O", "-", "http://127.0.0.1:12345/metrics"],
            text=True,
        )
        pending = [float(line.split()[-1]) for line in metrics.splitlines()
                   if line.startswith(tuple("prometheus_remote_storage_" + kind + "_pending{"
                                            for kind in ("samples", "histograms", "exemplars")))]
        if pending and sum(pending) == 0:
            print("Old direct writer drained; existing Alloy WAL and log positions are retained")
            return
        time.sleep(2)
    raise SystemExit("Old writer has pending samples; refusing to restart it or change destination")


if __name__ == "__main__":
    main()
