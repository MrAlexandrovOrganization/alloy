#!/usr/bin/env python3
"""SSH transport for a checked revision; never print key material."""

import os
from pathlib import Path
import re
import subprocess
import tempfile

HOSTS = {
    "server": "100.64.0.1",
    "hostkey-russia": "100.64.0.5",
    "hostkey-finland": "100.64.0.6",
    "private-test-server": "100.64.0.4",
    "wsl": "100.64.0.3",
}


def main():
    project = Path(__file__).resolve().parents[1].name
    target = os.environ["DEPLOY_TARGET"]
    sha = os.environ["DEPLOY_SHA"]
    operation = os.environ.get("DEPLOY_OPERATION", "deploy")
    if target not in HOSTS or (project != "alloy" and target != "wsl"):
        raise SystemExit("Target is not allowed for this project")
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or operation not in {"probe", "deploy"}:
        raise SystemExit("Invalid deploy request")
    key = os.environ["DEPLOY_SSH_KEY"]
    known_hosts = os.environ["DEPLOY_KNOWN_HOSTS"]
    if not key.strip() or not known_hosts.strip():
        raise SystemExit("Deploy credentials and pinned host keys are required")
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix="observability-ssh-") as directory:
        root = Path(directory)
        (root / "key").write_text(key.rstrip() + "\n")
        (root / "known_hosts").write_text(known_hosts.rstrip() + "\n")
        lines = [
            "Host obs-*",
            f"  IdentityFile {root / 'key'}",
            f"  UserKnownHostsFile {root / 'known_hosts'}",
            "  StrictHostKeyChecking yes",
            "  IdentitiesOnly yes",
            "  BatchMode yes",
            "  ConnectTimeout 15",
            "  ServerAliveInterval 15",
            "  ServerAliveCountMax 3",
            "  RequestTTY no",
            "  ForwardAgent no",
        ]
        lines += ["Host obs-gateway", "  HostName 193.187.93.245",
                  "  User observability-deploy", "  HostKeyAlias obs-hostkey-russia"]
        for name, address in HOSTS.items():
            lines += [f"Host obs-{name}", f"  HostName {address}", "  User maxim", f"  HostKeyAlias obs-{name}"]
            lines += ["  ProxyJump obs-gateway"]
        config = root / "config"
        config.write_text("\n".join(lines) + "\n")
        subprocess.run(
            ["ssh", "-F", str(config), f"obs-{target}", f"{operation} {sha}"],
            check=True,
            timeout=900,
        )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode)
    except subprocess.TimeoutExpired:
        raise SystemExit("SSH deployment timed out; inspect the target before retrying")
