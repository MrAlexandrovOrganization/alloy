"""Query receivers directly or through the optional collector VPN container."""

import os
import shlex
import subprocess
import urllib.request


def read_url(url, timeout=10):
    container = os.environ.get("QUERY_CONTAINER")
    if not container:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    docker = shlex.split(os.environ.get("DOCKER", "docker"))
    try:
        return subprocess.check_output(
            docker + ["exec", container, "wget", "-q", "-T", str(timeout), "-O", "-", url],
            stderr=subprocess.DEVNULL, timeout=timeout + 5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise OSError("Receiver query through collector container failed") from error
