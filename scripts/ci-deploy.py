#!/usr/bin/env python3
"""Deploy a checked revision using private, pinned SSH transport configuration."""

import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def configuration(raw):
    config = json.loads(raw)
    targets = config.get("targets", [])
    if not isinstance(targets, list) or not targets:
        raise ValueError("A private target policy is required")
    nodes = [*targets, *([config["gateway"]] if config.get("gateway") else [])]
    aliases = set()
    for node in nodes:
        for name in ("alias", "user", "key_alias"):
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", node.get(name, "")):
                raise ValueError("Invalid SSH identity field")
        if (
            not re.fullmatch(r"[A-Za-z0-9_.:-]+", node.get("host", ""))
            or type(node.get("port", 22)) is not int
            or not 1 <= node.get("port", 22) <= 65535
        ):
            raise ValueError("Invalid SSH destination")
        if node["alias"] in aliases:
            raise ValueError("Duplicate SSH alias")
        aliases.add(node["alias"])
    return config


def main():
    sha = os.environ["DEPLOY_SHA"]
    operation = os.environ.get("DEPLOY_OPERATION", "deploy")
    if not re.fullmatch(r"[a-f0-9]{40}", sha) or operation not in {"probe", "deploy"}:
        raise ValueError("Invalid deploy request")
    config = configuration(os.environ["DEPLOY_TRANSPORT_JSON"])
    bundle = b""
    chunks = (
        os.environ.get("OBSERVABILITY_BUNDLE_1", "")
        + os.environ.get("OBSERVABILITY_BUNDLE_2", "")
        + os.environ.get("OBSERVABILITY_BUNDLE_3", "")
    )
    if chunks:
        bundle = base64.b64decode(chunks, validate=True)
        expected = os.environ["OBSERVABILITY_BUNDLE_SHA256"]
        if hashlib.sha256(bundle).hexdigest() != expected:
            raise ValueError("Private input checksum mismatch")
        if len(bundle) > 2 * 1024 * 1024 or not bundle.startswith(b"\x1f\x8b"):
            raise ValueError("Invalid private input envelope")
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix="deployment-") as directory:
        root = Path(directory)
        (root / "key").write_text(os.environ["DEPLOY_SSH_KEY"].rstrip() + "\n")
        (root / "known").write_text(os.environ["DEPLOY_KNOWN_HOSTS"].rstrip() + "\n")
        lines = [
            "Host *",
            f"  IdentityFile {root / 'key'}",
            f"  UserKnownHostsFile {root / 'known'}",
            "  StrictHostKeyChecking yes",
            "  IdentitiesOnly yes",
            "  BatchMode yes",
            "  ForwardAgent no",
            "  RequestTTY no",
            "  ConnectTimeout 20",
            "  ServerAliveInterval 15",
            "  ServerAliveCountMax 3",
            "  HostKeyAlgorithms ssh-ed25519",
            "  KexAlgorithms curve25519-sha256",
            "  IPQoS none",
        ]
        nodes = [
            *config["targets"],
            *([config["gateway"]] if config.get("gateway") else []),
        ]
        for node in nodes:
            lines += [
                f"Host {node['alias']}",
                f"  HostName {node['host']}",
                f"  Port {node.get('port', 22)}",
                f"  User {node['user']}",
                f"  HostKeyAlias {node['key_alias']}",
            ]
            if node in config["targets"] and config.get("gateway"):
                lines += ["  ProxyJump " + config["gateway"]["alias"]]
        (root / "config").write_text("\n".join(lines) + "\n")
        failures = []
        for index, node in enumerate(config["targets"], 1):
            result = subprocess.run(
                [
                    "ssh",
                    "-F",
                    str(root / "config"),
                    node["alias"],
                    f"{operation} {sha}",
                ],
                input=bundle,
                capture_output=True,
                timeout=900,
            )
            # Receiver output can contain private inventory. Never copy it to
            # a public Actions log; runtime receipts remain on the target.
            if result.returncode:
                failures.append(index)
                print(
                    f"Deployment target #{index}: failed (receiver diagnostics are private)",
                    flush=True,
                )
            else:
                print(f"Deployment target #{index}: verified", flush=True)
        if failures:
            raise ValueError("One or more deployment targets failed")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired):
        raise SystemExit(
            "Deployment failed; inspect private transport policy and receiver state"
        ) from None
