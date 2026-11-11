#!/usr/bin/env python3
"""Root-installed forced SSH command. Canonical source belongs to Alloy."""

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

def repository(project, config):
    if project not in config["projects"] or not re.fullmatch(r"[a-z][a-z0-9-]*", project):
        raise ValueError("Project is not allowed on this host")
    url = config.get("repositories", {}).get(project, "")
    if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git", url):
        raise ValueError("A pinned repository URL is required in the private policy")
    return url


def request(value):
    parts = value.split()
    if len(parts) != 2 or parts[0] not in {"probe", "deploy"} or not re.fullmatch(r"[0-9a-f]{40}", parts[1]):
        raise ValueError("Expected probe/deploy and one full commit SHA")
    return parts


def run(args, cwd, capture=False):
    result = subprocess.run(args, cwd=cwd, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None, timeout=600)
    return result.stdout.strip() if capture else ""


def deploy(project, sha, config, base, state):
    url = repository(project, config)
    directory = base / project
    if run(["git", "rev-parse", "--show-toplevel"], directory, True) != str(directory.resolve()):
        raise ValueError("Unexpected repository root")
    if run(["git", "branch", "--show-current"], directory, True) != "main":
        raise ValueError("Deploy checkout must be on main")
    if run(["git", "status", "--porcelain", "--untracked-files=no"], directory, True):
        raise ValueError("Tracked local changes must be reconciled before deployment")
    previous = run(["git", "rev-parse", "HEAD"], directory, True)
    run(["git", "fetch", "--quiet", "--no-tags", url, "refs/heads/main"], directory)
    if run(["git", "rev-parse", "FETCH_HEAD"], directory, True) != sha:
        raise ValueError("Stale workflow: main has changed; deploy the latest checked revision")
    run(["git", "merge", "--ff-only", "--quiet", sha], directory)
    if run(["git", "rev-parse", "HEAD"], directory, True) != sha:
        raise ValueError("Activated checkout does not match the requested revision")
    command = ["sudo", "-n", "make", "deploy"] if config["sudo_make"] else ["make", "deploy"]
    run(command, directory)
    receipt = {"host": config["host"], "project": project, "sha": sha,
               "previous_sha": previous, "verified_at": datetime.now(timezone.utc).isoformat()}
    temporary = state / f"{project}.json.new"
    temporary.write_text(json.dumps(receipt) + "\n")
    temporary.replace(state / f"{project}.json")
    print(json.dumps(receipt), flush=True)


def main():
    os.umask(0o077)
    if len(sys.argv) != 2:
        raise ValueError("A project must be fixed by authorized_keys")
    project = sys.argv[1]
    operation, sha = request(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    config = json.loads(Path("/etc/observability-deploy.json").read_text())
    repository(project, config)
    if operation == "probe":
        print(f"SSH deploy access verified: {config['host']}/{project}")
        return
    state = Path.home() / ".local/state/observability-deploy"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / f"{project}.lock").open("a") as lock:
        deadline = time.monotonic() + 300
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Another deployment is still running")
                time.sleep(1)
        deploy(project, sha, config, Path(config.get("base_path", str(Path.home() / "projects/infra/observability"))), state)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError:
        print("Deployment command failed; inspect checkout and runtime before retrying", file=sys.stderr)
        sys.exit(1)
    except (ValueError, TimeoutError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)

