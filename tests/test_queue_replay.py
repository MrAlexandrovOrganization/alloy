"""Outage/recreation test with synthetic data and isolated Docker resources only."""

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time
import unittest
import urllib.parse
import uuid


class QueueReplay(unittest.TestCase):
    def docker(self, *args, **kwargs):
        return subprocess.check_output(self.cli + list(args), **kwargs)

    def setUp(self):
        self.cli = shlex.split(os.environ.get("DOCKER", "docker"))
        compose = shlex.split(os.environ.get("DOCKER_COMPOSE", "docker compose"))
        config = json.loads(subprocess.check_output(compose + ["config", "--format", "json"]))
        self.vm = config["services"]["vmagent"]
        self.alloy_image = config["services"]["alloy"]["image"]
        self.prom_image = "prom/prometheus:" + os.environ["TEST_PROMETHEUS_VERSION"]
        self.prefix = "alloy-queue-test-" + uuid.uuid4().hex[:12]
        self.network = self.prefix + "-net"
        self.agent = self.prefix + "-vmagent"
        self.collector = self.prefix + "-alloy"
        self.receiver = self.prefix + "-prometheus"
        self.volumes = [self.prefix + suffix for suffix in ("-queue", "-alloy", "-prometheus")]
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup)
        self.docker("network", "create", "--internal", self.network)
        for volume in self.volumes:
            self.docker("volume", "create", volume)
        self.docker("run", "--rm", "--network", "none", "--entrypoint", "sh",
                    "-v", self.volumes[0] + ":/queue", self.vm["image"],
                    "-c", "chown 65534:65534 /queue")
        self.create_agent()
        self.write_config("prometheus.yml", "global:\n  scrape_interval: 1s\nscrape_configs: []\n"
                          "storage:\n  tsdb:\n    out_of_order_time_window: 26h\n")
        self.docker("create", "--name", self.receiver, "--network", self.network,
                    "--network-alias", "receiver", "-v", self.volumes[2] + ":/prometheus",
                    self.prom_image, "--config.file=/etc/prometheus/prometheus.yml",
                    "--web.enable-remote-write-receiver")
        self.docker("cp", str(Path(self.tmp.name) / "prometheus.yml"),
                    self.receiver + ":/etc/prometheus/prometheus.yml")
        self.create_collector()
        self.docker("start", self.agent, self.receiver, self.collector)
        self.wait_for(lambda: self.query('up{job="fixture"}'), "initial delivery")

    def cleanup(self):
        for container in (self.collector, self.agent, self.receiver):
            subprocess.run(self.cli + ["rm", "-f", container], capture_output=True)
        for volume in self.volumes:
            subprocess.run(self.cli + ["volume", "rm", volume], capture_output=True)
        subprocess.run(self.cli + ["network", "rm", self.network], capture_output=True)
        self.tmp.cleanup()

    def write_config(self, name, content):
        path = Path(self.tmp.name) / name
        path.write_text(content)
        path.chmod(0o644)  # Synthetic config must be readable by the non-root receiver.

    def create_agent(self):
        command = ["-remoteWrite.url=http://receiver:9090/api/v1/write" if arg.startswith("-remoteWrite.url=")
                   else arg for arg in self.vm["command"]]
        self.docker("create", "--name", self.agent, "--network", self.network,
                    "--network-alias", "vmagent", "--user", self.vm["user"],
                    "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "-v", self.volumes[0] + ":/queue",
                    self.vm["image"], *command)

    def create_collector(self):
        self.write_config("config.alloy", '''prometheus.scrape "fixture" {
  targets = [{"__address__" = "vmagent:8429"}]
  job_name = "fixture"
  scrape_interval = "1s"
  scrape_timeout = "1s"
  forward_to = [prometheus.remote_write.central.receiver]
}
prometheus.remote_write "central" {
  endpoint { url = "http://vmagent:8429/api/v1/write" }
}
''')
        self.docker("create", "--name", self.collector, "--network", self.network,
                    "-v", self.volumes[1] + ":/state", self.alloy_image, "run",
                    "--storage.path=/state", "/etc/alloy/config.alloy")
        self.docker("cp", str(Path(self.tmp.name) / "config.alloy"),
                    self.collector + ":/etc/alloy/config.alloy")

    def get(self, url):
        return self.docker("exec", self.agent, "wget", "-q", "-T", "3", "-O", "-", url,
                           text=True, stderr=subprocess.DEVNULL)

    def query(self, expression):
        url = "http://receiver:9090/api/v1/query?" + urllib.parse.urlencode({"query": expression})
        return json.loads(self.get(url))["data"]["result"]

    def wait_for(self, condition, description):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                result = condition()
                if result:
                    return result
            except (subprocess.CalledProcessError, ValueError, KeyError):
                pass
            time.sleep(1)
        self.fail("Timed out waiting for " + description)

    def test_offline_samples_survive_recreation_of_both_processes(self):
        self.docker("stop", "-t", "30", self.receiver)
        offline_start = time.time()
        old_ms = int((offline_start - 23 * 3600) * 1000)
        # Explicit old timestamps exercise the receiver's out-of-bounds window.
        sample = f'queue_durability_probe{{case="day-old"}} 123 {old_ms}\n'
        self.docker("exec", "-i", self.agent, "sh", "-c",
                    "cat > /tmp/probe; wget -q -O - --post-file=/tmp/probe "
                    "http://127.0.0.1:8429/api/v1/import/prometheus", input=sample.encode())

        def disk_pending():
            metrics = self.get("http://127.0.0.1:8429/metrics")
            return any(float(value) > 0 for value in re.findall(
                r'^vm_persistentqueue_bytes_pending\{[^\n]+\} ([0-9.e+]+)$', metrics, re.M))

        self.wait_for(disk_pending, "actual file-backed backlog")
        # Allow Alloy's 5s batch deadline to put offline scrapes in vmagent too.
        time.sleep(7)
        before_restart = time.time()
        for container in (self.collector, self.agent):
            self.docker("stop", "-t", "60", container)
            state = json.loads(self.docker("inspect", container))[0]["State"]
            self.assertEqual(state["ExitCode"], 0, "graceful stop must finish before timeout")
            self.docker("rm", container)
        self.create_agent()
        self.create_collector()
        self.docker("start", self.agent, self.collector)
        self.wait_for(disk_pending, "reopened backlog while receiver is still offline")
        self.docker("start", self.receiver)

        old = self.wait_for(lambda: self.query('queue_durability_probe{case="day-old"}[26h]'),
                            "day-old sample after recreation")
        self.assertEqual(old[0]["values"], [[old_ms / 1000, "123"]])

        def offline_scrapes():
            result = self.query('up{job="fixture"}[5m]')
            return result and any(offline_start <= ts < before_restart for ts, value in result[0]["values"])

        self.wait_for(offline_scrapes, "Alloy samples scraped before restart during the outage")
        self.wait_for(lambda: not disk_pending(), "queue to drain after recovery")
        print("Replay verified: original offline scrape timestamps and a 23h-old sample survived recreation")
