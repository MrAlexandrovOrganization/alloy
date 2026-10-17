"""Explicit Docker Desktop runtime regression, using an isolated exporter only."""

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time
import unittest
import uuid


@unittest.skipUnless(os.environ.get("ALLOY_DOCKER_DESKTOP") == "true", "Requires explicit Docker Desktop environment")
class DesktopRestart(unittest.TestCase):
    def docker(self, *args, **kwargs):
        return subprocess.check_output(self.cli + list(args), **kwargs)

    def test_exporters_survive_graceful_restart_and_kill(self):
        self.cli = shlex.split(os.environ.get("DOCKER", "docker"))
        image = "grafana/alloy:" + os.environ["ALLOY_VERSION"]
        helper = "victoriametrics/vmagent:" + os.environ["VMAGENT_VERSION"]
        name = "alloy-desktop-test-" + uuid.uuid4().hex[:12]
        self.addCleanup(lambda: subprocess.run(self.cli + ["rm", "-f", name], capture_output=True))
        self.docker("create", "--name", name, "--privileged", "--pid", "host",
                    "--cgroupns", "host", "--network", "none",
                    "-e", "ALLOY_DOCKER_DESKTOP=true", "-e", "ALLOY_ROOTFS_PATH=/rootfs",
                    "-v", "/:/rootfs:ro", "-v", "/var/lib/docker:/var/lib/docker:ro",
                    "-v", "/run:/run:ro", "-v", "/var/run/docker.sock:/var/run/docker.sock:ro",
                    "--entrypoint", "sh", image, "/etc/alloy/start.sh", "run", "/etc/alloy/config.alloy")
        self.docker("cp", str(Path(__file__).parents[1] / "scripts/start.sh"), name + ":/etc/alloy/start.sh")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.alloy"
            path.write_text('''prometheus.exporter.cadvisor "containers" {
  docker_only = true
  store_container_labels = false
  allowlisted_container_labels = ["com.docker.compose.service"]
}
prometheus.exporter.unix "host" {
  procfs_path = "/proc"
  rootfs_path = sys.env("ALLOY_ROOTFS_PATH")
  filesystem {
    fs_types_exclude = "^$"
  }
}
''')
            path.chmod(0o644)
            self.docker("cp", str(path), name + ":/etc/alloy/config.alloy")
        self.docker("start", name)

        def get(component):
            return self.docker("run", "--rm", "--network", "container:" + name,
                               "--entrypoint", "wget", helper, "-q", "-T", "5", "-O", "-",
                               "http://127.0.0.1:12345/api/v0/component/" + component + "/metrics",
                               text=True, stderr=subprocess.DEVNULL)

        def verify():
            deadline = time.monotonic() + 90
            count, sizes = 0, []
            while time.monotonic() < deadline:
                try:
                    containers = get("prometheus.exporter.cadvisor.containers")
                    count = sum(1 for line in containers.splitlines()
                                if line.startswith("container_memory_usage_bytes{") and re.search(r'name="[^"}]+"', line))
                    node = get("prometheus.exporter.unix.host")
                    sizes = [float(line.rsplit(" ", 1)[1]) for line in node.splitlines()
                             if line.startswith("node_filesystem_size_bytes{") and 'mountpoint="/"' in line]
                    if count and sizes and max(sizes) > 16 * 1024 * 1024:
                        return
                except subprocess.CalledProcessError:
                    pass
                time.sleep(2)
            self.fail(f"Named container metrics: {count}; root filesystem sizes: {sizes}")

        verify()
        self.docker("restart", "-t", "30", name)
        verify()
        self.docker("kill", name)
        self.docker("start", name)
        verify()
        print("Docker Desktop exporters verified after graceful restart and SIGKILL; mountpoints remain intact")
