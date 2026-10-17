"""First-switch safety: never discard the old writer's pending queue."""

import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("queue_migration", Path(__file__).parents[1] / "scripts/migrate-queue.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class QueueMigration(unittest.TestCase):
    def info(self, url="http://central/api/v1/write", running=True):
        return json.dumps([{"Config": {"Env": ["PROMETHEUS_REMOTE_WRITE_URL=" + url]},
                            "State": {"Running": running}}])

    def test_waits_for_pending_samples_to_drain(self):
        with patch.object(migration.subprocess, "check_output", side_effect=[
            "container\n", self.info(),
            'prometheus_remote_storage_samples_pending{remote_name="central"} 100\n',
            'prometheus_remote_storage_samples_pending{remote_name="central"} 0\n',
        ]), patch.object(migration.time, "sleep") as sleep:
            migration.main()
            sleep.assert_called_once_with(2)

    def test_refuses_stopped_direct_writer(self):
        with patch.object(migration.subprocess, "check_output", side_effect=["container\n", self.info(running=False)]):
            with self.assertRaises(SystemExit):
                migration.main()

    def test_local_queue_does_not_need_to_be_drained_for_restart(self):
        with patch.object(migration.subprocess, "check_output", side_effect=[
            "container\n", self.info("http://vmagent:8429/api/v1/write"),
        ]) as docker:
            migration.main()
            self.assertEqual(docker.call_count, 2)

    def test_refuses_to_switch_if_queue_does_not_drain(self):
        pending = 'prometheus_remote_storage_samples_pending{remote_name="central"} 100\n'
        with patch.object(migration.subprocess, "check_output", side_effect=["container\n", self.info()] + [pending] * 60), patch.object(migration.time, "sleep"):
            with self.assertRaises(SystemExit):
                migration.main()
