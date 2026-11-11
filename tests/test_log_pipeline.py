"""Exercise the deployed Alloy stages with isolated synthetic HTTP log input."""

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


class LogPipelineTests(unittest.TestCase):
    def docker(self, *args):
        return subprocess.check_output(
            self.cli + list(args), text=True, stderr=subprocess.STDOUT
        )

    def setUp(self):
        self.cli = shlex.split(os.environ.get("DOCKER", "docker"))
        self.name = "alloy-log-test-" + uuid.uuid4().hex[:12]
        self.image = "grafana/alloy:" + os.environ["ALLOY_VERSION"]
        self.helper = "victoriametrics/vmagent:" + os.environ["VMAGENT_VERSION"]
        self.root = Path(__file__).resolve().parents[1]
        self.addCleanup(
            lambda: subprocess.run(
                self.cli + ["rm", "-f", self.name], capture_output=True
            )
        )

    def start(self, config, *environment, watchdog=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.alloy"
            path.write_text(config)
            self.docker(
                "create",
                "--name",
                self.name,
                "--network",
                "none",
                *environment,
                "--entrypoint",
                "sh",
                self.image,
                "/etc/alloy/start.sh",
                "run",
                "/etc/alloy/test.alloy",
            )
            self.docker("cp", str(path), self.name + ":/etc/alloy/test.alloy")
            self.docker(
                "cp",
                str(self.root / "scripts/start.sh"),
                self.name + ":/etc/alloy/start.sh",
            )
            if watchdog:
                metrics = Path(directory) / "rootfs/var/lib/vless-relay-watchdog"
                metrics.mkdir(parents=True)
                (metrics / "metrics.prom").write_text("synthetic_watchdog_success 1\n")
                self.docker(
                    "cp", str(Path(directory) / "rootfs"), self.name + ":/rootfs"
                )
        self.docker("start", self.name)

    def request(self, url, payload=None):
        args = [
            "run",
            "--rm",
            "--network",
            "container:" + self.name,
            "--entrypoint",
            "wget",
            self.helper,
            "-q",
            "-T",
            "3",
            "-O",
            "-",
        ]
        if payload is not None:
            args += [
                "--header=Content-Type: application/json",
                "--post-data=" + json.dumps(payload),
            ]
        return self.docker(*args, url)

    def wait_for(self, action):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                result = action()
                if result:
                    return result
            except subprocess.CalledProcessError:
                pass
            time.sleep(0.2)
        self.fail(
            "Isolated Alloy did not produce the expected result: "
            + self.docker("logs", self.name)[-3000:]
        )

    def test_severity_and_body_preservation(self):
        import sys

        sys.path.insert(0, str(self.root / "scripts"))
        import observability

        config = observability.render(self.root, public=True)["config.alloy"].decode()
        # Extend the generic pipeline with synthetic parser inputs only.
        for phase, needle in [
            ("parse", "\t// Prefer explicit JSON severity"),
            ("normalize", "\tstage.labels {"),
        ]:
            stages = (self.root / "tests/fixtures" / (phase + ".alloy")).read_text()
            indented = (
                "\n".join("\t" + line if line else "" for line in stages.splitlines())
                + "\n\n"
            )
            config = config.replace(needle, indented + needle, 1)
        pipeline = (
            'loki.process "docker" {'
            + config.split('loki.process "docker" {', 1)[1].split(
                'loki.write "loki" {', 1
            )[0]
        )
        pipeline = pipeline.replace(
            "loki.write.loki.receiver", "loki.echo.fixture.receiver"
        )
        self.start(
            pipeline
            + """
loki.echo "fixture" {}
loki.source.api "fixture" {
  http {
    listen_address = "127.0.0.1"
    listen_port = 9999
  }
  forward_to = [loki.process.docker.receiver]
}
"""
        )
        pg = "2026-11-10 21:00:00.001 UTC [42] "
        cases = [
            (
                "any/service",
                '{"severity_text":"WARNING","level":"INFO","msg":"test"}',
                "warn",
            ),
            (
                "any/service",
                'time=2026-11-10T21:00:00Z level=ERROR msg="test"',
                "error",
            ),
            ("location/postgres", pg + "WARNING: connection interrupted", "warn"),
            ("standalone-fixture", pg + "LOG: checkpoint starting: time", "debug"),
            (
                "fixture-notes/postgres",
                pg + "FATAL: connection to client lost",
                "fatal",
            ),
            ("fixture-audio/postgres", pg + "ERROR: duplicate key", "error"),
            (
                "fixture-models/ollama",
                '[GIN] 2026/11/10 - 21:00:00 | 200 | 1ms | 127.0.0.1 | GET "/api/tags"',
                "debug",
            ),
            (
                "fixture-models/ollama",
                '[GIN] 2026/11/10 - 21:00:00 | 503 | 1ms | 127.0.0.1 | GET "/api/tags"',
                "error",
            ),
            (
                "fixture-models/ollama",
                '[GIN] 2026/11/10 - 21:00:00 | 404 | 1ms | 192.0.2.1 | GET "/missing"',
                "warn",
            ),
            (
                "fixture-edge/headscale",
                "2026-11-10T21:00:00Z INF Node connected",
                "info",
            ),
            (
                "fixture-edge/headscale",
                "2026-11-10T21:00:00Z ERR test failure",
                "error",
            ),
            ("fixture-edge/headscale", "2026-11-10T21:00:00Z WRN test warning", "warn"),
            ("fixture-edge/headscale", "2026-11-10T21:00:00Z DBG test debug", "debug"),
            (
                "fixture-audio/rabbitmq",
                "2026-11-10 21:00:00.001+00:00 [warning] <0.1.0> client disconnected",
                "warn",
            ),
            (
                "fixture-proxy/bridge",
                "2026/11/10 21:00:00.001 [Error] transport/internet: test",
                "error",
            ),
            (
                "fixture-runners/runner",
                "[RUNNER 2026-11-10 21:00:00Z WARN GitHubActionsService] delayed",
                "warn",
            ),
            (
                "fixture-bot/fixture-bot",
                "[Tue Nov 10 21:00:00 UTC 2026] ERROR test",
                "error",
            ),
            (
                "fixture-notes/web",
                "2026/11/10 21:00:00 traces export: context deadline exceeded",
                "error",
            ),
            (
                "fixture-agent/collector-tailnet",
                "2026/11/10 21:00:00 netcheck: UDP is blocked, trying ICMP",
                "debug",
            ),
            (
                "fixture-ui/fixture-ui-tailnet",
                "2026/11/10 21:00:00 netcheck: netcheck: UDP is blocked, trying HTTPS",
                "debug",
            ),
            (
                "fixture-app/fixture-app-tailnet",
                "2026/11/10 21:00:00 control: NetInfo: NetInfo{udp=false}",
                "debug",
            ),
            (
                "fixture-workers/worker-tailnet",
                "2026/11/10 21:00:00 magicsock: new contact: peer=[fake] via=derp",
                "debug",
            ),
            (
                "fixture-app/fixture-app-tailnet",
                "2026/11/10 21:00:00 control: authentication failed",
                "unknown",
            ),
            (
                "another/service",
                "2026/11/10 21:00:00 netcheck: UDP is blocked, trying ICMP",
                "unknown",
            ),
            (
                "fixture-agent/collector-tailnet",
                "2026/11/10 21:00:00 netcheck: DetectCaptivePortal(found=false)",
                "debug",
            ),
            (
                "fixture-ui/fixture-ui-tailnet",
                "2026/11/10 21:00:00 netcheck: DetectCaptivePortal(found=true)",
                "unknown",
            ),
            (
                "fixture-gateway/gateway",
                "1794359159.337206 notice redsocks.c:958 connected(...) Host is unreachable",
                "warn",
            ),
            (
                "fixture-gateway/gateway",
                "1794359159.337206 notice utils.c:223 connected",
                "info",
            ),
            (
                "fixture-gateway/gateway",
                "1794359159.337206 error utils.c:223 broken",
                "error",
            ),
            (
                "another/service",
                "1794359159.337206 notice utils.c:223 connected",
                "unknown",
            ),
            (
                "fixture-broker/kafka",
                "retention (604800000) (kafka.raft.KafkaMetadataLog)",
                "info",
            ),
            (
                "fixture-broker/kafka",
                "retention failed (kafka.raft.KafkaMetadataLog)",
                "unknown",
            ),
            (
                "fixture-agent/collector-tailnet",
                "2026/11/10 21:00:00 Received error: PollNetMap: connection refused",
                "warn",
            ),
            (
                "fixture-agent/collector-tailnet",
                "2026/11/10 21:00:00 [RATELIMIT] format(synthetic)",
                "warn",
            ),
            (
                "fixture-agent/collector-tailnet",
                "2026/11/10 21:00:00 magicsock: derp-1 connected; connGen=2",
                "debug",
            ),
            (
                "fixture-models/ollama",
                "slot update_slots: id  0 | task 0 | cached n_tokens = 15",
                "debug",
            ),
            (
                "fixture-models/ollama",
                "llama_model_loader: loaded meta data with 58 key-value pairs",
                "debug",
            ),
            (
                "fixture-models/ollama",
                "load_tensors: failed to allocate tensor",
                "error",
            ),
            (
                "fixture-models/ollama",
                "srv  log_server_r: done request: POST /completion 127.0.0.1 503",
                "error",
            ),
            (
                "fixture-models/ollama",
                "load: control-looking token; this is probably a bug in the model",
                "warn",
            ),
            ("fixture-models/ollama", "unfamiliar runner output", "unknown"),
            ("any/service", '{"level":99,"msg":"test"}', "unknown"),
            ("any/service", "the message mentions error but has no level", "unknown"),
        ]
        streams = [
            {
                "stream": {
                    "container": "synthetic",
                    "service": service,
                    "case": str(i),
                },
                "values": [[str(time.time_ns()), line]],
            }
            for i, (service, line, _) in enumerate(cases)
        ]
        self.wait_for(lambda: self.request("http://127.0.0.1:9999/ready") or True)
        self.request("http://127.0.0.1:9999/loki/api/v1/push", {"streams": streams})

        def entries():
            output = {}
            for line in self.docker("logs", self.name).splitlines():
                if "received log entry" not in line:
                    continue
                fields = dict(item.split("=", 1) for item in shlex.split(line))
                case = re.search(r'case="([0-9]+)"', fields["labels"]).group(1)
                output[int(case)] = fields
            return output if len(output) == len(cases) else None

        output = self.wait_for(entries)
        for i, (_, body, severity) in enumerate(cases):
            with self.subTest(case=i):
                self.assertIn('level="' + severity + '"', output[i]["labels"])
                self.assertEqual(
                    json.loads(output[i]["structured_metadata"])["detected_level"],
                    severity,
                )
                self.assertEqual(output[i]["entry"], body)

    def textfile_probe(self, watchdog=False, override=None):
        config = """prometheus.exporter.unix "fixture" {
  set_collectors = ["textfile"]
  textfile { directory = sys.env("ALLOY_TEXTFILE_DIRECTORY") }
}
"""
        env = [] if override is None else ["-e", "ALLOY_TEXTFILE_DIRECTORY=" + override]
        self.start(config, *env, watchdog=watchdog)
        return self.wait_for(
            lambda: self.request(
                "http://127.0.0.1:12345/api/v0/component/prometheus.exporter.unix.fixture/metrics"
            )
        )

    def test_optional_watchdog_absent_does_not_generate_errors(self):
        self.assertIn("node_textfile_scrape_error 0", self.textfile_probe())
        self.assertNotIn(
            "failed to read textfile collector directory",
            self.docker("logs", self.name),
        )

    def test_existing_watchdog_is_still_collected(self):
        self.assertIn(
            "synthetic_watchdog_success 1", self.textfile_probe(watchdog=True)
        )

    def test_explicit_missing_path_remains_an_error(self):
        self.assertIn(
            "node_textfile_scrape_error 1",
            self.textfile_probe(override="/missing-explicit-path"),
        )
