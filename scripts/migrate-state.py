#!/usr/bin/env python3
"""Preserve legacy container-layer state before Compose recreates Alloy."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile


def migrate(container=None, volume="observability_alloy_data", backup_dir=None):
    docker = shlex.split(os.environ.get("DOCKER", "docker"))

    def run(*args, **kwargs):
        return subprocess.run(docker + list(args), check=True, **kwargs)

    if container is None:
        compose = shlex.split(os.environ.get("DOCKER_COMPOSE", "docker compose"))
        container = subprocess.check_output(
            compose + ["ps", "-a", "-q", "alloy"], text=True
        ).strip()
    if not container:
        print("No existing Alloy container; no legacy state to migrate")
        return None
    info = json.loads(run("inspect", container, capture_output=True).stdout)[0]
    args = info["Args"]
    if any(a.startswith("--storage.path") for a in args):
        print("Alloy already has an explicit storage path; keeping its volume")
        return None
    image = info["Config"]["Image"]
    run("volume", "create", volume, stdout=subprocess.DEVNULL)
    # Never overwrite state from another deployment or a partial earlier import.
    run(
        "run", "--rm", "--network", "none", "--entrypoint", "sh",
        "-v", f"{volume}:/state", image,
        "-c", 'test -z "$(find /state -mindepth 1 -maxdepth 1 -print -quit)"',
    )
    backup_dir = Path(backup_dir or Path.home() / ".local/state/alloy-migration")
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    was_running = info["State"]["Running"]
    if was_running:
        run("stop", "-t", "30", container, stdout=subprocess.DEVNULL)
    try:
        with tempfile.NamedTemporaryFile(
            prefix="legacy-state-", suffix=".tar", dir=backup_dir, delete=False
        ) as backup:
            backup_path = Path(backup.name)
            run("cp", f"{container}:/data-alloy/.", "-", stdout=backup)
        with backup_path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        with backup_path.open("rb") as source:
            run(
                "run", "--rm", "--network", "none", "-i",
                "--entrypoint", "sh", "-v", f"{volume}:/state", image,
                "-c", "tar -xf - -C /state", stdin=source,
            )
        print(f"Legacy state imported; backup={backup_path}; sha256={digest}")
        print("Legacy container remains stopped; Compose may now recreate it")
        return backup_path
    except BaseException:
        if was_running:
            run("start", container, stdout=subprocess.DEVNULL)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container")
    parser.add_argument("--volume", default="observability_alloy_data")
    parser.add_argument("--backup-dir")
    options = parser.parse_args()
    migrate(options.container, options.volume, options.backup_dir)
