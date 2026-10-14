"""Real Docker checks using only isolated containers and volumes."""

import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
import uuid


spec = importlib.util.spec_from_file_location(
    "migration", Path(__file__).parents[1] / "scripts/migrate-state.py"
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class StateMigration(unittest.TestCase):
    def setUp(self):
        self.docker = shlex.split(os.environ.get("DOCKER", "docker"))
        self.image = "grafana/alloy:" + os.environ["ALLOY_VERSION"]
        self.name = "alloy-migration-test-" + uuid.uuid4().hex[:12]
        self.volume = self.name + "-state"
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup)
        self.run_docker("volume", "create", self.volume)
        self.run_docker(
            "run", "-d", "--name", self.name, "--network", "none",
            "--entrypoint", "sh", self.image, "-c",
            "mkdir -p /data-alloy/loki.source.docker.containers; "
            "printf 'synthetic-position: 12345\\n' > "
            "/data-alloy/loki.source.docker.containers/positions.yml; sleep 300",
        )
        self.run_docker("exec", self.name, "test", "-s",
                        "/data-alloy/loki.source.docker.containers/positions.yml")

    def cleanup(self):
        subprocess.run(self.docker + ["rm", "-f", self.name], capture_output=True)
        subprocess.run(self.docker + ["volume", "rm", self.volume], capture_output=True)
        self.tmp.cleanup()

    def run_docker(self, *args):
        return subprocess.check_output(self.docker + list(args))

    def test_preserves_positions_and_keeps_backup(self):
        backup = migration.migrate(self.name, self.volume, self.tmp.name)
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        data = self.run_docker(
            "run", "--rm", "--network", "none", "--entrypoint", "cat",
            "-v", f"{self.volume}:/state:ro", self.image,
            "/state/loki.source.docker.containers/positions.yml",
        )
        self.assertEqual(data, b"synthetic-position: 12345\n")
        state = json.loads(self.run_docker("inspect", self.name))[0]["State"]
        self.assertFalse(state["Running"])

    def test_refuses_to_overwrite_existing_state(self):
        self.run_docker(
            "run", "--rm", "--network", "none", "--entrypoint", "sh",
            "-v", f"{self.volume}:/state", self.image,
            "-c", "echo keep-me > /state/existing",
        )
        with self.assertRaises(subprocess.CalledProcessError):
            migration.migrate(self.name, self.volume, self.tmp.name)
        state = json.loads(self.run_docker("inspect", self.name))[0]["State"]
        self.assertTrue(state["Running"])
