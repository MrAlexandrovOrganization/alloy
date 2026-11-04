#!/usr/bin/env python3
"""Register the optional standard host-network exporter; no metric parsing."""

import ipaddress
import json
import os
from pathlib import Path
import tempfile


def register(directory, address):
    target = directory / "host-network.json"
    if not address:
        target.unlink(missing_ok=True)
        return
    host, port = address.rsplit(":", 1)
    if ipaddress.ip_address(host) not in ipaddress.ip_network("100.64.0.0/10") or not 1 <= int(port) <= 65535:
        raise ValueError("host network metrics must bind a private tailnet address")
    text = json.dumps([{"targets": [address], "labels": {"service": "host-network", "scope": "host"}}]) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=".host-network-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            os.fchmod(stream.fileno(), 0o644)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == "__main__":
    register(Path(os.environ["MONITORING_DATA_DIR"]) / "prometheus-targets",
             os.environ.get("HOST_NETWORK_METRICS_ADDRESS", ""))
