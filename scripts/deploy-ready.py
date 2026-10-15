#!/usr/bin/env python3
"""Avoid restarting a WAL collector while its receivers are unavailable."""

import os
import time
import urllib.request


def main():
    prometheus = os.environ.get("PROMETHEUS_QUERY_URL") or os.environ["PROMETHEUS_REMOTE_WRITE_URL"].rsplit("/api/v1/write", 1)[0]
    loki = os.environ.get("LOKI_QUERY_URL") or os.environ["LOKI_PUSH_URL"].rsplit("/loki/api/v1/push", 1)[0]
    for attempt in range(12):
        try:
            for url in (prometheus + "/-/ready", loki + "/ready"):
                with urllib.request.urlopen(url, timeout=5) as response:
                    if response.status != 200:
                        raise OSError("Receiver is not ready")
            print("Central receivers are ready; collector may be updated")
            return
        except OSError:
            time.sleep(5)
    raise SystemExit("Central receiver unavailable; keeping the existing Alloy process and WAL")


if __name__ == "__main__":
    main()
