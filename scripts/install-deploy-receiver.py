#!/usr/bin/env python3
"""Explicit administrative bootstrap with a private JSON policy on stdin."""

import base64
import datetime
import json
import os
from pathlib import Path
import pwd
import re
import sys

if os.geteuid() != 0:
    raise SystemExit("Receiver bootstrap requires root")
payload = json.load(sys.stdin)
config = payload["config"]
if (
    not re.fullmatch(r"[a-z][a-z0-9-]*", config.get("host", ""))
    or not config.get("projects")
    or type(config.get("sudo_make")) is not bool
):
    raise SystemExit("Invalid private receiver policy")
repositories = config.get("repositories", {})
if any(not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git", repositories.get(project, "")) for project in config["projects"]):
    raise SystemExit("Each allowed project requires a private pinned repository URL")
for project, key in payload["keys"].items():
    if not re.fullmatch(r"[a-z][a-z0-9-]*", project) or not re.fullmatch(
        r"ssh-ed25519 [A-Za-z0-9+/=]+", key
    ):
        raise SystemExit("Invalid repository public key")
user = pwd.getpwnam(config.get("user", "maxim"))
install = Path("/usr/local/lib/observability-deploy")
install.mkdir(parents=True, exist_ok=True)
receiver = install / "receiver.py"
receiver.write_bytes(base64.b64decode(payload["receiver"], validate=True))
receiver.chmod(0o755)
config_file = Path("/etc/observability-deploy.json")
config_file.write_text(json.dumps(config) + "\n")
config_file.chmod(0o600)
os.chown(config_file, user.pw_uid, user.pw_gid)
home = Path(user.pw_dir)
(home / ".ssh").mkdir(exist_ok=True, mode=0o700)
auth = home / ".ssh/authorized_keys"
old = auth.read_text() if auth.exists() else ""
state = home / ".local/state/observability-deploy"
state.mkdir(parents=True, exist_ok=True, mode=0o700)
backup = state / (
    "authorized_keys."
    + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
)
backup.write_text(old)
backup.chmod(0o600)
lines = old.splitlines()
for project, key in payload["keys"].items():
    marker = "observability-cd-" + project + "-20261015"
    lines = [line for line in lines if not line.endswith(" " + marker)]
    if project in config["projects"]:
        command = (
            "/usr/bin/python3 /usr/local/lib/observability-deploy/receiver.py "
            + project
        )
        lines.append('restrict,command="' + command + '" ' + key + " " + marker)
temporary = auth.with_name("authorized_keys.observability-new")
temporary.write_text("\n".join(lines) + "\n")
temporary.chmod(0o600)
os.chown(temporary, user.pw_uid, user.pw_gid)
temporary.replace(auth)
for path in (home / ".ssh", state, backup):
    os.chown(path, user.pw_uid, user.pw_gid)
print("Restricted receiver installed from private policy")
