"""Validate generated configuration without disclosing it in public CI output."""

import os
from pathlib import Path
import shlex
import subprocess
import sys

path = Path(sys.argv[1])
command = shlex.split(os.environ.get("DOCKER", "docker")) + [
    "run",
    "--rm",
    "--network",
    "none",
    "-e",
    "ALLOY_HOST=validation",
    "-e",
    "ALLOY_PROCFS_PATH=/proc",
    "-e",
    "ALLOY_ROOTFS_PATH=/rootfs",
    "-e",
    "LOKI_PUSH_URL=http://127.0.0.1:3100/loki/api/v1/push",
    "-e",
    "PROMETHEUS_REMOTE_WRITE_URL=http://127.0.0.1:9090/api/v1/write",
    "-i",
    "--entrypoint",
    "sh",
    "grafana/alloy:" + os.environ["ALLOY_VERSION"],
    "-c",
    "cat > /tmp/check.alloy; /bin/alloy validate /tmp/check.alloy",
]
r = subprocess.run(command, input=path.read_bytes(), capture_output=True)
if r.returncode:
    diagnostics = path.parent / "validation.private.log"
    diagnostics.write_bytes(r.stdout + r.stderr)
    diagnostics.chmod(0o600)
    raise SystemExit(
        "Generated configuration validation failed; diagnostics retained locally"
    )
print("Generated configuration validated")
